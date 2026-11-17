"""MCP Streamable HTTP primitives; network I/O is exclusively caller-injected.

This is not the Norma permission adapter or a production HTTP implementation.
See PORTING.md for integration/security boundaries.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from threading import RLock
from typing import Any, Mapping, Protocol
from urllib.parse import urljoin, urlsplit

PROTOCOL_VERSION = '2025-06-18'
LEGACY_SSE_PROTOCOL_VERSION = '2024-11-05'
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def tool_name(server: str, name: str) -> str:
    return f'mcp__{server}__{name}'


class MCPError(Exception):
    pass


class RPCError(MCPError):
    def __init__(self, code: int, message: str):
        self.code, self.message = code, message
        super().__init__(f'mcp rpc error {code}: {message}')


class ToolError(MCPError):
    def __init__(self, tool: str, text: str):
        self.text = text
        super().__init__(f'mcp tool {tool!r} error: {text}')


def normalize_headers(headers: Mapping[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in headers.items():
        clean = key.strip()
        if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", clean):
            raise ValueError('invalid HTTP header name')
        if not isinstance(value, str) or any(c in value for c in '\r\n\x00'):
            raise ValueError('invalid HTTP header value')
        if clean.lower() in out:
            raise ValueError('duplicate HTTP header name')
        out[clean.lower()] = value
    return out


@dataclass(frozen=True)
class HTTPRequest:
    method: str
    url: str
    headers: Mapping[str, str] = field(repr=False)
    body: bytes = b''


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes = b''


class Transport(Protocol):
    def __call__(self, request: HTTPRequest) -> HTTPResponse: ...


def _reject_json_constant(value: str) -> Any:
    raise ValueError('non-JSON numeric constant')


def _decode(raw: bytes | str) -> dict[str, Any]:
    try:
        value = json.loads(raw, parse_constant=_reject_json_constant)
    except (ValueError, UnicodeDecodeError) as exc:
        raise MCPError('mcp: invalid JSON response') from exc
    if not isinstance(value, dict):
        raise MCPError('mcp: response must be an object')
    return value


def parse_sse(body: bytes, want_id: int) -> dict[str, Any]:
    """Parse buffered SSE data frames, skipping requests/notifications/other IDs."""
    if len(body) > MAX_RESPONSE_BYTES:
        raise MCPError('mcp: response exceeds byte limit')
    try:
        # SSE only recognizes CR/LF, not the Unicode separators that
        # str.splitlines() would split inside otherwise valid JSON strings.
        lines = re.split(r'\r\n|\r|\n', body.decode('utf-8-sig'))
    except UnicodeDecodeError as exc:
        raise MCPError('mcp: invalid UTF-8 event stream') from exc
    data: list[str] = []
    for line in lines + ['']:
        if line == '':
            if data:
                try:
                    response = _decode('\n'.join(data))
                except MCPError:
                    response = {}
                data = []
                if ('method' not in response and type(response.get('id')) is int
                        and response['id'] == want_id):
                    return response
        else:
            name, _, value = line.partition(':')
            if name == 'data':
                data.append(value[1:] if value.startswith(' ') else value)
    raise MCPError('mcp: no matching response in event stream')


def resolve_sse_endpoint(base: str, candidate: str) -> str:
    """Resolve a legacy announcement without allowing cross-origin credential routing."""
    target = urljoin(base, candidate.strip())
    a, b = urlsplit(base), urlsplit(target)
    def origin(u):
        return u.scheme.lower(), u.hostname, u.port or (443 if u.scheme == 'https' else 80)
    if b.scheme not in ('http', 'https') or not b.hostname or b.username or b.password or b.fragment:
        raise ValueError('invalid MCP endpoint')
    if origin(a) != origin(b):
        raise ValueError('cross-origin MCP endpoint rejected')
    return target


@dataclass(frozen=True)
class RemoteTool:
    name: str
    description: str = ''
    input_schema: Mapping[str, Any] = field(default_factory=lambda: {'type': 'object'})

    def permission_prompt(self, server: str) -> str:
        return f'call MCP tool {tool_name(server, self.name)}?'


class Client:
    def __init__(self, server: str, url: str, *, transport: Transport,
                 headers: Mapping[str, str] | None = None):
        resolve_sse_endpoint(url, url)
        self.server, self.url, self.transport = server, url, transport
        self.headers = normalize_headers(headers or {})
        if set(self.headers) & {'content-type', 'accept', 'mcp-protocol-version', 'mcp-session-id'}:
            raise ValueError('protocol headers are reserved')
        self.session_id = ''
        self._next_id = 0
        self._closed = False
        self._lock = RLock()

    def initialize(self) -> Any:
        result = self.rpc('initialize', {'protocolVersion': PROTOCOL_VERSION,
            'capabilities': {}, 'clientInfo': {'name': 'artex', 'version': '0.2'}})
        self.notify('notifications/initialized', {})
        return result

    def _exchange(self, frame: dict[str, Any], expect: bool) -> Any:
        if self._closed:
            raise MCPError('mcp: client closed')
        headers = {**self.headers, 'content-type': 'application/json',
            'accept': 'application/json, text/event-stream', 'mcp-protocol-version': PROTOCOL_VERSION}
        if self.session_id:
            headers['mcp-session-id'] = self.session_id
        response = self.transport(HTTPRequest('POST', self.url, headers,
            json.dumps(frame, allow_nan=False, separators=(',', ':')).encode()))
        if not 200 <= response.status < 300:
            # Do not echo potentially credential-bearing server bodies.
            raise MCPError(f'mcp http {response.status}')
        if len(response.body) > MAX_RESPONSE_BYTES:
            raise MCPError('mcp: response exceeds byte limit')
        received = normalize_headers(response.headers)
        if not expect:
            return None
        if received.get('content-type', '').lower().startswith('text/event-stream'):
            reply = parse_sse(response.body, frame['id'])
        else:
            reply = _decode(response.body)
        if reply.get('jsonrpc') != '2.0' or type(reply.get('id')) is not int or reply['id'] != frame['id']:
            raise MCPError('mcp: invalid version or mismatched response id')
        if 'method' in reply:
            raise MCPError('mcp: expected response, received request')
        if ('result' in reply) == ('error' in reply):
            raise MCPError('mcp: expected exactly one of result or error')
        if 'error' in reply:
            error = reply['error']
            if not isinstance(error, dict) or type(error.get('code')) is not int or not isinstance(error.get('message'), str):
                raise MCPError('mcp: malformed RPC error')
            raise RPCError(error['code'], error['message'])
        # Only a successful initialize response establishes a session. Keep it
        # before the initialized notification so that notification carries it.
        if frame['method'] == 'initialize' and not self.session_id:
            self.session_id = received.get('mcp-session-id', '')
        return reply['result']

    def rpc(self, method: str, params: Any) -> Any:
        with self._lock:
            self._next_id += 1
            return self._exchange({'jsonrpc': '2.0', 'id': self._next_id, 'method': method, 'params': params}, True)

    def notify(self, method: str, params: Any) -> None:
        with self._lock:
            self._exchange({'jsonrpc': '2.0', 'method': method, 'params': params}, False)

    def tools(self) -> list[RemoteTool]:
        result = self.rpc('tools/list', {})
        if not isinstance(result, dict) or not isinstance(result.get('tools'), list):
            raise MCPError('mcp: malformed tool list')
        tools = []
        for item in result['tools']:
            if not isinstance(item, dict) or not isinstance(item.get('name'), str):
                raise MCPError('mcp: malformed tool')
            schema = item.get('inputSchema')
            description = item.get('description', '')
            if (schema is not None and not isinstance(schema, dict)) or not isinstance(description, str):
                raise MCPError('mcp: malformed tool metadata')
            tools.append(RemoteTool(item['name'], description, schema if schema is not None else {'type': 'object'}))
        return tools

    def call(self, tool: str, args: Any) -> str:
        """Low-level backend call; caller MUST enforce authorization before invoking."""
        result = self.rpc('tools/call', {'name': tool, 'arguments': args})
        if not isinstance(result, dict) or not isinstance(result.get('content'), list):
            raise MCPError('mcp: malformed tool response')
        chunks = []
        for block in result['content']:
            if not isinstance(block, dict) or not isinstance(block.get('text', ''), str):
                raise MCPError('mcp: malformed content block')
            chunks.append(block.get('text', ''))
        text = ''.join(chunks)
        if result.get('isError'):
            raise ToolError(tool, text)
        return text

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self.session_id:
                try:
                    self.transport(HTTPRequest('DELETE', self.url,
                        {**self.headers, 'mcp-session-id': self.session_id}))
                except Exception:
                    pass  # Matches Go's best-effort cleanup; never retry a tool call.

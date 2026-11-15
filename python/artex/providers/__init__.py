"""Vendor serialization adapters with mandatory, injected HTTP transport.

No default network transport, credential discovery, tool execution, or retries.
See README.md for the explicit boundary versus pinned Norma v0.4.3.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field, is_dataclass
import json
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlsplit


class ProtocolError(ValueError):
    """Malformed request/response; do not treat as transient HTTP failure."""


class HTTPError(RuntimeError):
    def __init__(self, status: int):
        self.status = status
        # Never expose response bodies or auth headers through exception text.
        super().__init__(f"provider HTTP status {status}")


class ProviderError(RuntimeError):
    """Structured provider error returned in an otherwise successful response."""


class IncompleteStreamError(ConnectionError):
    """EOF before the protocol terminal event; any emitted events forbid replay."""


@dataclass(frozen=True)
class Config:
    format: str
    model: str
    base_url: str = ""
    api_key: str = field(default="", repr=False)
    api_version: str = "2023-06-01"
    thinking_type: str = ""
    reasoning_effort: str = ""
    max_tokens_field: str = ""


@dataclass
class Response:
    status: int
    body: bytes | str | Iterable[bytes]
    close: Callable[[], None] = lambda: None


def _mapping(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return deepcopy(dict(value))
    raise ProtocolError("Expected a mapping or dataclass")


def _cancel(context):
    err = getattr(context, 'err', None)
    error = err() if callable(err) else None
    if error is not None:
        if isinstance(error, BaseException):
            raise error
        raise RuntimeError("context canceled")
    if isinstance(context, Mapping) and context.get('cancelled'):
        raise RuntimeError("context canceled")


def _json(value):
    try:
        result = json.loads(value) if isinstance(value, (str, bytes, bytearray)) else deepcopy(value)
    except (ValueError, UnicodeError) as exc:
        raise ProtocolError("Invalid JSON") from exc
    if not isinstance(result, dict):
        raise ProtocolError("Expected a JSON object")
    return result


def _input(value):
    return _json(value if value not in (None, '', b'') else {})


def _text(blocks):
    return ''.join(b.get('text', '') for b in blocks if b.get('type') == 'text')


def _messages(request):
    messages = [_mapping(m) for m in request.get('messages', [])]
    pending, seen = set(), set()
    for message in messages:
        role, blocks = message.get('role'), message.get('content', [])
        if role not in ('user', 'assistant') or not isinstance(blocks, list):
            raise ProtocolError("Only user/assistant content-block messages are supported")
        message['content'] = blocks = [_mapping(b) for b in blocks]
        if pending and role != 'user':
            raise ProtocolError("Tool calls require an immediately following user result message")
        results = set()
        for b in blocks:
            kind = b.get('type')
            if kind not in ('text', 'thinking', 'tool_use', 'tool_result'):
                raise ProtocolError(f"Unsupported content block: {kind}")
            if kind == 'tool_use':
                key = b.get('id')
                if role != 'assistant' or not key or key in seen or not b.get('name'):
                    raise ProtocolError("Invalid or duplicate tool call")
                seen.add(key)
                pending.add(key)
                b['input'] = _input(b.get('input'))
            elif kind == 'tool_result':
                key = b.get('tool_use_id')
                if role != 'user' or key not in pending or key in results:
                    raise ProtocolError("Orphan or duplicate tool result")
                results.add(key)
                b['content'] = [_mapping(c) for c in b.get('content', [])]
                if any(c.get('type') != 'text' for c in b['content']):
                    raise ProtocolError("Only text tool results are supported")
        if role == 'user' and pending:
            if results != pending:
                raise ProtocolError("Every pending tool call must have a result")
            pending.clear()
    if pending:
        raise ProtocolError("Unanswered tool calls in history")
    return messages


def _usage(value=None, anthropic=False):
    value = value or {}
    if anthropic:
        read, write = value.get('cache_read_input_tokens', 0), value.get('cache_creation_input_tokens', 0)
        return dict(input_tokens=value.get('input_tokens', 0) + read + write,
                    output_tokens=value.get('output_tokens', 0), cache_read_tokens=read, cache_write_tokens=write)
    return dict(input_tokens=value.get('prompt_tokens', 0), output_tokens=value.get('completion_tokens', 0),
                cache_read_tokens=(value.get('prompt_tokens_details') or {}).get('cached_tokens', 0), cache_write_tokens=0)


def _stop(value):
    return {'stop': 'end_turn', 'length': 'max_tokens', 'tool_calls': 'tool_use'}.get(value, value or '')


def _join(system):
    return '\n\n'.join(s.strip() for s in system if s.strip())


def serialize(config: Config, request, *, stream=False):
    request = _mapping(request)
    messages = _messages(request)
    thinking = request.get('thinking') or config.thinking_type
    body = {'model': config.model, 'stream': stream}
    if request.get('temperature') is not None:
        body['temperature'] = request['temperature']
    tools = [_mapping(t) for t in request.get('tools', [])]
    if thinking:
        if thinking not in ('enabled', 'disabled'):
            raise ProtocolError("Unsupported thinking type")
        body['thinking'] = {'type': thinking}
    if config.format == 'openai':
        out = []
        if system := _join(request.get('system', [])):
            out.append({'role': 'system', 'content': system})
        for m in messages:
            blocks = m['content']
            text = _text(blocks)
            if m['role'] == 'assistant':
                row = {'role': 'assistant'}
                if text:
                    row['content'] = text
                reasoning = ''.join(b.get('thinking', '') for b in blocks if b['type'] == 'thinking')
                if reasoning:
                    row['reasoning_content'] = reasoning
                calls = [{'id': b['id'], 'type': 'function', 'function': {'name': b['name'], 'arguments': json.dumps(b['input'], ensure_ascii=False, separators=(',', ':'))}}
                         for b in blocks if b['type'] == 'tool_use']
                if calls:
                    row['tool_calls'] = calls
                if not text and not calls:
                    row['content'] = '…'
                out.append(row)
            else:
                out.extend({'role': 'tool', 'tool_call_id': b['tool_use_id'], 'content': _text(b['content'])}
                           for b in blocks if b['type'] == 'tool_result')
                if text:
                    out.append({'role': 'user', 'content': text})
        body['messages'] = out
        if tools:
            body['tools'] = [{'type': 'function', 'function': {'name': t['name'], 'description': t.get('description', ''), 'parameters': t.get('input_schema', {})}} for t in tools]
        if request.get('max_tokens', 0):
            key = 'max_completion_tokens' if config.max_tokens_field == 'max_completion_tokens' else 'max_tokens'
            body[key] = request['max_tokens']
        if request.get('stop'):
            body['stop'] = request['stop']
        if config.reasoning_effort:
            body['reasoning_effort'] = config.reasoning_effort
        if stream:
            body['stream_options'] = {'include_usage': True}
    elif config.format == 'anthropic':
        out = []
        for m in messages:
            blocks = [b for b in m['content'] if b['type'] != 'thinking' or thinking == 'enabled']
            if any(b['type'] == 'thinking' and not b.get('signature') for b in blocks):
                raise ProtocolError("Anthropic replayed thinking requires its original signature")
            if not blocks:
                continue
            if out and out[-1]['role'] == m['role']:
                out[-1]['content'].extend(blocks)
            else:
                out.append({'role': m['role'], 'content': blocks})
        body.update(messages=out, max_tokens=request.get('max_tokens') or 8192)
        system = request.get('system', [])
        boundary = request.get('dynamic_boundary', 0)
        if system:
            if boundary <= 0:
                body['system'] = [{'type': 'text', 'text': _join(system)}]
            else:
                body['system'] = [{'type': 'text', 'text': _join(system[:boundary]), 'cache_control': {'type': 'ephemeral'}}]
                if boundary < len(system):
                    body['system'].append({'type': 'text', 'text': _join(system[boundary:])})
        if tools:
            body['tools'] = tools
        if request.get('stop'):
            body['stop_sequences'] = request['stop']
        if config.reasoning_effort:
            body['output_config'] = {'effort': config.reasoning_effort}
    else:
        raise ProtocolError("Supported formats: openai, anthropic; Responses API is not implemented")
    return body


def _provider_error(data):
    if data.get('error') is not None or data.get('type') == 'error':
        # Error categories remain available without echoing potentially sensitive bodies.
        error = data.get('error') or {}
        kind = error.get('type', 'provider_error') if isinstance(error, dict) else 'provider_error'
        raise ProviderError(f"Provider returned {kind}")


def parse_response(config, payload):
    data = _json(payload)
    _provider_error(data)
    if config.format == 'anthropic':
        if not isinstance(data.get('content'), list):
            raise ProtocolError("Anthropic response is missing content")
        blocks = []
        for item in data['content']:
            b = _mapping(item)
            if b.get('type') == 'tool_use':
                b['input'] = _input(b.get('input'))
            if b.get('type') not in ('text', 'thinking', 'tool_use'):
                raise ProtocolError("Unsupported Anthropic response content")
            blocks.append(b)
        stop, usage = data.get('stop_reason', ''), _usage(data.get('usage'), True)
    else:
        if not data.get('choices'):
            raise ProtocolError("OpenAI returned no choices")
        choice = data['choices'][0]
        message = choice.get('message') or {}
        blocks = []
        reasoning = next((message[k] for k in ('reasoning_content', 'reasoning', 'reasoning_text') if message.get(k)), '')
        if reasoning:
            blocks.append({'type': 'thinking', 'thinking': reasoning})
        text = message.get('content') or message.get('refusal')
        if text:
            if not isinstance(text, str):
                raise ProtocolError("Only textual OpenAI response content is supported")
            blocks.append({'type': 'text', 'text': text})
        for call in message.get('tool_calls') or []:
            function = call['function']
            blocks.append({'type': 'tool_use', 'id': call['id'], 'name': function['name'], 'input': _input(function.get('arguments'))})
        refusal = message.get('refusal')
        if refusal is not None and not isinstance(refusal, str):
            raise ProtocolError('Invalid OpenAI refusal field')
        stop = 'refusal' if refusal else _stop(choice.get('finish_reason'))
        usage = _usage(data.get('usage'))
    result = {'role': 'assistant', 'content': blocks, 'model': data.get('model', config.model)}
    if config.format == 'openai' and message.get('refusal'):
        result['refusal'] = message['refusal']
    return result, stop, usage


def _frames(body, context):
    """Parse UTF-8 SSE data events across arbitrary byte boundaries; bounded frames."""
    chunks = [body] if isinstance(body, (bytes, str)) else body
    buffer = b''
    data = []
    size = 0
    for chunk in chunks:
        _cancel(context)
        buffer += chunk.encode('utf-8') if isinstance(chunk, str) else chunk
        if len(buffer) > 8 * 1024 * 1024:
            raise ProtocolError("SSE line exceeds 8 MiB")
        while b'\n' in buffer:
            line, buffer = buffer.split(b'\n', 1)
            line = line.rstrip(b'\r')
            if not line:
                if data:
                    try:
                        yield b'\n'.join(data).decode('utf-8')
                    except UnicodeError as exc:
                        raise ProtocolError("Invalid SSE UTF-8") from exc
                data, size = [], 0
            elif line.startswith(b'data:'):
                value = line[5:].removeprefix(b' ')
                size += len(value)
                if size > 8 * 1024 * 1024:
                    raise ProtocolError("SSE event exceeds 8 MiB")
                data.append(value)
    if buffer.strip() or data:
        raise IncompleteStreamError("Truncated SSE event")


class Provider:
    """Transport callable: (context, url, headers, JSON bytes, stream) -> Response.

    It owns network policy, timeouts, proxies and any connection retry policy.
    It must never replay a response after handing any bytes to this adapter.
    """
    def __init__(self, config: Config, transport: Callable):
        if config.format not in ('openai', 'anthropic'):
            raise ProtocolError("Unsupported provider format")
        self.config, self.transport = config, transport
        base = config.base_url or ('https://api.openai.com/v1' if config.format == 'openai' else 'https://api.anthropic.com')
        parsed = urlsplit(base)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ProtocolError("Base URL must be an HTTP(S) origin/path without credentials, query or fragment")
        self.url = base.rstrip('/') + ('/chat/completions' if config.format == 'openai' else '/v1/messages')

    def _send(self, context, request, stream):
        _cancel(context)
        body = json.dumps(serialize(self.config, request, stream=stream), ensure_ascii=False).encode('utf-8')
        headers = {'content-type': 'application/json', 'accept': 'text/event-stream' if stream else 'application/json'}
        if self.config.format == 'openai':
            headers['authorization'] = 'Bearer ' + self.config.api_key
        else:
            headers.update({'x-api-key': self.config.api_key, 'anthropic-version': self.config.api_version})
        if any('\r' in v or '\n' in v for v in headers.values()):
            raise ProtocolError("Invalid header value")
        response = self.transport(context, self.url, headers, body, stream)
        if not 200 <= response.status < 300:
            response.close()
            raise HTTPError(response.status)
        return response

    def complete(self, context, request):
        response = self._send(context, request, False)
        try:
            _cancel(context)
            if not isinstance(response.body, (str, bytes)):
                raise ProtocolError("Non-stream response body must be bytes or text")
            return parse_response(self.config, response.body)
        finally:
            response.close()

    def stream(self, context, request):
        response = self._send(context, request, True)
        try:
            if self.config.format == 'anthropic':
                yield from _anthropic_events(_frames(response.body, context), self.config.model)
            else:
                yield from _openai_events(_frames(response.body, context), self.config.model)
        finally:
            response.close()


def _anthropic_events(frames, model):
    started, stopped = False, False
    blocks = {}
    for raw in frames:
        data = _json(raw)
        _provider_error(data)
        kind = data.get('type')
        if kind == 'ping':
            continue
        if kind == 'message_start':
            if started:
                raise ProtocolError("Duplicate message_start")
            started = True
            message = data.get('message') or {}
            usage = _usage(message.get('usage'), True)
            usage['output_tokens'] = 0
            yield {'type': 'message_start', 'model': message.get('model', model), 'usage': usage}
        elif kind == 'content_block_start':
            block = data.get('content_block') or {}
            index = data.get('index', 0)
            blocks[index] = block
            if block.get('type') == 'tool_use':
                yield {'type': 'tool_use_start', 'index': index, 'tool_id': block.get('id', ''), 'tool_name': block.get('name', '')}
        elif kind == 'content_block_delta':
            delta = data.get('delta') or {}
            index = data.get('index', 0)
            mapping = {'text_delta': ('text_delta', 'text'), 'thinking_delta': ('thinking_delta', 'thinking'),
                       'signature_delta': ('thinking_signature', 'signature'), 'input_json_delta': ('tool_input_delta', 'partial_json')}
            if delta.get('type') in mapping:
                event, key = mapping[delta['type']]
                yield {'type': event, 'index': index, 'tool_id': blocks.get(index, {}).get('id', ''), 'text': delta.get(key, '')}
        elif kind == 'message_delta':
            usage = _usage()
            usage['output_tokens'] = (data.get('usage') or {}).get('output_tokens', 0)
            yield {'type': 'message_delta', 'stop_reason': (data.get('delta') or {}).get('stop_reason', ''), 'usage': usage}
        elif kind == 'message_stop':
            stopped = True
            yield {'type': 'message_stop'}
            break
    if not started or not stopped:
        raise IncompleteStreamError("Anthropic stream ended before message_stop")


def _openai_events(frames, model):
    started, stopped = False, False
    calls = {}
    usage, reason = _usage(), ''
    refused = False
    for raw in frames:
        if raw == '[DONE]':
            stopped = True
            break
        data = _json(raw)
        _provider_error(data)
        if not started:
            started = True
            yield {'type': 'message_start', 'model': data.get('model', model), 'usage': _usage()}
        if data.get('usage') is not None:
            usage = _usage(data['usage'])
        choices = data.get('choices') or []
        if not choices:
            continue
        choice = choices[0]
        delta = choice.get('delta') or {}
        if delta.get('refusal'):
            if not isinstance(delta['refusal'], str):
                raise ProtocolError('Invalid streamed refusal field')
            refused = True
        if choice.get('finish_reason'):
            reason = _stop(choice['finish_reason'])
        thinking = next((delta[k] for k in ('reasoning_content', 'reasoning', 'reasoning_text') if delta.get(k)), '')
        if thinking:
            yield {'type': 'thinking_delta', 'text': thinking}
        if text := delta.get('content') or delta.get('refusal'):
            yield {'type': 'text_delta', 'text': text}
        for call in delta.get('tool_calls') or []:
            index = call.get('index', 0)
            state = calls.setdefault(index, {'id': '', 'name': '', 'pending': [], 'started': False})
            function = call.get('function') or {}
            if call.get('id'):
                state['id'] = call['id']
            if function.get('name'):
                if state['started'] and function['name'] != state['name']:
                    raise ProtocolError('Fragmented tool names after tool_use_start are unsupported')
                if not state['started']:
                    state['name'] += function['name']
            if function.get('arguments'):
                state['pending'].append(function['arguments'])
            if not state['started'] and state['id'] and state['name']:
                state['started'] = True
                yield {'type': 'tool_use_start', 'index': index, 'tool_id': state['id'], 'tool_name': state['name']}
            if state['started']:
                for fragment in state['pending']:
                    yield {'type': 'tool_input_delta', 'index': index, 'tool_id': state['id'], 'text': fragment}
                state['pending'].clear()
    if not started or not stopped:
        raise IncompleteStreamError("OpenAI stream ended before DONE")
    if any(not c['started'] for c in calls.values()):
        raise ProtocolError("Incomplete streamed tool-call identity")
    yield {'type': 'message_delta', 'stop_reason': 'refusal' if refused else reason, 'usage': usage}
    yield {'type': 'message_stop'}


class ContextBoundProvider:
    """Adapt complete(request) callers (sidequestion) without changing pool API.

    A context factory is invoked for each call so cancellation/session metadata
    is not accidentally reused across independent requests.
    """
    def __init__(self, provider, context_factory=lambda: None):
        self.provider, self.context_factory = provider, context_factory

    def complete(self, request):
        return self.provider.complete(self.context_factory(), request)

    def stream(self, request):
        return self.provider.stream(self.context_factory(), request)

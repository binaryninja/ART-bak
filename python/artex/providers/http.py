"""Opt-in stdlib HTTP transport for providers.Provider; never instantiated here.

Only explicitly allowlisted origins receive POSTs or credentials. HTTPS uses
certificate and hostname verification. Plain HTTP requires BOTH an explicitly
listed literal loopback-IP origin and allow_loopback_http=True. Redirects are
never followed. http.client connects directly, ignoring environment proxy vars.
No credential discovery, retries, downloads, tool execution or implicit wiring.

Response bodies are byte-bounded; compressed responses are rejected, not expanded.
Connections are single-request and closed after EOF, error or explicit close.
Streaming consumers MUST close the returned Response when abandoning iteration.

Timeouts/cancellation are cooperative at request, header and read boundaries.
Socket timeouts bound individual blocking I/O, NOT a hard real-time deadline:
DNS resolution, slowly arriving headers and platform TLS/HTTP internals may take
longer. No worker threads or forced socket-thread cancellation are created. Host
providers/tools must still honor their own runtime cancellation contracts.

The configured hostname is the trust boundary for HTTPS: DNS/IP pinning and
enterprise egress policy are not implemented. DNS changes cannot change the
allowlisted Host/SNI name; TLS verification remains mandatory. Request/response
payloads and authentication headers are never included in raised error text.
Raw llmrec capture and proxy support remain unimplemented.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import http.client
import ipaddress
import math
import re
import socket
import ssl
from threading import Lock
import time
from urllib.parse import urlsplit

from . import Response


class TransportPolicyError(ValueError):
    pass


class TransportProtocolError(ValueError):
    pass


class TransportError(ConnectionError):
    pass


class TransportTimeout(TimeoutError):
    pass


class TransportCancelled(RuntimeError):
    pass


_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")
_CONTROL = re.compile(r'[\x00-\x20\x7f]')


@dataclass(frozen=True)
class Origin:
    scheme: str
    host: str
    port: int


def _url(value, *, origin_only=False):
    if not isinstance(value, str) or _CONTROL.search(value) or '\\' in value:
        raise TransportPolicyError('Invalid provider URL')
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in ('https', 'http') or not parsed.hostname or
                parsed.username is not None or parsed.password is not None or
                parsed.fragment or '%' in parsed.hostname):
            raise ValueError()
        host = parsed.hostname.encode('idna').decode('ascii').lower()
        if host.endswith('.'):
            raise ValueError()
        port = parsed.port
        if port is None:
            port = 443 if parsed.scheme == 'https' else 80
        if not 0 < port < 65536:
            raise ValueError()
        if origin_only and (parsed.path not in ('', '/') or parsed.query):
            raise ValueError()
        # Request targets are ASCII URIs; require callers to percent-encode Unicode.
        target = (parsed.path or '/') + ('?' + parsed.query if parsed.query else '')
        target.encode('ascii')
        if not target.startswith('/') or target.startswith('//'):
            raise ValueError()
    except (ValueError, UnicodeError):
        raise TransportPolicyError('Invalid provider URL or origin') from None
    return Origin(parsed.scheme, host, port), target


def _cancel(context):
    checks = [context]
    if isinstance(context, Mapping):
        if context.get('cancelled') is True:
            raise TransportCancelled('context canceled')
        nested = context.get('runtime_context')
        if nested is not None and nested is not context:
            checks.append(nested)
    for item in checks:
        try:
            check = getattr(item, 'check', None)
            if callable(check):
                check()
            err = getattr(item, 'err', None)
            if callable(err) and err() is not None:
                raise TransportCancelled('context canceled')
        except Exception:
            raise TransportCancelled('context canceled') from None


class _Resources:
    def __init__(self, connection, response):
        self.connection, self.response = connection, response
        self.closed = False
        self._lock = Lock()

    def close(self):
        with self._lock:
            if self.closed:
                return
            self.closed = True
            try:
                self.response.close()
            except Exception:
                pass
            finally:
                try:
                    self.connection.close()
                except Exception:
                    pass


class _Body:
    def __init__(self, resources, context, transport, deadline, length):
        self.resources, self.context, self.transport = resources, context, transport
        self.deadline, self.expected_length, self.total = deadline, length, 0

    def __iter__(self):
        return self

    def __next__(self):
        if self.resources.closed:
            raise StopIteration
        try:
            self.transport._boundary(self.context, self.deadline, self.resources.connection)
            remaining = self.transport.max_response_bytes - self.total
            # One extra byte detects an unannounced over-limit body.
            chunk = self.resources.response.read1(min(self.transport.chunk_size, remaining + 1))
            self.transport._boundary(self.context, self.deadline, self.resources.connection)
            if not isinstance(chunk, bytes):
                raise TransportProtocolError('Provider response was not bytes')
            if not chunk:
                if self.expected_length is not None and self.total != self.expected_length:
                    raise TransportProtocolError('Truncated provider response')
                self.resources.close()
                raise StopIteration
            self.total += len(chunk)
            if self.total > self.transport.max_response_bytes:
                raise TransportProtocolError('Provider response exceeds byte limit')
            if self.expected_length is not None and self.total > self.expected_length:
                raise TransportProtocolError('Provider response exceeds declared length')
            return chunk
        except StopIteration:
            raise
        except (TransportCancelled, TransportPolicyError, TransportProtocolError, TransportTimeout):
            self.resources.close()
            raise
        except (TimeoutError, socket.timeout):
            self.resources.close()
            raise TransportTimeout('Provider read timed out') from None
        except Exception:
            self.resources.close()
            raise TransportError('Provider response read failed') from None

    def close(self):
        self.resources.close()


class HTTPTransport:
    """Explicit origin allowlist; defaults create verified, direct connections.

    Call signature matches providers.Provider: (context, url, headers, body,
    stream) -> Response. Constructor does not connect or load credentials.
    Tests patch stdlib connection constructors with fakes; no test hook changes
    the production TLS/default policy.
    """
    def __init__(self, origins, *, allow_loopback_http=False, timeout=15,
                 total_timeout=120, max_request_bytes=4*1024*1024,
                 max_response_bytes=16*1024*1024, chunk_size=65536,
                 tls_context=None, clock=time.monotonic):
        if isinstance(origins, (str, bytes)):
            raise TransportPolicyError('Origins must be an explicit iterable of origins')
        for value in (timeout, total_timeout):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError('Timeouts must be positive and finite')
        for value in (max_request_bytes, max_response_bytes, chunk_size):
            if type(value) is not int or value <= 0:
                raise ValueError('Byte limits must be positive integers')
        if type(allow_loopback_http) is not bool:
            raise ValueError('Loopback HTTP opt-in must be a boolean')
        allowed = set()
        for value in origins:
            origin, _ = _url(value, origin_only=True)
            if origin.scheme == 'http':
                try:
                    loopback = ipaddress.ip_address(origin.host).is_loopback
                except ValueError:
                    loopback = False
                if not allow_loopback_http or not loopback:
                    raise TransportPolicyError('Plain HTTP requires explicit literal-loopback opt-in')
            allowed.add(origin)
        if not allowed:
            raise TransportPolicyError('At least one provider origin must be explicitly allowed')
        self.origins = frozenset(allowed)
        self.timeout, self.total_timeout = timeout, total_timeout
        self.max_request_bytes, self.max_response_bytes, self.chunk_size = max_request_bytes, max_response_bytes, chunk_size
        self.clock = clock
        self.tls_context = tls_context if tls_context is not None else ssl.create_default_context()
        self._verify_tls()

    def _verify_tls(self):
        if not isinstance(self.tls_context, ssl.SSLContext) or not self.tls_context.check_hostname or self.tls_context.verify_mode != ssl.CERT_REQUIRED:
            raise TransportPolicyError('TLS certificate and hostname verification are required')

    def _boundary(self, context, deadline, connection=None):
        _cancel(context)
        now = self.clock()
        if not isinstance(now, (int, float)) or not math.isfinite(now):
            raise TransportPolicyError('Invalid transport clock')
        remaining = deadline - now
        if remaining <= 0:
            raise TransportTimeout('Provider request deadline exceeded')
        limit = min(self.timeout, remaining)
        if connection is not None:
            connection.timeout = limit
            if connection.sock is not None:
                connection.sock.settimeout(limit)
        return limit

    def __call__(self, context, url, headers, body, stream):
        origin, target = _url(url)
        if origin not in self.origins:
            raise TransportPolicyError('Provider origin is not allowed')
        if not isinstance(body, bytes) or len(body) > self.max_request_bytes:
            raise TransportPolicyError('Request body must be bytes within the configured limit')
        if type(stream) is not bool:
            raise TransportPolicyError('Streaming mode must be a boolean')
        if not isinstance(headers, Mapping) or len(headers) > 64:
            raise TransportPolicyError('Invalid request headers')
        clean, header_bytes = {}, 0
        forbidden = {'host', 'content-length', 'transfer-encoding', 'connection', 'proxy-authorization', 'proxy-connection', 'upgrade'}
        for key, value in headers.items():
            if not isinstance(key, str) or not _TOKEN.fullmatch(key) or not isinstance(value, str):
                raise TransportPolicyError('Invalid request header')
            lower = key.lower()
            if lower in forbidden or lower in clean or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise TransportPolicyError('Unsafe or duplicate request header')
            try:
                size = len(key.encode('ascii')) + len(value.encode('latin-1'))
            except UnicodeError:
                raise TransportPolicyError('Invalid request header encoding') from None
            header_bytes += size
            if header_bytes > 16384:
                raise TransportPolicyError('Request headers exceed byte limit')
            clean[lower] = value
        if clean.get('accept-encoding', 'identity').lower() != 'identity':
            raise TransportPolicyError('Only identity response encoding is supported')
        clean['accept-encoding'] = 'identity'
        self._verify_tls()  # Reject a caller-mutated context before sending auth.
        now = self.clock()
        if not isinstance(now, (int, float)) or not math.isfinite(now):
            raise TransportPolicyError('Invalid transport clock')
        deadline = now + self.total_timeout
        timeout = self._boundary(context, deadline)
        connection = response = resources = None
        streaming_owner = False
        try:
            if origin.scheme == 'https':
                connection = http.client.HTTPSConnection(origin.host, origin.port, timeout=timeout, context=self.tls_context)
            else:
                connection = http.client.HTTPConnection(origin.host, origin.port, timeout=timeout)
            self._boundary(context, deadline, connection)
            connection.request('POST', target, body=body, headers=clean)
            self._boundary(context, deadline, connection)
            response = connection.getresponse()
            resources = _Resources(connection, response)
            self._boundary(context, deadline, connection)
            status = response.status
            if type(status) is not int or not 100 <= status <= 599:
                raise TransportProtocolError('Invalid HTTP status')
            if not 200 <= status < 300:
                # In particular, never follow 3xx or forward auth to Location.
                resources.close()
                return Response(status, b'', resources.close)
            encoding = response.getheader('Content-Encoding')
            if encoding and encoding.strip().lower() != 'identity':
                raise TransportProtocolError('Compressed provider responses are not supported')
            transfer = response.getheader('Transfer-Encoding')
            if transfer is not None and transfer.strip().lower() != 'chunked':
                raise TransportProtocolError('Unsupported provider transfer encoding')
            length = response.getheader('Content-Length')
            if length is not None:
                if response.getheader('Transfer-Encoding') is not None or not re.fullmatch(r'[0-9]+', length.strip()):
                    raise TransportProtocolError('Ambiguous provider response length')
                length = int(length)
                if length > self.max_response_bytes:
                    raise TransportProtocolError('Provider response exceeds byte limit')
            iterator = _Body(resources, context, self, deadline, length)
            if stream:
                streaming_owner = True
                return Response(status, iterator, resources.close)
            payload = b''.join(iterator)
            return Response(status, payload, resources.close)
        except (TransportCancelled, TransportPolicyError, TransportProtocolError, TransportTimeout):
            raise
        except (TimeoutError, socket.timeout):
            raise TransportTimeout('Provider HTTP operation timed out') from None
        except ssl.SSLError:
            raise TransportError('Provider TLS connection failed') from None
        except Exception:
            raise TransportError('Provider HTTP operation failed') from None
        finally:
            # A returned streaming body owns open resources. Every failure and
            # every fully-read/non-success response closes them here or at EOF.
            if not streaming_owner:
                if resources is not None:
                    resources.close()
                else:
                    if response is not None:
                        try: response.close()
                        except Exception: pass
                    if connection is not None:
                        try: connection.close()
                        except Exception: pass

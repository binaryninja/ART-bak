"""Bounded WSGI transport adapter for explicitly registered Python routes.

No listener is started here. Deployers must supply TLS, request timeouts, trusted
proxy configuration, rate limits and production storage/password adapters. Never
mount unported routes to generic success handlers. WSGI gateways may collapse
repeated headers before this adapter sees them: the gateway must reject ambiguous
duplicate Authorization/Content-Length headers and normalize URLs consistently.
Decoded PATH_INFO containing '?' or '#' is rejected, not reinterpreted as a URL.
StreamingResponse bodies are pull-based, never buffered or assigned Content-Length.
Gateways must close the returned iterable on disconnect; stream exceptions close
without appending internal errors or pretending application-level completion.
"""
from http import HTTPStatus
import re
from .core import Request, error_response
from .streaming import StreamingResponse, ClosingIterator

_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")
_HOP_BY_HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
               'te', 'trailer', 'trailers', 'transfer-encoding', 'upgrade'}


def _header_valid(name, value):
    if not isinstance(name, str) or not isinstance(value, str) or not _TOKEN.fullmatch(name):
        return False
    # WSGI header values are ISO-8859-1 strings; allow HTAB but no other controls.
    return all((ord(char) >= 32 or char == '\t') and ord(char) != 127 and ord(char) <= 255 for char in value)


class WSGIApplication:
    def __init__(self, router, *, max_body_bytes=1_048_576):
        if type(max_body_bytes) is not int or max_body_bytes < 0:
            raise ValueError('max_body_bytes must be a nonnegative integer')
        self.router = router
        self.max_body_bytes = max_body_bytes

    def __call__(self, environ, start_response):
        streaming = None
        try:
            response = self._handle(environ)
            if isinstance(response, StreamingResponse):
                streaming = ClosingIterator(response)
            if type(response.status) is not int or not 100 <= response.status <= 599 or (streaming is None and not isinstance(response.body, bytes)):
                raise ValueError('Invalid response')
            headers = list(response.headers.items())
            if any(not _header_valid(k, v) for k, v in headers):
                raise ValueError('Invalid response header')
            connection_fields = {part.strip().lower() for k, v in headers if k.lower() == 'connection'
                                 for part in v.split(',') if part.strip()}
            if any(not _TOKEN.fullmatch(name) for name in connection_fields):
                raise ValueError('Invalid connection option')
            headers = [(k, v) for k, v in headers if k.lower() not in
                       _HOP_BY_HOP | connection_fields | {'content-length', 'x-content-type-options'}]
        except Exception:
            if streaming is not None:
                streaming.close()
                streaming = None
            # Injected routers/handlers must never expose internal exception text.
            response = error_response(500, 'Internal server error')
            headers = list(response.headers.items())
        try:
            reason = HTTPStatus(response.status).phrase
        except ValueError:
            reason = 'Unknown'
        # WSGI servers own framing. Informational/204/304 responses have no body;
        # HEAD preserves the would-be representation length but emits no bytes.
        no_body_status = response.status < 200 or response.status in (204, 304)
        if not no_body_status and streaming is None:
            headers.append(('Content-Length', str(len(response.body))))
        headers.append(('X-Content-Type-Options', 'nosniff'))
        try:
            start_response(f'{response.status} {reason}', headers)
        except BaseException:
            if streaming is not None: streaming.close()
            raise
        if no_body_status or str(environ.get('REQUEST_METHOD', '')).upper() == 'HEAD':
            if streaming is not None: streaming.close()
            return []
        return streaming if streaming is not None else [response.body]

    def _handle(self, environ):
        try:
            length_text = environ.get('CONTENT_LENGTH', '')
            if not isinstance(length_text, str) or (length_text and (not length_text.isascii() or not length_text.isdecimal())):
                return error_response(400, 'Invalid Content-Length')
            length = int(length_text or '0')
            if length > self.max_body_bytes:
                return error_response(413, 'Request body too large')
            # WSGI servers decode chunked transfer; do not reinterpret raw headers.
            if environ.get('HTTP_TRANSFER_ENCODING'):
                return error_response(400, 'Unsupported transfer encoding')
            stream = environ.get('wsgi.input')
            body = stream.read(length) if length and stream is not None else b''
            if not isinstance(body, bytes) or len(body) != length:
                return error_response(400, 'Incomplete request body')
            method = environ.get('REQUEST_METHOD', 'GET')
            path = environ.get('PATH_INFO', '/')
            query = environ.get('QUERY_STRING', '')
            if not isinstance(method, str) or not isinstance(path, str) or not isinstance(query, str):
                return error_response(400, 'Invalid request')
            if (not _TOKEN.fullmatch(method) or not path.startswith('/') or path.startswith('//')
                    or '?' in path or '#' in path or '#' in query
                    or any(ord(c) < 32 or ord(c) == 127 for c in path + query)):
                return error_response(400, 'Invalid request target')
            headers = {}
            for key, value in environ.items():
                if not isinstance(key, str):
                    return error_response(400, 'Invalid environment key')
                if key.startswith('HTTP_'):
                    name = key[5:].replace('_', '-')
                    if name.upper() == 'CONTENT-LENGTH' or not _header_valid(name, value) or name.upper() in headers:
                        return error_response(400, 'Invalid header')
                    headers[name.upper()] = value
            if 'CONTENT_TYPE' in environ:
                value = environ['CONTENT_TYPE']
                if not _header_valid('CONTENT-TYPE', value) or 'CONTENT-TYPE' in headers:
                    return error_response(400, 'Invalid Content-Type')
                headers['CONTENT-TYPE'] = value
            return self.router.dispatch(Request(method, path + ('?' + query if query else ''), headers, body))
        except (OSError, ValueError, TypeError, AttributeError):
            return error_response(400, 'Invalid request')

"""Transport-independent routing; callers provide trusted Python handlers."""
from dataclasses import dataclass, field
import json
import re
from typing import Callable, Mapping
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Request:
    method: str
    target: str
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b''

    @property
    def path(self) -> str:
        return urlsplit(self.target).path

    def header(self, name: str) -> str:
        return next((v for k, v in self.headers.items() if k.lower() == name.lower()), '')

    def json_object(self, max_bytes: int = 1_048_576) -> dict:
        if len(self.body) > max_bytes:
            raise ValueError('Request body too large')
        value = json.loads(self.body, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid number')))
        if not isinstance(value, dict):
            raise ValueError('Expected JSON object')
        return value


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)

    def json(self):
        return json.loads(self.body)


def json_response(status: int, value) -> Response:
    return Response(status, (json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(',', ':')) + '\n').encode(),
                    {'Content-Type': 'application/json'})


def error_response(status: int, message: str) -> Response:
    return json_response(status, {'error': message})


class Router:
    """Exact and {parameter} paths; protected by default, no catch-all auth bypass.

    Deliberately not an HTTP server or a complete Go ServeMux replacement.
    Encoded path separators and slash canonicalization are left to transport adapters.
    """
    def __init__(self, authorize: Callable[[Request], bool]):
        self.authorize = authorize
        self._routes = []

    def add(self, method: str, path: str, handler: Callable, *, public: bool = False):
        if not path.startswith('/') or '?' in path:
            raise ValueError('Expected absolute route path without query')
        names = re.findall(r'\{([A-Za-z_][A-Za-z_0-9]*)\}', path)
        if len(names) != len(set(names)):
            raise ValueError('Duplicate route parameter')
        pattern = re.escape(path)
        for name in names:
            pattern = pattern.replace(re.escape('{' + name + '}'), '(?P<' + name + '>[^/]+)')
        if any(m == method.upper() and p == path for m, p, *_ in self._routes):
            raise ValueError('Duplicate route')
        self._routes.append((method.upper(), path, re.compile('^' + pattern + '$'), handler, public))
        # Static routes win over parameter routes regardless of registration order.
        self._routes.sort(key=lambda r: r[1].count('{'))

    def mount_protected(self, staged, *, use_parent_authorizer=False):
        """Atomically mount staged declarations under this router's authorization.

        This does not inherit staged.authorize. Callers must explicitly opt in
        to the parent policy; service-level scope/body policies remain in handlers.
        Only protected declarations are accepted, and conflicting parameter names
        cannot hide the same method/path shape. No partial mount on validation error.
        """
        if use_parent_authorizer is not True or not isinstance(staged, Router) or staged is self:
            raise ValueError('Explicit parent authorization and distinct staging router required')
        identity = lambda method, path: (method, re.sub(r'\{[^}]+\}', '{}', path))
        existing = {identity(method, path) for method, path, *_ in self._routes}
        additions = list(staged._routes)
        for method, path, _, _, public in additions:
            if public:
                raise ValueError('Staged routes must remain protected')
            key = identity(method, path)
            if key in existing:
                raise ValueError('Route already registered')
            existing.add(key)
        self._routes.extend(additions)
        self._routes.sort(key=lambda row: row[1].count('{'))

    def dispatch(self, request: Request) -> Response:
        matches = [(m, p, regex.fullmatch(request.path), h, public)
                   for m, p, regex, h, public in self._routes if regex.fullmatch(request.path)]
        # Unknown routes remain protected; callers must explicitly mark public routes.
        selected = next((r for r in matches if r[0] == request.method.upper()), None)
        # Go ServeMux GET patterns also match HEAD, unless an explicit HEAD
        # registration wins. Keep authorization tied to the selected handler.
        if selected is None and request.method.upper() == 'HEAD':
            selected = next((r for r in matches if r[0] == 'GET'), None)
        if selected is None or not selected[4]:
            try:
                allowed = self.authorize(request)
            except Exception:
                allowed = False
            if not allowed:
                return error_response(401, 'Unauthorized')
        if not matches:
            return error_response(404, 'Not found')
        if selected is None:
            response = error_response(405, 'Method not allowed')
            return Response(response.status, response.body, {**response.headers, 'Allow': ', '.join(sorted(({r[0] for r in matches} | ({'HEAD'} if any(r[0] == 'GET' for r in matches) else set()))))})
        try:
            return selected[3](request, selected[2].groupdict())
        except Exception:
            return error_response(500, 'Internal server error')

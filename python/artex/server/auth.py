"""Go auth.go contracts with explicit fail-closed dependencies and HS256 only."""
import base64
import hashlib
import hmac
from http.cookies import SimpleCookie, CookieError
import json
import math
import time
from typing import Protocol
from urllib.parse import parse_qs, urlsplit
from .core import Router, error_response, json_response

PASSWORD_KEY = 'auth.password_hash'
JWT_TTL = 7 * 24 * 60 * 60


def validate_password(password: str) -> str:
    if len(password) < 8:
        return 'Password must contain at least 8 characters'
    try:
        encoded = password.encode('utf-8')
    except UnicodeError:
        return 'Password must be valid UTF-8'
    if len(encoded) > 72:
        return 'Password must not exceed 72 bytes'
    return ''


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def _decode(value: str) -> bytes:
    return base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)


def sign_jwt(key: bytes, *, now: float | None = None) -> str:
    if len(key) < 32:
        raise ValueError('Signing key must be at least 32 bytes')
    now = time.time() if now is None else now
    header = _encode(b'{"alg":"HS256","typ":"JWT"}')
    claims = _encode(json.dumps({'sub': 'ARTEX', 'iat': int(now), 'exp': int(now) + JWT_TTL}, separators=(',', ':')).encode())
    payload = header + '.' + claims
    return payload + '.' + _encode(hmac.digest(key, payload.encode(), hashlib.sha256))


def verify_jwt(token: str, key: bytes, *, now: float | None = None) -> bool:
    try:
        if len(key) < 32 or len(token) > 16384:
            return False
        header, claims, signature = token.split('.')
        if json.loads(_decode(header)).get('alg') != 'HS256':
            return False
        if not hmac.compare_digest(_decode(signature), hmac.digest(key, (header + '.' + claims).encode('ascii'), hashlib.sha256)):
            return False
        data = json.loads(_decode(claims))
        now = time.time() if now is None else now
        exp = data.get('exp')
        if type(exp) not in (int, float) or not math.isfinite(exp) or exp <= now:
            return False
        nbf = data.get('nbf', 0)
        return type(nbf) in (int, float) and math.isfinite(nbf) and nbf <= now
    except (ValueError, TypeError, AttributeError, UnicodeError, OverflowError):
        return False


def extract_token(request) -> str:
    header = request.header('Authorization')
    if header.startswith('Bearer '):
        return header[7:]
    try:
        cookie = SimpleCookie()
        cookie.load(request.header('Cookie'))
        if 'artex_token' in cookie and cookie['artex_token'].value:
            return cookie['artex_token'].value
    except CookieError:
        pass
    return parse_qs(urlsplit(request.target).query).get('token', [''])[0]


class PasswordStore(Protocol):
    """Implementations must raise on read failure, and insert atomically."""
    def get(self, key: str) -> str | None: ...
    def insert_if_absent(self, key: str, value: str) -> bool: ...
    def set(self, key: str, value: str) -> None: ...


class PasswordCodec(Protocol):
    """Production adapter must use bcrypt, never plaintext or a fast digest."""
    def hash(self, password: str) -> str: ...
    def verify(self, password: str, encoded: str) -> bool: ...


class AuthService:
    def __init__(self, store: PasswordStore, codec: PasswordCodec, key: bytes, *, clock=time.time):
        if len(key) < 32:
            raise ValueError('Signing key must be at least 32 bytes')
        self.store, self.codec, self.key, self.clock = store, codec, key, clock

    def authorized(self, request) -> bool:
        return verify_jwt(extract_token(request), self.key, now=self.clock())

    def router(self, version: str = 'dev') -> Router:
        router = Router(self.authorized)
        router.add('GET', '/api/health', lambda r, p: json_response(200, {'ok': True, 'service': 'artex', 'version': version}), public=True)
        for method, name in [('GET', 'status'), ('POST', 'init'), ('POST', 'login'), ('POST', 'change-password')]:
            router.add(method, '/api/auth/' + name, lambda r, p, name=name: self.handle(name, r), public=name != 'change-password')
        return router

    def handle(self, action, request):
        if action not in ('status', 'init', 'login', 'change-password'):
            return error_response(404, 'Not found')
        if action == 'change-password' and not self.authorized(request):
            return error_response(401, 'Unauthorized')
        try:
            existing = self.store.get(PASSWORD_KEY)
        except Exception:
            return error_response(503, 'Data source temporarily unavailable')
        if action == 'status':
            return json_response(200, {'initialized': bool(existing)})
        if action == 'init' and existing:
            return error_response(403, 'Password already set')
        try:
            data = request.json_object()
            field = 'new_password' if action == 'change-password' else 'password'
            password = data.get(field, '')
            if not isinstance(password, str) or not isinstance(data.get('old_password', ''), str):
                raise ValueError('Invalid password')
        except (ValueError, UnicodeError):
            return error_response(400, 'Invalid request')
        if action in ('init', 'change-password'):
            error = validate_password(password)
            if error:
                return error_response(400, error)
        if action != 'init' and not existing:
            return error_response(403, 'Password not initialized')
        try:
            if action == 'login':
                if data.get('username') != 'ARTEX' or not self.codec.verify(password, existing):
                    return error_response(401, 'Invalid username or password')
            elif action == 'init':
                if not self.store.insert_if_absent(PASSWORD_KEY, self.codec.hash(password)):
                    return error_response(403, 'Password already set')
            else:
                if not self.codec.verify(data.get('old_password', ''), existing):
                    return error_response(401, 'Incorrect current password')
                self.store.set(PASSWORD_KEY, self.codec.hash(password))
                return json_response(200, {'ok': True})
            return json_response(200, {'token': sign_jwt(self.key, now=self.clock())})
        except Exception:
            return error_response(500, 'Authentication operation failed')

"""Optional production bcrypt adapter for AuthService's PasswordCodec protocol.

Requires the separately installed official PyCA ``bcrypt>=5.0,<6`` package.
Import is lazy; this module does not install packages, open sockets, read stored
credentials, or provide a runnable server. A missing/unsupported dependency is an
explicit error. There is no plaintext, fast-hash, truncation or prehash fallback.

Creation and verification use auth.validate_password unchanged: at least eight
Unicode characters and at most 72 UTF-8 bytes (not eight bytes). Invalid input
fails before importing or invoking bcrypt. Existing $2a$/$2b$/$2y$ hashes can be
verified; new hashes use $2b$ with cost 10 by default, matching Go's original creation
cost. Stored hashes retain their embedded work factor. Deployments may
explicitly select a higher work factor after measuring local performance.

Reviewed upstream contracts (2026-11-15):
https://pypi.org/project/bcrypt/#changelog
https://github.com/pyca/bcrypt/blob/main/src/bcrypt/__init__.pyi
bcrypt 5 rejects input beyond 72 bytes; previous versions could truncate it.
Explicit input bounds here avoid reliance on version-dependent truncation.
A driver can be injected for deterministic unit tests; production must use PyCA.
"""
from __future__ import annotations

import importlib
import re
from typing import Protocol
from .auth import validate_password

DEFAULT_ROUNDS = 10
# A complete bcrypt hash is 60 ASCII bytes, including prefix and work factor.
_HASH = re.compile(r'\$2[aby]\$(?:0[4-9]|[12][0-9]|3[01])\$[./A-Za-z0-9]{53}\Z')
_VERSION = re.compile(r'5\.[0-9]+\.[0-9]+(?:\.post[0-9]+)?\Z')


class BcryptUnavailable(RuntimeError):
    """A supported, installed bcrypt backend is required; there is no fallback."""


class PasswordHashError(RuntimeError):
    """Hashing failed; error details must not disclose passwords or hash inputs."""


class BcryptDriver(Protocol):
    def gensalt(self, rounds: int = 12, prefix: bytes = b'2b') -> bytes: ...
    def hashpw(self, password: bytes, salt: bytes) -> bytes: ...
    def checkpw(self, password: bytes, hashed_password: bytes) -> bool: ...


def _password_bytes(password: str) -> bytes:
    if not isinstance(password, str):
        raise ValueError('Password must be a string')
    try:
        message = validate_password(password)
        if message:
            raise ValueError(message)
        return password.encode('utf-8')
    except UnicodeError:
        raise ValueError('Password must be valid UTF-8') from None


class BcryptCodec:
    """Fail-closed bcrypt PasswordCodec, with no credential persistence.

    ``rounds`` is trusted deployment configuration, not a request parameter.
    Lower cost values remain supported for controlled interoperability testing;
    deployments should retain the default or calibrate a higher cost locally.
    Dependency injection is a testing seam, not a user-selectable plugin API.
    """
    def __init__(self, *, rounds: int = DEFAULT_ROUNDS, driver: BcryptDriver | None = None):
        if type(rounds) is not int or not 4 <= rounds <= 31:
            raise ValueError('bcrypt rounds must be an integer between 4 and 31')
        self.rounds = rounds
        self._driver = driver

    def _backend(self) -> BcryptDriver:
        if self._driver is None:
            try:
                driver = importlib.import_module('bcrypt')
            except (ImportError, OSError):
                raise BcryptUnavailable('Install the official PyCA bcrypt>=5.0,<6 dependency') from None
            version = getattr(driver, '__version__', '')
            if not isinstance(version, str) or not _VERSION.fullmatch(version):
                raise BcryptUnavailable('A supported PyCA bcrypt 5.x release is required')
            if not all(callable(getattr(driver, name, None)) for name in ('gensalt', 'hashpw', 'checkpw')):
                raise BcryptUnavailable('The bcrypt dependency does not provide the required API')
            self._driver = driver
        return self._driver

    def hash(self, password: str) -> str:
        raw = _password_bytes(password)
        driver = self._backend()
        try:
            salt = driver.gensalt(rounds=self.rounds, prefix=b'2b')
            value = driver.hashpw(raw, salt)
            if not isinstance(value, bytes):
                raise ValueError('Invalid backend output')
            encoded = value.decode('ascii')
            if not _HASH.fullmatch(encoded) or not encoded.startswith(f'$2b${self.rounds:02d}$'):
                raise ValueError('Invalid backend output')
            return encoded
        except Exception:
            raise PasswordHashError('bcrypt password hashing failed') from None

    def verify(self, password: str, encoded: str) -> bool:
        try:
            raw = _password_bytes(password)
        except ValueError:
            return False
        if not isinstance(encoded, str) or not _HASH.fullmatch(encoded):
            return False
        driver = self._backend()
        try:
            # checkpw is the backend's password-checking operation; do not
            # regenerate salts or compare hash strings with Python equality.
            return driver.checkpw(raw, encoded.encode('ascii')) is True
        except ValueError:
            # Invalid salts/hashes are not successful authentication.
            return False
        except Exception:
            raise PasswordHashError('bcrypt password verification failed') from None

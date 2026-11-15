"""PostgreSQL settings persistence through the injected Executor contract.

Ports db/settings.go and implements server.auth.PasswordStore structurally.
There is no in-memory production fallback, connection creation, schema migration,
credential logging, or import-time I/O. A real Executor must bind Query.params,
commit successful statements (or expose the owning transaction's commit), return
accurate affected counts, and propagate errors. Do not return a success response
before the transaction containing an auth initialization is durably committed.

First-admin initialization is a SINGLE INSERT ... ON CONFLICT DO NOTHING. The
existing settings.key primary key arbitrates concurrent writers; a prior get is
only an early check, never the security boundary. No application lock or invented
compare-and-swap API is required. Auth password changes retain upstream upsert
behavior; this module does not add token revocation or conditional password changes.
"""
from collections.abc import Sequence
from .core import Executor, Query

_GET = 'SELECT value FROM settings WHERE key=$1'
_SET = '''INSERT INTO settings(key, value) VALUES ($1, $2)
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()'''
_INSERT_IF_ABSENT = '''INSERT INTO settings(key, value) VALUES ($1, $2)
ON CONFLICT (key) DO NOTHING'''
_DELETE = 'DELETE FROM settings WHERE key=$1'


class SettingsContractError(RuntimeError):
    """The executor returned a result incompatible with the settings schema."""


def _string(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f'{name} must be a string')
    return value


def _affected(value: int, *, may_be_absent: bool) -> int:
    allowed = (0, 1) if may_be_absent else (1,)
    if type(value) is not int or value not in allowed:
        # Never leak the setting value or key via diagnostic messages.
        raise SettingsContractError('Executor returned an invalid settings affected-row count')
    return value


class SettingsStore:
    """Settings CRUD and the get/insert_if_absent/set auth-store interface.

    get returns None only for a genuinely absent row, preserving empty strings.
    It propagates database failures. get_bool alone mirrors upstream's explicitly
    best-effort default behavior and MUST NOT be used for auth initialization.
    """

    def __init__(self, executor: Executor):
        self.executor = executor

    def get(self, key: str) -> str | None:
        rows = self.executor.fetch_all(Query(_GET, (_string(key, 'key'),)))
        # Executor promises a row sequence. Invalid results must not look absent.
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
            raise SettingsContractError('Executor did not return a settings row sequence')
        if not rows:
            return None
        if (len(rows) != 1 or not isinstance(rows[0], Sequence)
                or isinstance(rows[0], (str, bytes, bytearray)) or len(rows[0]) != 1):
            raise SettingsContractError('Executor returned an invalid settings row shape')
        value = rows[0][0]
        if not isinstance(value, str):
            raise SettingsContractError('Executor returned a non-text settings value')
        return value

    def set(self, key: str, value: str) -> None:
        query = Query(_SET, (_string(key, 'key'), _string(value, 'value')))
        _affected(self.executor.execute(query), may_be_absent=False)

    def insert_if_absent(self, key: str, value: str) -> bool:
        query = Query(_INSERT_IF_ABSENT, (_string(key, 'key'), _string(value, 'value')))
        return _affected(self.executor.execute(query), may_be_absent=True) == 1

    def delete(self, key: str) -> bool:
        """Bound-key deletion; returns False if absent (helper beyond Go methods).

        Mirrors DELETE FROM settings WHERE key=$1 used by the upstream settings
        regression tests. This is not an authorization check; callers control who
        may delete settings, especially auth.password_hash.
        """
        query = Query(_DELETE, (_string(key, 'key'),))
        return _affected(self.executor.execute(query), may_be_absent=True) == 1

    def get_bool(self, key: str, default: bool = False) -> bool:
        """Go compatibility: missing/read error -> default; only 'true'/'1' true.

        Other stored strings are False even if default=True. This follows the
        implementation of Go GetBool, not its broader 'unparseable' comment.
        """
        _string(key, 'key')
        if type(default) is not bool:
            raise TypeError('default must be a bool')
        try:
            value = self.get(key)
        except Exception:
            return default
        return default if value is None else value in ('true', '1')

    def set_bool(self, key: str, value: bool) -> None:
        if type(value) is not bool:
            raise TypeError('value must be a bool')
        self.set(key, 'true' if value else 'false')

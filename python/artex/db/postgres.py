"""Optional synchronous psycopg >= 3.2 adapter; no import-time connections.

RawCursor binds PostgreSQL $1/$2 placeholders directly, without rewriting SQL:
https://www.psycopg.org/psycopg3/docs/advanced/cursors.html#raw-query-cursors
Transaction contexts commit on normal exit and roll back exceptions:
https://www.psycopg.org/psycopg3/docs/basic/transactions.html#transaction-contexts

Use an exclusively owned/borrowed, idle autocommit connection. Do not share that
connection with code outside this adapter. The adapter serializes its own callers
and holds its lock for an entire transaction. Standalone writes, including writes
with RETURNING, commit before success is returned. No schema migrations occur.
The optional driver and live PostgreSQL behavior require integration validation;
unit tests exercise an injected driver contract only.
"""
from __future__ import annotations

from contextlib import contextmanager
from threading import RLock
from typing import Any, Callable
from .core import Query


class NoRowsError(LookupError):
    """fetch_one returned no row (distinct from database/transport failure)."""


def _driver():
    try:
        import psycopg
        from psycopg.rows import tuple_row
    except ImportError as error:
        raise RuntimeError('PostgreSQL support requires optional psycopg >= 3.2') from error
    if not hasattr(psycopg, 'RawCursor'):
        raise RuntimeError('PostgreSQL support requires psycopg >= 3.2 with RawCursor')
    return psycopg, tuple_row


class PostgresExecutor:
    def __init__(self, connection: Any, *, cursor_factory: Callable | None = None,
                 owns_connection: bool = False):
        """Injected factory must return a context-managed native-bind tuple cursor.

        For a real psycopg connection omit cursor_factory to use RawCursor with
        tuple_row. A custom factory exists for contract tests, not SQL rewriting.
        """
        if not connection.autocommit:
            raise ValueError('PostgresExecutor requires an autocommit connection')
        if connection.info.transaction_status != 0:  # psycopg TransactionStatus.IDLE
            raise ValueError('PostgresExecutor requires an idle connection')
        if cursor_factory is None:
            driver, tuple_row = _driver()
            cursor_factory = lambda conn: driver.RawCursor(conn, row_factory=tuple_row)
        self._connection = connection
        self._cursor_factory = cursor_factory
        self._owns_connection = owns_connection
        self._lock = RLock()
        self._depth = 0
        self._closed = False

    @classmethod
    def connect(cls, dsn: str, **options) -> PostgresExecutor:
        """Open a new owned connection; caller must close it (or use with).

        Requires an installed optional psycopg >= 3.2. No credentials are logged.
        Explicit autocommit=False is rejected rather than silently overridden.
        """
        if options.get('autocommit', True) is not True:
            raise ValueError('PostgresExecutor requires autocommit=True')
        options['autocommit'] = True
        driver, tuple_row = _driver()
        connection = driver.connect(dsn, **options)
        try:
            return cls(connection, cursor_factory=lambda conn: driver.RawCursor(conn, row_factory=tuple_row),
                       owns_connection=True)
        except BaseException:
            connection.close()
            raise

    def _check_open(self):
        if self._closed or self._connection.closed:
            raise RuntimeError('PostgreSQL executor is closed')

    @contextmanager
    def transaction(self):
        """Commit before leaving; propagate query, rollback, and commit failures.

        Nested explicit scopes use the driver's savepoints. The executor cannot
        close while inside a transaction. Never use it after context exit if the
        outer owner has closed it.
        """
        with self._lock:
            self._check_open()
            with self._connection.transaction():
                self._depth += 1
                try:
                    yield self
                finally:
                    self._depth -= 1

    @contextmanager
    def _operation(self):
        with self._lock:
            self._check_open()
            if self._depth:
                yield
            else:
                with self.transaction():
                    yield

    def _run(self, query: Query, mode: str):
        if not isinstance(query, Query):
            raise TypeError('query must be a Query instance')
        if not isinstance(query.sql, str) or not isinstance(query.params, tuple):
            raise TypeError('Query requires SQL text and a parameter tuple')
        with self._operation():
            with self._cursor_factory(self._connection) as cursor:
                # RawCursor sends SQL unchanged and binds values separately.
                cursor.execute(query.sql, query.params)
                if mode == 'all':
                    result = cursor.fetchall()
                elif mode == 'one':
                    result = cursor.fetchone()
                    if result is None:
                        raise NoRowsError('query returned no rows')
                else:
                    result = cursor.rowcount
                    if type(result) is not int or result < 0:
                        raise RuntimeError('driver did not report an affected-row count')
        return result

    def fetch_all(self, query: Query):
        return self._run(query, 'all')

    def fetch_one(self, query: Query):
        return self._run(query, 'one')

    def execute(self, query: Query) -> int:
        return self._run(query, 'execute')

    def close(self):
        with self._lock:
            if self._depth:
                raise RuntimeError('cannot close executor during a transaction')
            if not self._closed:
                if self._owns_connection:
                    self._connection.close()
                self._closed = True

    def __enter__(self):
        with self._lock:
            self._check_open()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False

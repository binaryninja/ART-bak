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


class CommitBoundaryError(RuntimeError):
    """Owned outer commit refused or its completion could not be confirmed."""


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
        self._committed = None

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
            if self._committed is not None:
                with self._checked_transaction():
                    yield self
                return
            with self._connection.transaction():
                self._depth += 1
                try:
                    yield self
                finally:
                    self._depth -= 1

    @contextmanager
    def _checked_transaction(self):
        """Keep failures sticky only inside an opted-in outer boundary."""
        state = self._committed
        if state['failure'] is not None:
            raise state['failure']
        completed = False
        try:
            with self._connection.transaction():
                self._depth += 1
                try:
                    try:
                        yield self
                        if state['failure'] is not None:
                            raise state['failure']
                        if (self._connection.closed or not self._connection.autocommit or
                                self._connection.info.transaction_status != 2):
                            raise CommitBoundaryError('Transaction body did not leave an active transaction')
                        completed = True
                    except BaseException as error:
                        if state['failure'] is None:
                            state['failure'] = error
                        raise
                finally:
                    self._depth -= 1
        except BaseException as error:
            # An exposed driver exit error takes precedence over the body error
            # it interrupted: do not lose commit/rollback uncertainty if caught.
            state['failure'] = error
            raise
        # A driver can suppress Rollback (or an injected body error). It must
        # never turn the opted-in boundary into a successful mutation result.
        if not completed or state['failure'] is not None:
            if state['failure'] is not None:
                raise state['failure']
            raise CommitBoundaryError('Driver suppressed an incomplete transaction')

    @contextmanager
    def committed_transaction(self):
        """Own a fresh outer commit; normal return acknowledges driver completion.

        Caller-owned/nested/ambient transactions are refused before yielding.
        Successful ordinary savepoints are allowed inside; any nested failure is
        sticky, even when caught or driver-suppressed, and aborts before the outer
        commit. Ordinary transaction() outside this boundary remains unchanged.

        This trusts the exclusively used driver's default transaction contract;
        it is not a durable receipt, task authority, or all-writer barrier.
        Exposed driver errors propagate unchanged and may represent uncertain
        commit/rollback. There is no retry. Lock/driver waits retain the caller's
        existing timeout configuration; this does not add bounded cancellation.
        """
        with self._lock:
            self._check_open()
            if (self._committed is not None or self._depth or
                    self._connection.info.transaction_status != 0 or
                    not self._connection.autocommit):
                raise CommitBoundaryError('Committed transaction requires an idle autocommit connection and no existing transaction')
            self._committed = {'failure': None}
            try:
                with self._checked_transaction():
                    yield self
                if (self._connection.closed or not self._connection.autocommit or
                        self._connection.info.transaction_status != 0):
                    raise CommitBoundaryError('Driver did not settle the committed transaction')
            finally:
                self._committed = None

    @contextmanager
    def archive_snapshot_transaction(self):
        """Fresh read-only repeatable-read snapshot; never upgrade a nested scope.

        Exact transaction-control SQL intentionally bypasses DML rowcount checks.
        No arbitrary control command API or automatic read-write fallback exists.
        """
        with self._lock:
            self._check_open()
            if self._depth or self._connection.info.transaction_status != 0:
                raise RuntimeError('Archive snapshot requires an idle connection and no transaction')
            with self.transaction():
                with self._cursor_factory(self._connection) as cursor:
                    cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY', ())
                yield self

    def lock_intercept_settings(self):
        """Exact settings CAS lock inside an already owned transaction only.

        LOCK has no DML affected-row count. This deliberately accepts no SQL or
        identifier argument and does not relax execute() rowcount guarantees.
        The transaction owner retains the lock through commit/rollback.
        """
        with self._lock:
            self._check_open()
            if not self._depth:
                raise RuntimeError('Intercept settings lock requires an active transaction')
            with self._cursor_factory(self._connection) as cursor:
                cursor.execute('LOCK TABLE settings IN SHARE ROW EXCLUSIVE MODE', ())

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

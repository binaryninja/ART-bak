from contextlib import contextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from artex.db.core import Query
from artex.db.postgres import PostgresExecutor, NoRowsError


class Connection:
    def __init__(self):
        self.autocommit = True
        self.info = SimpleNamespace(transaction_status=0)
        self.closed = False
        self.events = []
        self.rows = [(1,)]
        self.rowcount = 1
        self.execute_error = None
        self.fetch_error = None
        self.commit_error = None

    @contextmanager
    def transaction(self):
        self.events.append('begin')
        try:
            yield
        except BaseException:
            self.events.append('rollback')
            raise
        else:
            self.events.append('commit')
            if self.commit_error:
                raise self.commit_error

    def close(self):
        self.events.append('connection.close')
        self.closed = True


class Cursor:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        self.connection.events.append('cursor.open')
        return self

    def __exit__(self, *args):
        self.connection.events.append('cursor.close')

    def execute(self, sql, params):
        self.connection.events.append(('execute', sql, params))
        if self.connection.execute_error:
            raise self.connection.execute_error

    def fetchall(self):
        if self.connection.fetch_error:
            raise self.connection.fetch_error
        return self.connection.rows

    def fetchone(self):
        rows = self.fetchall()
        return rows[0] if rows else None

    @property
    def rowcount(self):
        return self.connection.rowcount


class PostgresTests(unittest.TestCase):
    def make(self, **kwargs):
        connection = Connection()
        return connection, PostgresExecutor(connection, cursor_factory=Cursor, **kwargs)

    def test_native_placeholders_untouched_and_bound(self):
        conn, db = self.make()
        sql = "SELECT $2, $1, '$1 %s', $$literal $2$$, $1"
        params = ("x'); DROP TABLE settings; --", 7)
        self.assertEqual(db.fetch_all(Query(sql, params)), [(1,)])
        self.assertEqual(conn.events, ['begin', 'cursor.open', ('execute', sql, params), 'cursor.close', 'commit'])

    def test_writes_and_returning_commit_before_success(self):
        conn, db = self.make()
        self.assertEqual(db.execute(Query('UPDATE settings SET value=$1', ('new hash',))), 1)
        self.assertEqual(conn.events[-1], 'commit')
        self.assertEqual(db.fetch_one(Query('INSERT INTO task_categories(name) VALUES ($1) RETURNING id', ('name',))), (1,))
        self.assertEqual(conn.events[-1], 'commit')

    def test_no_rows_rolls_back_and_cursor_closes(self):
        conn, db = self.make()
        conn.rows = []
        self.assertEqual(db.fetch_all(Query('SELECT 1')), [])
        with self.assertRaises(NoRowsError):
            db.fetch_one(Query('SELECT 1'))
        self.assertEqual(conn.events[-2:], ['cursor.close', 'rollback'])

    def test_errors_propagate_and_rollback(self):
        for field in ('execute_error', 'fetch_error'):
            conn, db = self.make()
            error = RuntimeError('driver failed')
            setattr(conn, field, error)
            with self.assertRaises(RuntimeError) as raised:
                db.fetch_all(Query('SELECT $1', (4,)))
            self.assertIs(raised.exception, error)
            self.assertEqual(conn.events[-2:], ['cursor.close', 'rollback'])
        conn, db = self.make()
        conn.rowcount = -1
        with self.assertRaisesRegex(RuntimeError, 'affected-row'):
            db.execute(Query('UPDATE settings SET value=$1', ('value',)))
        self.assertEqual(conn.events[-1], 'rollback')

    def test_transaction_groups_operations(self):
        conn, db = self.make()
        with db.transaction() as tx:
            self.assertIs(tx, db)
            tx.execute(Query('UPDATE tasks SET category_id=$1', (7,)))
            tx.fetch_one(Query('SELECT $1', (7,)))
            self.assertNotIn('commit', conn.events)
        self.assertEqual(conn.events.count('begin'), 1)
        self.assertEqual(conn.events.count('commit'), 1)
        self.assertEqual(conn.events[-1], 'commit')

    def test_transaction_rollback_and_nested_scope(self):
        conn, db = self.make()
        with self.assertRaisesRegex(ValueError, 'bad'):
            with db.transaction():
                db.execute(Query('UPDATE tasks SET category_id=NULL'))
                raise ValueError('bad')
        self.assertEqual(conn.events[-1], 'rollback')
        with db.transaction():
            with db.transaction():
                db.fetch_all(Query('SELECT 1'))
        self.assertEqual(conn.events[-2:], ['commit', 'commit'])

    def test_commit_failure_not_success(self):
        conn, db = self.make()
        error = RuntimeError('commit failed')
        conn.commit_error = error
        with self.assertRaises(RuntimeError) as raised:
            db.execute(Query('UPDATE settings SET value=$1', ('new hash',)))
        self.assertIs(raised.exception, error)
        self.assertEqual(conn.events[-2:], ['cursor.close', 'commit'])

    def test_borrowed_owned_close_and_closed_rejection(self):
        conn, db = self.make()
        db.close()
        self.assertFalse(conn.closed)
        with self.assertRaisesRegex(RuntimeError, 'closed'):
            db.fetch_all(Query('SELECT 1'))
        conn, db = self.make(owns_connection=True)
        with db:
            db.fetch_all(Query('SELECT 1'))
        db.close()
        self.assertTrue(conn.closed)
        self.assertEqual(conn.events.count('connection.close'), 1)

    def test_reject_unsafe_connection_and_close_in_transaction(self):
        conn = Connection()
        conn.autocommit = False
        with self.assertRaisesRegex(ValueError, 'autocommit'):
            PostgresExecutor(conn, cursor_factory=Cursor)
        conn.autocommit = True
        conn.info.transaction_status = 2
        with self.assertRaisesRegex(ValueError, 'idle'):
            PostgresExecutor(conn, cursor_factory=Cursor)
        conn, db = self.make()
        with self.assertRaisesRegex(RuntimeError, 'during a transaction'):
            with db.transaction():
                db.close()
        self.assertEqual(conn.events[-1], 'rollback')

    def test_connect_driver_factory_and_cleanup(self):
        conn = Connection()
        calls = []
        marker = object()
        def raw_cursor(connection, *, row_factory):
            self.assertIs(row_factory, marker)
            return Cursor(connection)
        def connect(dsn, **options):
            calls.append((dsn, options))
            return conn
        driver = SimpleNamespace(connect=connect, RawCursor=raw_cursor)
        with patch('artex.db.postgres._driver', return_value=(driver, marker)):
            with PostgresExecutor.connect('local-dsn', connect_timeout=3) as db:
                db.fetch_one(Query('SELECT 1'))
            self.assertEqual(calls, [('local-dsn', {'connect_timeout': 3, 'autocommit': True})])
            self.assertTrue(conn.closed)
            conn = Connection()
            conn.info.transaction_status = 2
            with self.assertRaises(ValueError):
                PostgresExecutor.connect('local-dsn')
            self.assertTrue(conn.closed)


if __name__ == '__main__':
    unittest.main()

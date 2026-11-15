"""Offline SQL-contract tests; no real credentials, database, or model calls."""
import json
from pathlib import Path
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
import unittest

from artex.db.settings import SettingsStore, SettingsContractError
from artex.server.auth import AuthService, PASSWORD_KEY
from artex.server.core import Request


class RecordingExecutor:
    def __init__(self):
        self.queries = []
        self.rows = []
        self.affected = 1
        self.error = None

    def _record(self, query):
        self.queries.append(query)
        if self.error:
            raise self.error

    def fetch_all(self, query):
        self._record(query)
        return self.rows

    def fetch_one(self, query):
        raise AssertionError('SettingsStore must not assume an unspecified missing-row sentinel')

    def execute(self, query):
        self._record(query)
        return self.affected


class AtomicInsertFixture(RecordingExecutor):
    """Test-only executor modeling the key constraint, NOT a production store."""
    def __init__(self):
        super().__init__()
        self.lock = Lock()
        self.values = {}

    def fetch_all(self, query):
        with self.lock:
            self._record(query)
            return [(self.values[query.params[0]],)] if query.params[0] in self.values else []

    def execute(self, query):
        with self.lock:
            self._record(query)
            if 'ON CONFLICT (key) DO NOTHING' not in query.sql:
                raise AssertionError('Fixture accepts atomic initialization only')
            key, value = query.params
            if key in self.values:
                return 0
            self.values[key] = value
            return 1


class TestOnlyCodec:
    """Noncryptographic fabricated codec solely for the auth integration fixture."""
    def __init__(self, barrier=None):
        self.barrier = barrier

    def hash(self, password):
        if self.barrier:
            self.barrier.wait(timeout=5)
        return 'TEST-ONLY:' + password

    def verify(self, password, encoded):
        return encoded == 'TEST-ONLY:' + password


class SettingsStoreTests(unittest.TestCase):
    def setUp(self):
        self.executor = RecordingExecutor()
        self.store = SettingsStore(self.executor)

    def test_missing_and_empty_are_distinct(self):
        self.assertIsNone(self.store.get('theme'))
        self.executor.rows = [('',)]
        self.assertEqual(self.store.get('theme'), '')
        self.executor.rows = [('dark',)]
        self.assertEqual(self.store.get('theme'), 'dark')
        self.assertEqual(self.executor.queries[0].params, ('theme',))

    def test_all_operations_bind_keys_and_values(self):
        key, value = "key' OR 1=1 --", "hash'); DROP TABLE settings; --"
        self.store.get(key)
        self.store.set(key, value)
        self.store.insert_if_absent(key, value)
        self.store.delete(key)
        for query in self.executor.queries:
            self.assertNotIn(key, query.sql)
            self.assertNotIn(value, query.sql)
            self.assertEqual(query.params[0], key)
        self.assertEqual(self.executor.queries[1].params, (key, value))

    def test_upsert_refreshes_timestamp(self):
        self.store.set('theme', 'light')
        self.assertIn('DO UPDATE SET value = EXCLUDED.value, updated_at = now()', self.executor.queries[-1].sql)

    def test_initialization_is_one_statement_not_read_then_write(self):
        self.assertTrue(self.store.insert_if_absent(PASSWORD_KEY, 'fixture'))
        self.assertEqual(len(self.executor.queries), 1)
        query = self.executor.queries[0]
        self.assertIn('ON CONFLICT (key) DO NOTHING', query.sql)
        self.assertNotIn('DO UPDATE', query.sql)
        self.executor.affected = 0
        self.assertFalse(self.store.insert_if_absent(PASSWORD_KEY, 'second'))

    def test_delete_reports_absence(self):
        self.assertTrue(self.store.delete('theme'))
        self.executor.affected = 0
        self.assertFalse(self.store.delete('theme'))
        self.assertEqual(self.executor.queries[-1].sql, 'DELETE FROM settings WHERE key=$1')

    def test_database_errors_propagate(self):
        failure = RuntimeError('simulated database unavailable')
        self.executor.error = failure
        for operation in (lambda: self.store.get('x'), lambda: self.store.set('x', 'y'),
                          lambda: self.store.insert_if_absent('x', 'y'), lambda: self.store.delete('x')):
            with self.assertRaises(RuntimeError) as caught:
                operation()
            self.assertIs(caught.exception, failure)

    def test_malformed_rows_do_not_look_uninitialized(self):
        for rows in (None, '', {}, [None], [(None,)], [('a', 'b')], [('a',), ('b',)], [(b'bytes',)]):
            self.executor.rows = rows
            with self.subTest(rows=rows), self.assertRaises(SettingsContractError):
                self.store.get(PASSWORD_KEY)

    def test_invalid_affected_counts_fail_closed(self):
        for count in (-1, 2, None, True, '1'):
            self.executor.affected = count
            for operation in (lambda: self.store.insert_if_absent('x', 'y'),
                              lambda: self.store.set('x', 'y'), lambda: self.store.delete('x')):
                with self.subTest(count=count), self.assertRaises(SettingsContractError):
                    operation()
        self.executor.affected = 0
        with self.assertRaises(SettingsContractError):
            self.store.set('x', 'y')

    def test_types_rejected_before_execution(self):
        for operation in (lambda: self.store.get(None), lambda: self.store.set('x', None),
                          lambda: self.store.insert_if_absent(1, 'x'), lambda: self.store.delete([]),
                          lambda: self.store.set_bool('x', 1), lambda: self.store.get_bool('x', 1)):
            with self.assertRaises(TypeError):
                operation()
        self.assertEqual(self.executor.queries, [])

    def test_boolean_source_semantics(self):
        self.assertTrue(self.store.get_bool('x', True))
        for value in ('true', '1', 'false', '0', '', 'TRUE', 'yes', ' true '):
            self.executor.rows = [(value,)]
            self.assertEqual(self.store.get_bool('x', True), value in ('true', '1'))
        self.executor.error = RuntimeError('fixture failure')
        self.assertTrue(self.store.get_bool('x', True))
        self.assertFalse(self.store.get_bool('x', False))

    def test_boolean_writes_are_text(self):
        self.store.set_bool('x', True)
        self.store.set_bool('x', False)
        self.assertEqual([q.params for q in self.executor.queries], [('x', 'true'), ('x', 'false')])

    def test_sql_contract_matches_go(self):
        root = Path(__file__).resolve().parents[2]
        source = (root / 'db/settings.go').read_text()
        literals = re.findall(r'`([^`]+)`', source)
        normalize = lambda text: ' '.join(text.split())
        self.store.get('x')
        self.store.set('x', 'y')
        self.store.insert_if_absent('x', 'y')
        for query in self.executor.queries:
            self.assertIn(normalize(query.sql), [normalize(s) for s in literals])
        schema = (root / 'db/schema.sql').read_text()
        self.assertRegex(schema, r'CREATE TABLE IF NOT EXISTS settings\s*\(\s*key\s+TEXT PRIMARY KEY')

    def test_concurrent_first_admin_cannot_overwrite_winner(self):
        # Both requests read absence and reach hashing before either INSERT.
        # The test fixture simulates the SQL key constraint, not PostgreSQL locks.
        executor = AtomicInsertFixture()
        store = SettingsStore(executor)
        service = AuthService(store, TestOnlyCodec(Barrier(2)), b'x' * 32, clock=lambda: 1000)
        def initialize(password):
            request = Request('POST', '/api/auth/init', body=json.dumps({'password': password}).encode())
            return service.handle('init', request)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(initialize, 'first-password')
            second = pool.submit(initialize, 'second-password')
            statuses = [first.result(timeout=10).status, second.result(timeout=10).status]
        self.assertEqual(sorted(statuses), [200, 403])
        winner = executor.values[PASSWORD_KEY]
        self.assertIn(winner, ('TEST-ONLY:first-password', 'TEST-ONLY:second-password'))
        self.assertFalse(store.insert_if_absent(PASSWORD_KEY, 'replacement'))
        self.assertEqual(store.get(PASSWORD_KEY), winner)

    def test_auth_read_failure_cannot_initialize(self):
        self.executor.error = RuntimeError('database failure')
        service = AuthService(self.store, TestOnlyCodec(), b'x' * 32)
        response = service.handle('init', Request('POST', '/api/auth/init', body=b'{"password":"password"}'))
        self.assertEqual(response.status, 503)
        self.assertEqual(len(self.executor.queries), 1)
        self.assertTrue(self.executor.queries[0].sql.startswith('SELECT'))

    def test_empty_existing_value_is_not_overwritten_by_init(self):
        executor = AtomicInsertFixture()
        executor.values[PASSWORD_KEY] = ''
        service = AuthService(SettingsStore(executor), TestOnlyCodec(), b'x' * 32)
        response = service.handle('init', Request('POST', '/api/auth/init', body=b'{"password":"password"}'))
        self.assertEqual(response.status, 403)
        self.assertEqual(executor.values[PASSWORD_KEY], '')

    def test_auth_write_failure_returns_error_not_token(self):
        class FailingInsert(RecordingExecutor):
            def execute(self, query):
                raise RuntimeError('write failed')
        service = AuthService(SettingsStore(FailingInsert()), TestOnlyCodec(), b'x' * 32)
        response = service.handle('init', Request('POST', '/api/auth/init', body=b'{"password":"password"}'))
        self.assertEqual(response.status, 500)
        self.assertNotIn('token', response.json())


if __name__ == '__main__':
    unittest.main()

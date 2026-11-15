import io
import json
import unittest
from artex.server.application import build_application, build_persisted_application


class Executor:
    """Test-only deterministic settings map, never a production database."""
    def __init__(self):
        self.values = {}
        self.calls = []

    def fetch_all(self, query):
        self.calls.append(query)
        if 'FROM settings' in query.sql:
            value = self.values.get(query.params[0])
            return [] if value is None else [(value,)]
        if 'FROM server_logs' in query.sql:
            return []
        if 'FROM task_categories' in query.sql:
            return []
        raise AssertionError(query.sql)

    def execute(self, query):
        self.calls.append(query)
        if query.sql.startswith('INSERT INTO settings'):
            key, value = query.params
            if 'DO NOTHING' in query.sql and key in self.values:
                return 0
            self.values[key] = value
            return 1
        raise AssertionError(query.sql)


class TestCodec:
    # Explicitly injected test fake. Never used or imported by the factory.
    def hash(self, password):
        return 'test-only:' + password
    def verify(self, password, encoded):
        return encoded == self.hash(password)


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.db = Executor()
        self.app = build_application(self.db, b'x' * 32, TestCodec())

    def request(self, path, *, method='GET', data=None, token=None):
        body = b'' if data is None else json.dumps(data).encode()
        env = {'REQUEST_METHOD': method, 'PATH_INFO': path, 'QUERY_STRING': '',
               'CONTENT_LENGTH': str(len(body)), 'wsgi.input': io.BytesIO(body)}
        if token:
            env['HTTP_AUTHORIZATION'] = 'Bearer ' + token
        result = []
        response = b''.join(self.app(env, lambda s, h: result.append(s)))
        return int(result[0].split()[0]), json.loads(response)

    def test_health_auth_storage_categories_chain(self):
        self.assertEqual(self.request('/api/health')[0], 200)
        self.assertEqual(self.request('/api/task-categories')[0], 401)
        status, payload = self.request('/api/auth/init', method='POST', data={'password': 'dummy-password'})
        self.assertEqual(status, 200)
        self.assertIn('auth.password_hash', self.db.values)
        token = payload['token']
        self.assertEqual(self.request('/api/task-categories', token=token), (200, {'categories': []}))
        self.assertEqual(self.request('/api/auth/init', method='POST', data={'password': 'other-password'})[0], 403)
        self.assertEqual(self.request('/api/auth/login', method='POST', data={'username': 'ARTEX', 'password': 'dummy-password'})[0], 200)
        self.assertEqual(self.request('/api/tasks', token=token)[0], 503)
        self.assertEqual(self.request('/api/logs', token=token)[0], 503)
        self.assertEqual(self.request('/api/assets', token=token)[0], 503)
        self.assertEqual(self.request('/api/logs/history', token=token), (200, {'items': [], 'has_more': False}))

    def test_persisted_factory_loads_without_starting(self):
        class EmptyTasks(Executor):
            def fetch_all(self, query):
                if 'FROM tasks WHERE deleted_at IS NULL' in query.sql:
                    self.calls.append(query)
                    return []
                return super().fetch_all(query)
        self.db = EmptyTasks()
        self.app = build_persisted_application(self.db, b'x' * 32, TestCodec())
        self.assertTrue(any('FROM tasks' in q.sql for q in self.db.calls))
        status, payload = self.request('/api/auth/init', method='POST', data={'password': 'dummy-password'})
        self.assertEqual(status, 200)
        token = payload['token']
        self.assertEqual(self.request('/api/tasks', token=token), (200, {'tasks': [], 'active': ''}))
        count = len(self.db.calls)
        self.assertEqual(self.request('/api/tasks', method='POST', data={'description': 'fixture'}, token=token)[0], 503)
        self.assertEqual(len(self.db.calls), count)

    def test_persisted_factory_does_not_hide_read_failure(self):
        class Broken(Executor):
            def fetch_all(self, query):
                raise RuntimeError('database unavailable')
        with self.assertRaisesRegex(RuntimeError, 'database unavailable'):
            build_persisted_application(Broken(), b'x' * 32, TestCodec())

    def test_key_validation_before_use(self):
        with self.assertRaises(ValueError):
            build_application(self.db, b'short', TestCodec())
        self.assertEqual(self.db.calls, [])


if __name__ == '__main__':
    unittest.main()

import io
import json
import unittest
from artex.server.application import build_application


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
        self.assertEqual(self.request('/api/tasks', token=token)[0], 404)

    def test_key_validation_before_use(self):
        with self.assertRaises(ValueError):
            build_application(self.db, b'short', TestCodec())
        self.assertEqual(self.db.calls, [])


if __name__ == '__main__':
    unittest.main()

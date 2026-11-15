import io
import json
import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timezone
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
        self.assertEqual(self.request('/api/exploration/findings')[0], 401)
        self.assertEqual(self.request('/api/exploration/findings', token=token)[0], 503)
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

    def test_injected_findings_and_explicit_asset_resolver(self):
        from artex.db.findings import Finding
        finding = Finding(7,4,None,'CWE-79','','high','summary','proof','worker',[2],
                          'pending',datetime(2026,11,15,tzinfo=timezone.utc),'Example',1,1,0)
        store=Mock()
        store.list.return_value=[finding]
        store.get.return_value=finding
        resolver=Mock(return_value={2:{'id':2,'type':'root_domain','domain':'example.test'}})
        self.app=build_application(self.db,b'x'*32,TestCodec(),finding_store=store,finding_asset_resolver=resolver)
        self.assertEqual(self.request('/api/exploration/findings')[0],401)
        store.list.assert_not_called();resolver.assert_not_called()
        _,payload=self.request('/api/auth/init',method='POST',data={'password':'dummy-password'})
        token=payload['token']
        status,body=self.request('/api/exploration/findings',token=token)
        self.assertEqual(status,200)
        self.assertEqual(body[0]['assets'],[{'id':'2','type':'root_domain','label':'example.test'}])
        self.assertEqual(self.request('/api/exploration/findings/7',token=token)[0],200)
        self.assertEqual(self.request('/api/exploration/findings/export',token=token)[0],503)
        self.assertEqual(self.request('/api/exploration/findings/7',method='DELETE',token=token)[0],503)
        store.delete.assert_not_called()
        self.app=build_application(self.db,b'x'*32,TestCodec(),finding_store=store)
        self.assertEqual(self.request('/api/exploration/findings',token=token)[0],503)

    def test_persisted_findings_no_startup_enrichment_and_resolver_forwarding(self):
        class FindingsExecutor(Executor):
            def fetch_all(self,query):
                if 'FROM tasks WHERE deleted_at IS NULL' in query.sql:
                    self.calls.append(query);return []
                if 'FROM findings f' in query.sql:
                    self.calls.append(query)
                    return [(7,4,None,'CWE-79','','high','summary','proof','worker',[],
                             'pending',datetime(2026,11,15,tzinfo=timezone.utc),'Example',1,1,0)]
                return super().fetch_all(query)
        self.db=FindingsExecutor()
        resolver=Mock(return_value={})
        root=Mock(return_value='example.test')
        with patch('artex.db.asset_store.AssetStore') as assets:
            self.app=build_persisted_application(self.db,b'x'*32,TestCodec(),
                                               finding_asset_resolver=resolver,root_domain_resolver=root)
            assets.assert_called_once_with(self.db,allow_global=False,root_domain_resolver=root)
        resolver.assert_not_called();root.assert_not_called()
        self.assertEqual(len(self.db.calls),1)  # load existing tasks only, never start
        _,payload=self.request('/api/auth/init',method='POST',data={'password':'dummy-password'})
        response=self.request('/api/exploration/findings',token=payload['token'])
        self.assertEqual(response[0],200)
        self.assertEqual(response[1][0]['finding_id'],'7')
        resolver.assert_not_called()  # no anchored assets, no guessed/global lookup

    def test_key_validation_before_use(self):
        with self.assertRaises(ValueError):
            build_application(self.db, b'short', TestCodec())
        self.assertEqual(self.db.calls, [])


if __name__ == '__main__':
    unittest.main()

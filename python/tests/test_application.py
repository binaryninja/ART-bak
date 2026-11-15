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
        self.assertEqual(self.request('/api/llm/profiles')[0], 401)
        self.assertEqual(self.request('/api/llm/profiles', token=token)[0], 503)
        self.assertEqual(self.request('/api/llm', token=token)[0], 503)
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

    def test_exploration_requires_explicit_resolver_and_auth(self):
        from artex.server import sign_jwt
        token=sign_jwt(b'x'*32)
        self.assertEqual(self.db.calls,[])
        for path in ('graph','frontier','intents','nodes'):
            self.assertEqual(self.request('/api/exploration/'+path)[0],401)
            self.assertEqual(self.request('/api/exploration/'+path,token=token)[0],503)
        self.assertEqual(self.db.calls,[])

    def test_exploration_injected_graph_no_startup_reads(self):
        from artex.server import sign_jwt
        store=Mock(read_only=False)
        store.task_id.return_value=1
        store.nodes.return_value=[];store.edges.return_value=[];store.direct_sources.return_value=[]
        resolver=Mock(return_value=store);active=Mock(return_value='1')
        self.app=build_application(self.db,b'x'*32,TestCodec(),
                                  exploration_store_resolver=resolver,active_task_id=active)
        resolver.assert_not_called();active.assert_not_called()
        self.assertEqual(self.db.calls,[])
        self.assertEqual(self.request('/api/exploration/graph')[0],401)
        resolver.assert_not_called();active.assert_not_called()
        token=sign_jwt(b'x'*32)
        self.assertEqual(self.request('/api/exploration/graph',token=token),(200,{'nodes':[],'edges':[]}))
        resolver.assert_called_once_with('1');active.assert_called_once_with()
        self.app=build_application(self.db,b'x'*32,TestCodec(),exploration_store_resolver=resolver)
        self.assertEqual(self.request('/api/exploration/graph',token=token)[0],503)
        self.assertEqual(resolver.call_count,1)  # never infer active selection

    def test_persisted_exploration_callbacks_forwarded_without_call(self):
        class EmptyTasks(Executor):
            def fetch_all(self,query):
                if 'FROM tasks WHERE deleted_at IS NULL' in query.sql:
                    self.calls.append(query);return []
                return super().fetch_all(query)
        db=EmptyTasks();resolver=Mock();active=Mock();board=Mock();asset_labels=Mock()
        with patch('artex.server.application.ExplorationService') as service:
            build_persisted_application(db,b'x'*32,TestCodec(),
                                        exploration_store_resolver=resolver,active_task_id=active,
                                        exploration_nodeboard_factory=board,exploration_nodeboard_asset_resolver=asset_labels)
            service.assert_called_once_with(resolver,active_task_id=active,nodeboard_factory=board,nodeboard_asset_resolver=asset_labels)
            service.return_value.register.assert_called_once()
        resolver.assert_not_called();active.assert_not_called();board.assert_not_called();asset_labels.assert_not_called()
        self.assertEqual(len(db.calls),1)  # existing task registry load only

    def test_profile_factory_injection_no_runtime_substitution(self):
        from artex.db.profiles import LLMProfile
        store=Mock()
        store.list.return_value=[LLMProfile(id=4,name='Example',model='dummy',api_key='dummy-secret')]
        coordinator=Mock();coordinator.save.return_value=4
        self.app=build_application(self.db,b'x'*32,TestCodec(),profile_store=store,profile_coordinator=coordinator)
        self.assertEqual(self.request('/api/llm/profiles')[0],401)
        store.list.assert_not_called()
        _,payload=self.request('/api/auth/init',method='POST',data={'password':'dummy-password'})
        token=payload['token']
        status,body=self.request('/api/llm/profiles',token=token)
        self.assertEqual(status,200);self.assertEqual(body['profiles'][0]['id'],'4')
        self.assertNotIn('api_key',body['profiles'][0])
        self.assertEqual(self.request('/api/llm',token=token)[0],503)
        store.active.assert_not_called()
        self.assertEqual(self.request('/api/llm/profiles',method='POST',data={'name':'Example','model':'dummy'},token=token),
                         (200,{'id':4}))
        self.assertEqual(self.request('/api/llm/profiles/active',method='POST',data={'id':4},token=token),(200,{'ok':True}))
        coordinator.set_active.assert_called_once_with(4)
        self.assertEqual(self.request('/api/llm/test',method='POST',token=token)[0],503)
        self.assertEqual(self.request('/api/llm/profiles/4',method='DELETE',token=token)[0],503)

    def test_persisted_profile_adapters_forwarded_without_startup_reads(self):
        class EmptyTasks(Executor):
            def fetch_all(self,query):
                if 'FROM tasks WHERE deleted_at IS NULL' in query.sql:
                    self.calls.append(query);return []
                return super().fetch_all(query)
        self.db=EmptyTasks();coordinator=Mock();snapshot=Mock()
        with patch('artex.db.profiles.ProfileStore') as profile_store, patch('artex.server.application.ProfileService') as service:
            self.app=build_persisted_application(self.db,b'x'*32,TestCodec(),profile_coordinator=coordinator,runtime_snapshot=snapshot)
            profile_store.assert_called_once_with(self.db)
            service.assert_called_once_with(profile_store.return_value,coordinator=coordinator,runtime_snapshot=snapshot)
            service.return_value.register.assert_called_once()
            profile_store.return_value.list.assert_not_called()
            profile_store.return_value.active.assert_not_called()
        snapshot.assert_not_called();self.assertEqual(coordinator.mock_calls,[])
        self.assertEqual(len(self.db.calls),1)

    def test_nodeboard_explicit_scoped_adapters_and_auth(self):
        from artex.server import sign_jwt
        from artex.db.exploration import Node
        store=Mock(read_only=False,exploration_id=9)
        store.task_id.return_value=1
        board=Mock(read_only=False,exploration_id=9)
        node=Node(7,'fact',{},1,'done','','','','',datetime(2026,11,15,tzinfo=timezone.utc))
        board.nodes_page.return_value=([node],1)
        board.edges_touching.return_value=[];board.nodes_by_ids.return_value=[]
        board.node_assets.return_value={7:[2]}
        factory=Mock(return_value=board)
        labels=Mock(return_value={2:{'id':2,'type':'root_domain','domain':'example.test'}})
        resolver=Mock(return_value=store);active=Mock(return_value='1')
        self.app=build_application(self.db,b'x'*32,TestCodec(),exploration_store_resolver=resolver,
            active_task_id=active,exploration_nodeboard_factory=factory,exploration_nodeboard_asset_resolver=labels)
        resolver.assert_not_called();factory.assert_not_called();labels.assert_not_called()
        self.assertEqual(self.request('/api/exploration/nodes')[0],401)
        resolver.assert_not_called();factory.assert_not_called();labels.assert_not_called()
        token=sign_jwt(b'x'*32)
        status,body=self.request('/api/exploration/nodes',token=token)
        self.assertEqual(status,200)
        self.assertEqual(body['assets'],{'7':[{'id':'2','type':'root_domain','label':'example.test'}]})
        factory.assert_called_once_with(store);labels.assert_called_once_with(store,[2])
        # Same authorized selection without capability adapters must not infer a
        # nodeboard or globally scoped asset lookup.
        self.app=build_application(self.db,b'x'*32,TestCodec(),exploration_store_resolver=resolver,active_task_id=active)
        self.assertEqual(self.request('/api/exploration/nodes',token=token)[0],503)
        self.assertEqual(factory.call_count,1);self.assertEqual(labels.call_count,1)

    def test_company_auth_missing_empty_and_fake_create(self):
        from artex.server import sign_jwt
        token=sign_jwt(b'x'*32)
        self.assertEqual(self.request('/api/companies')[0],401)
        self.assertEqual(self.request('/api/companies',token=token)[0],503)
        store=Mock();store.list.return_value=[];store.create.return_value=3
        self.app=build_application(self.db,b'x'*32,TestCodec(),company_store=store)
        self.assertEqual(store.mock_calls,[])
        self.assertEqual(self.request('/api/companies')[0],401)
        store.list.assert_not_called()
        self.assertEqual(self.request('/api/companies',token=token),(200,None))
        response=self.request('/api/companies',method='POST',data={'name':'Example'},token=token)
        self.assertEqual(response,(201,{'id':3,'created':True,'scope_added':0,'scope_skipped':0,'scope_invalid':0}))
        store.create.assert_called_once_with('Example','',scope_inputs=[])
        self.assertEqual(self.request('/api/companies/3',method='DELETE',token=token)[0],503)
        self.assertEqual(self.request('/api/companies/3/scope',method='POST',data={'scope':['example.test']},token=token)[0],503)
        store.delete.assert_not_called();store.add_scope.assert_not_called()

    def test_persisted_company_admin_repository_no_startup_reads(self):
        from artex.server import sign_jwt
        class EmptyTasksCompanies(Executor):
            def fetch_all(self,query):
                if 'FROM tasks WHERE deleted_at IS NULL' in query.sql or 'FROM companies c' in query.sql:
                    self.calls.append(query);return []
                return super().fetch_all(query)
        db=EmptyTasksCompanies()
        self.app=build_persisted_application(db,b'x'*32,TestCodec())
        self.assertEqual(len(db.calls),1)
        self.assertNotIn('companies',db.calls[0].sql)
        self.assertEqual(self.request('/api/companies',token=sign_jwt(b'x'*32)),(200,None))
        self.assertIn('FROM companies c',db.calls[-1].sql)
        self.assertEqual(db.calls[-1].params,())  # explicit trusted administrator store

    def test_template_optional_store_auth_and_no_startup_reads(self):
        from artex.server import sign_jwt
        from artex.db.task_templates import TaskTemplateStore
        token=sign_jwt(b'x'*32)
        self.assertEqual(self.request('/api/task-templates')[0],401)
        self.assertEqual(self.request('/api/task-templates',token=token)[0],503)
        self.assertEqual(self.db.calls,[])
        class Templates(Executor):
            def fetch_all(self,query):
                if 'FROM task_templates' in query.sql:
                    self.calls.append(query);return []
                if query.sql.startswith('INSERT INTO task_templates'):
                    self.calls.append(query)
                    return [(7,'Preset','preset','description','goal',None,'[]',datetime(2026,11,15,tzinfo=timezone.utc),datetime(2026,11,15,tzinfo=timezone.utc))]
                return super().fetch_all(query)
        self.db=Templates()
        self.app=build_application(self.db,b'x'*32,TestCodec(),template_store=TaskTemplateStore(self.db))
        self.assertEqual(self.db.calls,[])
        self.assertEqual(self.request('/api/task-templates')[0],401)
        self.assertEqual(self.db.calls,[])
        self.assertEqual(self.request('/api/task-templates',token=token),(200,{'templates':[]}))
        status,data=self.request('/api/task-templates',method='POST',token=token,
                                 data={'name':'Preset','description':'description','goal':'goal'})
        self.assertEqual(status,201);self.assertEqual(data['id'],7)
        self.assertEqual(self.db.calls[-1].params,('Preset','preset','description','goal',None,'[]'))
        self.assertFalse(any('INSERT INTO tasks' in q.sql for q in self.db.calls))
        self.assertEqual(self.request('/api/tasks',token=token)[0],503)

    def test_persisted_template_store_no_startup_template_reads(self):
        from artex.server import sign_jwt
        class Empty(Executor):
            def fetch_all(self,query):
                if 'FROM tasks WHERE deleted_at IS NULL' in query.sql or 'FROM task_templates' in query.sql:
                    self.calls.append(query);return []
                return super().fetch_all(query)
        self.db=Empty()
        self.app=build_persisted_application(self.db,b'x'*32,TestCodec())
        self.assertEqual(len(self.db.calls),1)
        self.assertNotIn('task_templates',self.db.calls[0].sql)
        self.assertEqual(self.request('/api/task-templates')[0],401)
        self.assertEqual(len(self.db.calls),1)
        self.assertEqual(self.request('/api/task-templates',token=sign_jwt(b'x'*32)),(200,{'templates':[]}))
        self.assertEqual(len(self.db.calls),2)

    def test_key_validation_before_use(self):
        with self.assertRaises(ValueError):
            build_application(self.db, b'short', TestCodec())
        self.assertEqual(self.db.calls, [])


if __name__ == '__main__':
    unittest.main()

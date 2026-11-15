"""Asset Router contracts: fake executors/adapters only, no target interactions."""
from contextlib import contextmanager
import json
import unittest
from urllib.parse import urlencode
from artex.server import AuthService, Request, sign_jwt
from artex.server.assets import AssetService, asset_dto, MAX_REQUEST_BYTES
from artex.db.asset_store import AssetStore, AssetInvalid, AssetUnavailable, AssetUnsupported


ASSET = {'id': 8, 'type': 'root_domain', 'task_ids': [2], 'last_seen': '2026-11-15 00:00:00+00',
         'domain': 'example.test', 'root_domain': 'example.test', 'company_id': None,
         'port': None, 'page_title': '', 'bound_domains': [], 'extra': {}}


class Store:
    def __init__(self):
        self.calls = []
        self.total = 1
        self.unavailable = False
        self.error = None
    def count(self, **params):
        self.calls.append(('count', params))
        if self.error: raise self.error
        return self.total
    def query(self, **params):
        self.calls.append(('query', params))
        return [dict(ASSET)]
    def counts_by_type(self, **params):
        self.calls.append(('counts', params))
        return {'root_domain': 1, 'ip': 3}
    def check_mutation_available(self):
        self.calls.append(('preflight',))
        if self.unavailable: raise AssetUnavailable('missing resolver')
    def validate_upsert(self, asset, task_id=0):
        if asset.get('domain') == '': raise AssetInvalid('domain required')
        return dict(asset)
    def upsert(self, asset, task_id=0):
        self.calls.append(('upsert', dict(asset), task_id))
        if self.error: raise self.error
        if asset.get('domain') == '': raise AssetInvalid('domain required')
        return 8


class UnusedStore:
    def get(self, key): raise AssertionError('No credentials accessed')


class UnusedCodec:
    def hash(self, password): raise AssertionError('No credentials written')
    def verify(self, password, encoded): raise AssertionError('No password verification')


class AssetHTTPTests(unittest.TestCase):
    def setUp(self):
        self.key = b'k'*32
        self.auth = AuthService(UnusedStore(), UnusedCodec(), self.key, clock=lambda: 1000)
        self.router = self.auth.router()
        self.store = Store()
        AssetService(self.store).register(self.router)
        self.token = sign_jwt(self.key, now=1000)

    def request(self, method='GET', path='/api/assets', data=None, *, body=None, token=True):
        return self.router.dispatch(Request(method, path,
            {'Authorization': 'Bearer '+self.token} if token else {},
            body if body is not None else json.dumps(data or {}).encode()))

    def test_all_routes_require_auth_before_store_access(self):
        for method,path in [('GET','/api/assets'),('GET','/api/assets/counts'),('POST','/api/assets'),('DELETE','/api/assets')]:
            self.assertEqual(self.request(method,path,token=False).status, 401)
        self.assertEqual(self.store.calls, [])

    def test_global_list_defaults_and_exact_envelope(self):
        response = self.request()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.json(), {'count': 1, 'total': 1, 'assets': [asset_dto(ASSET)]})
        self.assertEqual(self.store.calls[0], ('count', {'dsl':'','typ':'root_domain','task_id':0,'company_id':None}))
        self.assertEqual(self.store.calls[1][1]['limit'],50)
        self.assertEqual(self.store.calls[1][1]['offset'],0)
        self.assertEqual(response.headers['Content-Type'],'application/json')

    def test_pagination_clamps_and_count_before_page(self):
        self.request(path='/api/assets?limit=999&offset=-2')
        self.assertEqual(self.store.calls[-1][1]['limit'],200)
        self.assertEqual(self.store.calls[-1][1]['offset'],0)
        self.store.calls.clear()
        response=self.request(path='/api/assets?offset=1')
        self.assertEqual(response.json(), {'count':0,'total':1,'assets':[]})
        self.assertEqual(len(self.store.calls),1)
        self.store.calls.clear()
        self.request(path='/api/assets?limit=0')
        self.assertEqual(self.store.calls[-1][1]['limit'],50)

    def test_task_company_and_dsl_filters_remain_explicit(self):
        self.request(path='/api/assets?task_id=2&type=ip')
        self.assertEqual(self.store.calls[-1][1]['task_id'],2)
        self.assertEqual(self.store.calls[-1][1]['typ'],'ip')
        self.request(path='/api/assets?'+urlencode({'company_id':3,'dsl':'domain=example.test OR ip=127.0.0.1'}))
        args=self.store.calls[-1][1]
        self.assertEqual(args['company_id'],3)
        self.assertEqual(args['dsl'],'domain=example.test OR ip=127.0.0.1')
        self.assertEqual(args['typ'],'')

    def test_invalid_scope_never_becomes_global(self):
        for query in ('task_id=', 'task_id=abc','task_id=0','task_id=-1','task_id=9223372036854775808',
                      'company_id=0','company_id=no','task_id=1&company_id=2','task_id=1&task_id=2',
                      'in_scope=true','scope=2','task_ids=1,2','company_ids=1,2'):
            self.assertEqual(self.request(path='/api/assets?'+query).status,400,query)
        self.assertEqual(self.store.calls,[])

    def test_invalid_query_values_fail_before_database(self):
        for query in ('limit=x','offset=x','type=unknown','type=ip&type=root_domain','dsl=%28', '&'.join('q=x' for _ in range(101))):
            self.assertEqual(self.request(path='/api/assets?'+query).status,400,query)
        self.assertEqual(self.store.calls,[])

    def test_counts_response_and_scope(self):
        response=self.request(path='/api/assets/counts?task_id=2')
        self.assertEqual(response.json(),{'root_domain':1,'ip':3})
        self.assertEqual(self.store.calls[-1],('counts',{'task_id':2}))
        for query in ('company_id=3','dsl=example','type=ip','limit=5','task_id=no'):
            self.assertEqual(self.request(path='/api/assets/counts?'+query).status,400)

    def test_store_failure_is_not_validation_error_or_leak(self):
        self.store.error=RuntimeError('postgres password secret')
        result=self.request()
        self.assertEqual(result.status,500)
        self.assertNotIn(b'secret',result.body)
        self.store.error=AssetUnavailable('no scope')
        self.assertEqual(self.request().status,503)

    def test_delete_and_invented_routes_not_enabled(self):
        self.assertEqual(self.request('DELETE').status,503)
        self.assertEqual(self.request('GET','/api/assets/8').status,404)
        self.assertEqual(self.request('PATCH','/api/assets/8',{}).status,404)
        self.assertEqual(self.store.calls,[])

    def test_asset_json_omits_empty_optional_fields_but_retains_zero_pointers(self):
        data=asset_dto({**ASSET,'status_code':0,'content_length':0,'port':0,'unexpected':'private'})
        for field in ('company_id','page_title','bound_domains','extra','unexpected'):
            self.assertNotIn(field,data)
        for field in ('status_code','content_length','port'):
            self.assertEqual(data[field],0)
        self.assertEqual(data['task_ids'],[2])

    def test_batch_upsert_updates_same_route_and_preserves_task_id(self):
        response=self.request('POST',data={'task_id':2,'assets':[{'type':'root_domain','domain':'example.test','icp':'ref'},
            {'type':'ip','ip':'192.0.2.1','bound_domains':['example.test'],'open_ports':[{'port':443,'service':'https'}]}]})
        self.assertEqual(response.status,200)
        self.assertEqual(response.json(),{'results':[{'index':0,'id':8,'type':'root_domain'},{'index':1,'id':8,'type':'ip'}],'errors':None})
        self.assertEqual(self.store.calls[0],('preflight',))
        self.assertEqual([c[2] for c in self.store.calls if c[0]=='upsert'],[2,2])

    def test_upsert_unknown_type_and_validation_errors_are_per_item(self):
        result=self.request('POST',data={'assets':[{'type':'unknown'},{'type':'root_domain','domain':''},{'type':'root_domain','domain':'valid.test'}]})
        self.assertEqual(result.status,200)
        self.assertEqual(result.json()['results'],[{'index':2,'id':8,'type':'root_domain'}])
        self.assertEqual([x['index'] for x in result.json()['errors']],[0,1])

    def test_known_unported_type_preflights_entire_batch(self):
        for typ in ('subdomain','app','service','endpoint'):
            result=self.request('POST',data={'assets':[{'type':'root_domain','domain':'valid.test'},{'type':typ}]})
            self.assertEqual(result.status,503)
        self.assertEqual(self.store.calls,[])

    def test_missing_mutation_dependencies_fail_before_any_write(self):
        self.store.unavailable=True
        result=self.request('POST',data={'assets':[{'type':'ip','ip':'192.0.2.1'}]})
        self.assertEqual(result.status,503)
        self.assertEqual(self.store.calls,[('preflight',)])

    def test_unknown_mutation_metadata_is_not_silently_discarded(self):
        response=self.request('POST',data={'assets':[{'type':'root_domain','domain':'one.test'},
            {'type':'root_domain','domain':'two.test','extra':{'important':'metadata'}}]})
        self.assertEqual(response.status,503)
        self.assertEqual(self.store.calls,[])

    def test_empty_batch_matches_upstream_nil_slice_shape(self):
        for data in ({},{'assets':None},{'assets':[]}):
            self.assertEqual(self.request('POST',data=data).json(),{'results':None,'errors':None})
        self.assertEqual(self.store.calls,[])

    def test_insertion_scope_fields_are_never_silently_dropped(self):
        for data in ({'company_id':2},{'task_id':-1},{'task_id':'2'}, {'task_id':True},
                     {'assets':[{'type':'ip','ip':'192.0.2.1','company_id':2}]},
                     {'assets':[{'type':'root_domain','domain':'example.test','task_id':2}]}):
            self.assertEqual(self.request('POST',data=data).status,400)
        self.assertEqual(self.store.calls,[])

    def test_json_types_and_body_limits(self):
        for data in ({'assets':'bad'},{'assets':[1]},{'assets':[{'type':7}]},
                     {'assets':[{'type':'ip','bound_domains':[3]}]},
                     {'assets':[{'type':'ip','open_ports':[{'port':True}]}]},
                     {'assets':[{'type':'ip','open_ports':[{'port':2,'service':False}]}]}):
            self.assertEqual(self.request('POST',data=data).status,400)
        for body in (b'[]',b'{',b'{}{}',b'{"task_id":NaN}',b'{"assets":[{"domain":"\\ud800"}]}','{}'.encode('utf-16')):
            self.assertEqual(self.request('POST',body=body).status,400)
        self.assertEqual(self.request('POST',body=b' '*(MAX_REQUEST_BYTES+1)).status,413)
        self.assertEqual(self.store.calls,[])

    def test_real_store_task_query_preserves_membership_and_provenance(self):
        from artex.db.assets import _COLUMNS
        class Executor:
            def __init__(self): self.calls=[]
            def fetch_one(self,query):
                self.calls.append(query)
                return (1,)
            def fetch_all(self,query):
                self.calls.append(query)
                if 'FROM task_asset_links' in query.sql:
                    return [(8,'api','Registered via asset API',None)]
                values={name:'' for name in _COLUMNS}
                values.update(ASSET)
                for name in ('port','status_code','content_length','company_id'): values[name]=None
                for name in ('bound_domains','open_ports','record_value','technologies','auth','params'): values[name]=[]
                values['extra']={}
                return [tuple(values[name] for name in _COLUMNS)]
        executor=Executor()
        router=self.auth.router()
        AssetService(AssetStore(executor)).register(router)
        path='/api/assets?'+urlencode({'task_id':2,'dsl':'domain=example.test OR ip=127.0.0.1'})
        result=router.dispatch(Request('GET',path,{'Authorization':'Bearer '+self.token}))
        self.assertEqual(result.status,200)
        asset=result.json()['assets'][0]
        self.assertEqual(asset['task_source'],'api')
        self.assertEqual(asset['task_source_summary'],'Registered via asset API')
        self.assertEqual(len(executor.calls),3)
        for query in executor.calls[:2]:
            self.assertIn('ANY(task_ids)',query.sql)
            self.assertIn(') AND ',query.sql)
            self.assertIn(2,query.params)
            self.assertNotIn('example.test',query.sql)
        self.assertEqual(executor.calls[-1].params,(2,'{8}'))
        before=len(executor.calls)
        result=router.dispatch(Request('GET','/api/assets',{'Authorization':'Bearer '+self.token}))
        self.assertEqual(result.status,503)  # Global reads were not explicitly enabled.
        self.assertEqual(len(executor.calls),before)

    def test_real_store_transactional_upsert_and_provenance(self):
        class Executor:
            def __init__(self): self.events=[]; self.fail_provenance=False
            @contextmanager
            def transaction(self):
                self.events.append('begin')
                try: yield self
                except Exception:
                    self.events.append('rollback')
                    raise
                else: self.events.append('commit')
            def execute(self,query):
                self.events.append(query)
                if 'task_asset_links' in query.sql and self.fail_provenance:
                    raise RuntimeError('private database details')
                return 1
            def fetch_one(self,query):
                self.events.append(query)
                return (8,)
        executor=Executor()
        def guard(tx,asset,task_id):
            self.assertIs(tx,executor)
            self.assertEqual(task_id,2)
            executor.events.append('guard')
        def resolver(tx,domain,ip,icp):
            self.assertIs(tx,executor)
            executor.events.append('resolve')
            return 3
        router=self.auth.router()
        AssetService(AssetStore(executor,company_resolver=resolver,mutation_guard=guard)).register(router)
        request=Request('POST','/api/assets',{'Authorization':'Bearer '+self.token},
                        b'{"task_id":2,"assets":[{"type":"root_domain","domain":"Example.TEST."}]}')
        response=router.dispatch(request)
        self.assertEqual(response.json(),{'results':[{'index':0,'id':8,'type':'root_domain'}],'errors':None})
        self.assertEqual(executor.events[0],'begin')
        self.assertEqual(executor.events[-1],'commit')
        self.assertLess(executor.events.index('guard'),executor.events.index('resolve'))
        sql=[x for x in executor.events if hasattr(x,'sql')]
        self.assertIn('pg_advisory_xact_lock',sql[0].sql)
        self.assertIn('INSERT INTO assets',sql[1].sql)
        self.assertNotIn('example.test',sql[1].sql)
        self.assertEqual(sql[1].params,('example.test',None,3,'{2}'))
        self.assertIn('task_asset_links',sql[2].sql)
        self.assertEqual(sql[2].params,(2,8,'api','Registered via asset API',None))
        executor.events.clear()
        executor.fail_provenance=True
        response=router.dispatch(request)
        self.assertIsNone(response.json()['results'])
        self.assertEqual(response.json()['errors'][0]['index'],0)
        self.assertEqual(executor.events[-1],'rollback')
        self.assertNotIn(b'private',response.body)

    def test_real_store_unsupported_metadata_batch_fails_before_transaction(self):
        class Executor:
            def transaction(self): raise AssertionError('No transaction may start')
        store=AssetStore(Executor(),allow_global=True,company_resolver=lambda *a:None,mutation_guard=lambda *a:None)
        router=self.auth.router()
        AssetService(store).register(router)
        body=json.dumps({'assets':[{'type':'root_domain','domain':'one.test'},
                                  {'type':'root_domain','domain':'two.test','url':'https://two.test'}]}).encode()
        response=router.dispatch(Request('POST','/api/assets',{'Authorization':'Bearer '+self.token},body))
        self.assertEqual(response.status,503)

    def test_missing_store_503_and_auth_precedes_availability(self):
        router=self.auth.router()
        AssetService(None).register(router)
        self.assertEqual(router.dispatch(Request('GET','/api/assets')).status,401)
        self.assertEqual(router.dispatch(Request('GET','/api/assets',{'Authorization':'Bearer '+self.token})).status,503)


if __name__=='__main__':
    unittest.main()

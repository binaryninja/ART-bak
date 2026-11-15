"""Offline profile HTTP contracts; all credentials here are dummy fixtures."""
import json
import unittest
from unittest.mock import Mock
from contextlib import contextmanager
from artex.server.core import Router,Request
from artex.server.profiles import ProfileService,ProfileApplyPending,MAX_BODY
from artex.db.profiles import LLMProfile,ProfileStore


class ProfileHTTPTests(unittest.TestCase):
    def setUp(self):
        self.store=Mock();self.coordinator=Mock()
        self.coordinator.save.return_value=4
        self.store.list.return_value=[LLMProfile(id=4,name='Example',model='dummy-model',api_key='dummy-secret',
            api_key_hint='…cret',base_url='https://user:password@example.invalid/v1?key=dummy#token',streaming=False)]
        self.router=Router(lambda r:r.header('Authorization')=='dummy')
        ProfileService(self.store,coordinator=self.coordinator).register(self.router)

    def call(self,path='/api/llm/profiles',method='GET',data=None,auth=True):
        body=json.dumps(data).encode() if isinstance(data,dict) else (data or b'')
        return self.router.dispatch(Request(method,path,{'Authorization':'dummy'} if auth else {},body))

    def test_auth_before_dependencies(self):
        for method,path in [('GET','/api/llm/profiles'),('POST','/api/llm/profiles'),('POST','/api/llm/profiles/active'),
                            ('DELETE','/api/llm/profiles/4'),('GET','/api/llm'),('POST','/api/llm/test')]:
            self.assertEqual(self.call(path,method,auth=False).status,401)
        self.assertEqual(self.store.mock_calls,[]);self.assertEqual(self.coordinator.mock_calls,[])

    def test_list_source_dto_and_redaction(self):
        r=self.call();self.assertEqual(r.status,200)
        p=r.json()['profiles'][0]
        self.assertEqual(p['id'],'4');self.assertFalse(p['streaming'])
        self.assertNotIn('api_key',p);self.assertNotIn('proxy',p)
        for secret in (b'dummy-secret',b'password',b'key=dummy',b'#token'):self.assertNotIn(secret,r.body)
        self.assertIn('[redacted]',p['base_url'])

    def test_no_invented_get_byid(self):
        self.assertEqual(self.call('/api/llm/profiles/4').status,405)
        self.assertEqual(self.call('/api/llm/profiles/active').status,405)
        self.store.get.assert_not_called()

    def test_save_defaults_and_source_normalization(self):
        r=self.call(method='POST',data={'name':'Example','model':'dummy','max_tokens':-2,'max_tokens_field':'unknown'})
        self.assertEqual(r.json(),{'id':4})
        p=self.coordinator.save.call_args.args[0]
        self.assertTrue(p.streaming);self.assertEqual(p.api_key,'');self.assertEqual(p.max_tokens,0)
        self.assertEqual(p.max_tokens_field,'')
        self.store.save.assert_not_called()

    def test_blank_key_preservation(self):
        for key in ('',' \t'):
            self.call(method='POST',data={'id':4,'name':'Example','model':'dummy','api_key':key})
            self.assertEqual(self.coordinator.save.call_args.args[0].api_key,'')

    def test_public_placeholders_rejected_before_coordinator(self):
        for extra in ({'api_key':'…abcd'},{'api_key':'[redacted]'},
                      {'base_url':'https://example.invalid/?[redacted]'},
                      {'proxy':'http://%5Bredacted%5D@example.invalid'}, {'api_key':None}):
            self.assertEqual(self.call(method='POST',data={'name':'Example','model':'dummy',**extra}).status,400)
        self.coordinator.save.assert_not_called()

    def test_types_duplicates_bounds_no_side_effects(self):
        for data in (b'{"name":"a","name":"b"}',b'[]',b'{"id":NaN}',
                     {'name':'Example','model':'dummy','streaming':1}, {'id':True},
                     {'name':'Example','model':'dummy','retry':{'connect':{'attempts':True}}},
                     {'name':'Example','model':'dummy','unknown':'secret'}):
            self.assertEqual(self.call(method='POST',data=data).status,400)
        self.assertEqual(self.call(method='POST',data=b' '*(MAX_BODY+1)).status,413)
        self.coordinator.save.assert_not_called()

    def test_active_and_unavailable(self):
        self.assertEqual(self.call('/api/llm/profiles/active','POST',{'id':4}).json(),{'ok':True})
        self.coordinator.set_active.assert_called_once_with(4)
        for ident in (0,-1,'4',True,None):
            self.assertEqual(self.call('/api/llm/profiles/active','POST',{'id':ident}).status,400)
        for method,path in [('DELETE','/api/llm/profiles/4'),('POST','/api/llm/test'),('POST','/api/llm/models'),('GET','/api/llm')]:
            self.assertEqual(self.call(path,method).status,503)

    def test_missing_coordinator_no_persistence(self):
        self.router=Router(lambda _:True);ProfileService(self.store).register(self.router)
        self.assertEqual(self.call(method='POST',data={'name':'Example','model':'dummy'}).status,503)
        self.assertEqual(self.call('/api/llm/profiles/active','POST',{'id':4}).status,503)
        self.store.save.assert_not_called();self.store.set_active.assert_not_called()

    def test_pending_apply_reported_not_hidden(self):
        self.coordinator.save.side_effect=ProfileApplyPending('secret')
        r=self.call(method='POST',data={'name':'Example','model':'dummy'})
        self.assertEqual(r.status,503);self.assertTrue(r.json()['persisted']);self.assertFalse(r.json()['runtime_applied'])
        self.assertNotIn(b'secret',r.body)

    def test_coordinator_result_validation_redacts_adapter_errors(self):
        for value in (None,True,'dummy-secret',{'api_key':'dummy-secret'},0,-1,2**63):
            self.coordinator.save.return_value=value
            response=self.call(method='POST',data={'name':'Example','model':'dummy'})
            self.assertEqual(response.status,500)
            self.assertNotIn(b'dummy-secret',response.body)

    def test_runtime_snapshot_projection(self):
        snapshot=dict(configured=True,provider='openai',model='dummy',base_url='https://example.invalid?secret',proxy='',
                      key_set=True,rate_per_second=0,rate_per_minute=0,context_window_k=100,thinking_type='',reasoning_effort='',
                      api_key='dummy-secret')
        self.router=Router(lambda _:True)
        ProfileService(self.store,runtime_snapshot=lambda:snapshot).register(self.router)
        r=self.call('/api/llm');self.assertEqual(r.status,200);self.assertNotIn(b'secret',r.body)
        snapshot['model']={'private':'dummy-secret'}
        self.assertEqual(self.call('/api/llm').status,500)

    def test_real_store_blank_key_sql_preserved_via_explicit_coordinator(self):
        class Executor:
            def __init__(self):self.queries=[]
            @contextmanager
            def transaction(self):yield self
            def execute(self,q):self.queries.append(q);return 1
        executor=Executor();store=ProfileStore(executor)
        events=[]
        class Coordinator:
            def save(self,p):
                events.append('preflight')
                identifier=store.save(p)
                events.append('invalidate-and-apply')
                return identifier
        self.router=Router(lambda _:True)
        ProfileService(store,coordinator=Coordinator()).register(self.router)
        r=self.call(method='POST',data={'id':4,'name':'Example','model':'dummy','api_key':''})
        self.assertEqual(r.json(),{'id':4})
        self.assertEqual(events,['preflight','invalidate-and-apply'])
        self.assertNotIn('api_key=',executor.queries[0].sql)
        self.assertEqual(executor.queries[0].params[-1],4)


if __name__=='__main__':unittest.main()

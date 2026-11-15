"""Offline company HTTP/store contracts, no network or live DB."""
import json
from contextlib import contextmanager
import unittest
from unittest.mock import Mock
from artex.server.core import Router,Request
from artex.server.companies import CompanyService,MAX_BODY
from artex.db.companies import CompanyStore,Company,CompanyWithScope,ScopeRule,CompanyNameConflict

ROW=(3,'Example','example',None,'created','updated')


class CompanyHTTPTests(unittest.TestCase):
    def setUp(self):
        self.store=Mock()
        self.store.list.return_value=[CompanyWithScope(*ROW,asset_count=2)]
        self.store.get.return_value=Company(*ROW)
        self.store.get_scope.return_value=[ScopeRule(8,3,'domain','example.test','','','example.test','api')]
        self.store.create.return_value=3
        self.router=Router(lambda req:req.header('Authorization')=='dummy')
        CompanyService(self.store).register(self.router)

    def call(self,path='',method='GET',data=None,auth=True):
        body=json.dumps(data).encode() if isinstance(data,dict) else data or b''
        return self.router.dispatch(Request(method,'/api/companies'+path,{'Authorization':'dummy'} if auth else {},body))

    def test_auth_before_store_or_unavailable(self):
        for method,path in [('GET',''),('POST',''),('GET','/3'),('DELETE','/3'),('POST','/3/scope'),('POST','/reattribute')]:
            self.assertEqual(self.call(path,method,auth=False).status,401)
        self.assertEqual(self.store.mock_calls,[])

    def test_missing_store_stays_protected(self):
        self.router=Router(lambda req:req.header('Authorization')=='dummy')
        CompanyService(None).register(self.router)
        self.assertEqual(self.call(auth=False).status,401)
        self.assertEqual(self.call().status,503)

    def test_list_source_dto_and_nil_slice(self):
        r=self.call();self.assertEqual(r.status,200)
        self.assertEqual(r.json()[0],dict(id=3,name='Example',nkey='example',created_at='created',updated_at='updated',asset_count=2,scope=[]))
        self.store.list.return_value=[]
        self.assertIsNone(self.call().json())

    def test_get_envelope_and_not_found(self):
        response=self.call('/3')
        self.assertEqual(response.status,200)
        self.assertEqual(response.json()['company']['id'],3)
        self.assertEqual(response.json()['scope'],[dict(id=8,company_id=3,kind='domain',domain='example.test',raw='example.test',reason='api')])
        self.store.get.return_value=None
        self.assertEqual(self.call('/3').status,404)

    def test_create_metadata_only_exact_envelope(self):
        response=self.call(method='POST',data={'name':'  Example\u3000','scope':[]})
        self.assertEqual(response.status,201)
        self.assertEqual(response.json(),dict(id=3,created=True,scope_added=0,scope_skipped=0,scope_invalid=0))
        self.store.create.assert_called_once_with('Example','',scope_inputs=[])
        self.store.upsert.assert_not_called()

    def test_nonempty_scope_prevents_partial_creation(self):
        for rules in (['example.test'],[{'kind':'domain','value':'example.test'}],['']):
            self.assertEqual(self.call(method='POST',data={'name':'Example','scope':rules}).status,503)
        self.store.create.assert_not_called()

    def test_invalid_body_and_scope(self):
        for data in ({'name':''},{'name':None},{'name':'Example','id':3},{'name':'Example','scope':'all'},
                     {'name':'Example','scope':[3]}, {'name':'Example','scope':[{'kind':'domain','value':'a'*1025}]},
                     {'name':'Example','scope':[{'value':'x','company_id':4}]},
                     b'{"name":"a","name":"b"}',b'{"name":NaN}',b'[]','{}'.encode('utf-16')):
            self.assertEqual(self.call(method='POST',data=data).status,400)
        self.assertEqual(self.call(method='POST',data=b' '*(MAX_BODY+1)).status,413)
        self.store.create.assert_not_called()

    def test_no_ignored_scope_queries_or_bad_ids(self):
        for path in ('?company_id=3','?id=3&id=4','?q=Example','/3?scope=all','/0','/-1','/bad','/9223372036854775808'):
            self.assertEqual(self.call(path).status,400)
        self.assertEqual(self.store.mock_calls,[])

    def test_unported_mutations_and_no_invented_patch(self):
        for method,path in [('DELETE','/3'),('POST','/3/scope'),('POST','/reattribute')]:
            self.assertEqual(self.call(path,method).status,503)
        self.assertEqual(self.call('/3','PATCH',{'name':'new'}).status,405)
        self.assertEqual(self.store.mock_calls,[])

    def test_conflict_and_internal_error_redaction(self):
        self.store.create.side_effect=CompanyNameConflict('sensitive')
        self.assertEqual(self.call(method='POST',data={'name':'Example'}).status,409)
        self.store.create.side_effect=RuntimeError('secret SQL')
        r=self.call(method='POST',data={'name':'Example'})
        self.assertEqual(r.status,500);self.assertNotIn(b'secret',r.body)

    def test_bad_creation_adapter_result_redacted(self):
        for result in (None,True,0,-1,2**63,{'secret':'private'}):
            self.store.create.return_value=result
            r=self.call(method='POST',data={'name':'Example'})
            self.assertEqual(r.status,500);self.assertNotIn(b'private',r.body)

    def test_real_store_scoped_get_and_create_not_broadened(self):
        executor=Mock()
        executor.fetch_all.side_effect=[[ROW],[]]
        store=CompanyStore(executor,company_ids=[3])
        self.router=Router(lambda _:True);CompanyService(store).register(self.router)
        self.assertEqual(self.call('/3').status,200)
        query=executor.fetch_all.call_args_list[0].args[0]
        self.assertEqual(query.params,(3,'{3}'))
        self.assertEqual(self.call('/4').status,404)
        self.assertEqual(executor.fetch_all.call_count,2)
        self.assertEqual(self.call(method='POST',data={'name':'New'}).status,503)
        executor.transaction.assert_not_called()

    def test_real_store_create_transaction_contract(self):
        class Executor:
            def __init__(self):self.events=[]
            @contextmanager
            def transaction(self):
                self.events.append('begin')
                try:yield self
                except Exception:self.events.append('rollback');raise
                else:self.events.append('commit')
            def execute(self,q):self.events.append(q);return 1
            def fetch_all(self,q):self.events.append(q);return [(3,)]
        executor=Executor()
        self.router=Router(lambda _:True);CompanyService(CompanyStore(executor)).register(self.router)
        self.assertEqual(self.call(method='POST',data={'name':'Example'}).status,201)
        self.assertEqual(executor.events[0],'begin');self.assertEqual(executor.events[-1],'commit')
        self.assertEqual(executor.events[1].params,(7337741003,))
        self.assertEqual(executor.events[2].params,('Example','example',None))
        self.assertIn('ON CONFLICT(nkey) DO NOTHING',executor.events[2].sql)


if __name__=='__main__':unittest.main()

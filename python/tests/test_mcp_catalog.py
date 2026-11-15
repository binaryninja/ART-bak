import unittest,json
from contextlib import nullcontext
from artex.db.mcp_catalog import *
from artex.server.mcp_catalog import MCPCatalogService
from artex.server.core import Request,Router
from test_tasks_store import Fake
ROW=(7,'Fixture','http','',['opaque-arg'],{'Authorization':'dummy-secret'},'https://example.test/path?key=opaque-url',True,False)
class MCPTests(unittest.TestCase):
    def make(self,results):
        self.db=Fake(results);self.store=MCPCatalogStore(self.db);self.valid=True
        self.service=MCPCatalogService(self.store,authorize=lambda *a:True,current=lambda:self.valid,lifecycle_guard=lambda:nullcontext(),mutation_authorize=lambda *a:True,delete_authorize=lambda *a:True,idle=lambda id:True,synchronize=lambda *a:True)
    def call(self,action,body=None):
        return self.service.handle(action,Request('POST','/api/mcp',body=json.dumps(body).encode() if body is not None else b''),{'id':'7'})
    def test_public_list_and_cache_credentials_redacted(self):
        self.make([[ROW],[('tool','description dummy-secret')]])
        response=self.call('list');self.assertEqual(response.status,200)
        for secret in ('dummy-secret','opaque-arg','opaque-url'):self.assertNotIn(secret.encode(),response.body)
        item=response.json()['servers'][0];self.assertEqual(item['id'],7);self.assertEqual(item['env'],{'[redacted:1]':'[redacted]'})
        self.assertEqual(item['url'],'[redacted]');self.assertEqual(item['tools'],['[redacted]'])
    def test_cached_tools_no_connection_and_description_redaction(self):
        self.make([[ROW],[('tool','contains dummy-secret')]])
        result=self.call('tools');self.assertEqual(result.status,200);self.assertNotIn(b'dummy-secret',result.body)
        self.assertEqual(len(self.db.calls),2)
    def test_actual_insert_bound_raw_opaque_config_no_discovery(self):
        self.make([[(8,)]])
        result=self.call('save',{'name':'Fixture','transport':'stdio','command':'do-not-run','args':['--secret=dummy'],'env':{'TOKEN':'dummy-secret'},'enabled':True})
        self.assertEqual(result.status,200,result.body);self.assertEqual(result.json(),{'id':8})
        query=next(q for _,q in self.db.calls if q)
        self.assertEqual(query.params[2],'do-not-run');self.assertEqual(json.loads(query.params[4]),{'TOKEN':'dummy-secret'})
        self.assertEqual(len([q for _,q in self.db.calls if q]),1)
    def test_update_missing_no_fabricated_success(self):
        self.make([[]]);response=self.call('save',{'id':7,'name':'Fixture','transport':'http'})
        self.assertEqual(response.status,404);self.assertEqual(self.db.calls[-1][0],'rollback')
    def test_delete_visibility_then_parent_atomic(self):
        self.make([[(7,)],3,1]);result=self.call('delete')
        self.assertEqual(result.status,200);queries=[q for _,q in self.db.calls if q]
        self.assertIn("resource_kind='mcp'",queries[1].sql);self.assertEqual(queries[2].sql,'DELETE FROM mcp_servers WHERE id=$1')
        self.assertEqual(self.db.calls[-1][0],'commit')
    def test_delete_failure_no_secret_echo_or_false_success(self):
        self.make([[(7,)],1,RuntimeError('dummy-secret')]);result=self.call('delete')
        self.assertEqual(result.status,503);self.assertIsNone(result.json()['committed']);self.assertNotIn(b'dummy-secret',result.body)
        self.assertEqual(self.db.calls[-1][0],'rollback')
    def test_placeholders_rejected_before_save(self):
        self.make([[ROW],[]]);public=self.call('list').json()['servers'][0]
        self.db.calls.clear();result=self.call('save',public)
        self.assertEqual(result.status,400);self.assertEqual(self.db.calls,[])
    def test_independent_policies_and_final_idle_fence(self):
        self.make([]);self.service.delete=None;self.assertEqual(self.call('delete').status,503)
        self.service.delete=lambda *a:False;self.assertEqual(self.call('delete').status,403)
        self.service.delete=lambda *a:True
        def idle(id):self.valid=False;return True
        self.service.idle=idle;self.assertEqual(self.call('delete').status,409);self.assertEqual(self.db.calls,[])
    def test_postcommit_sync_failure_explicit_receipt(self):
        self.make([[(8,)]]);self.service.sync=lambda *a:False
        result=self.call('save',{'name':'Fixture','transport':'http'})
        self.assertEqual(result.status,503);self.assertTrue(result.json()['committed']);self.assertTrue(result.json()['needs_reconciliation'])
    def test_mutation_callback_cannot_rewrite_authorized_config(self):
        self.make([[(8,)]])
        def mutate(req,action,id,server):server.command='changed';server.env['TOKEN']='changed';return True
        self.service.mutate=mutate
        result=self.call('save',{'name':'Fixture','transport':'stdio','command':'original','env':{'TOKEN':'original-secret'}})
        self.assertEqual(result.status,200)
        query=next(q for _,q in self.db.calls if q);self.assertEqual(query.params[2],'original');self.assertEqual(json.loads(query.params[4])['TOKEN'],'original-secret')
    def test_refresh_not_registered_and_constructor_inert(self):
        self.make([]);router=Router(lambda r:True);self.service.register(router)
        self.assertFalse(any(path.endswith('/refresh') for _,path,*_ in router._routes));self.assertEqual(self.db.calls,[])
    def test_store_exception_and_repr_do_not_echo_credentials(self):
        self.make([RuntimeError('dummy-secret')])
        with self.assertRaises(MCPUnavailable) as caught:self.store.list()
        self.assertNotIn('dummy-secret',str(caught.exception));self.assertNotIn('dummy-secret',repr(MCPServer(env={'TOKEN':'dummy-secret'})))
    def test_untrusted_tool_names_and_encoded_descriptions_are_not_public(self):
        self.make([[ROW],[('credential-fragment','encoded-secret-value')]])
        result=self.call('tools');self.assertEqual(result.status,200)
        self.assertEqual(result.json(),{'tools':[{'name':'[redacted]','description':'[redacted]'}]})
    def test_generated_redacted_environment_keys_cannot_be_saved(self):
        self.make([])
        for key in ('[redacted]','[redacted:1]','[REDACTED:2]'):
            result=self.call('save',{'name':'Fixture','env':{key:'new-value'}})
            self.assertEqual(result.status,400)
        self.assertEqual(self.db.calls,[])

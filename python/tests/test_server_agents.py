import json
import unittest
from unittest.mock import Mock
from artex.db.agents import Agent,AgentStore
from artex.server.agents import AgentService
from artex.server.core import Router,Request
from test_agents import ROW,SQL

class AgentHTTPTests(unittest.TestCase):
    def setUp(self):
        self.store=Mock();self.store.list.return_value=[Agent(*ROW)];self.store.get.return_value=Agent(*ROW)
        self.store.binding_counts.return_value=({1:2},{},{'custom':4})
        self.service=AgentService(self.store);self.router=Router(lambda req:req.header('Authorization')=='fixture')
        self.service.register(self.router)
    def call(self,path='',method='GET',data=None,auth=True):
        body=json.dumps(data).encode() if isinstance(data,dict) else data or b''
        return self.router.dispatch(Request(method,'/api/agents'+path,{'Authorization':'fixture'} if auth else {},body))
    def test_all_auth_and_missing_store(self):
        for method,path in [('GET',''),('POST',''),('PATCH','/custom'),('GET','/custom'),('DELETE','/custom'),('PUT','/custom/config')]:
            self.assertEqual(self.call(path,method,auth=False).status,401)
        self.assertEqual(self.store.mock_calls,[])
        self.service.store=None;self.assertEqual(self.call().status,503)
    def test_list_exact_source_dto_counts_without_readiness(self):
        response=self.call();self.assertEqual(response.status,200)
        self.assertEqual(response.json(),{'agents':[Agent(*ROW).to_dict(mcp_count=2,tool_count=4)]})
        self.assertFalse(self.service.execution_ready('custom'))
        self.store.list.return_value=[];self.assertEqual(self.call().json(),{'agents':[]})
    def test_explicit_ready_never_builtin_task_or_retester(self):
        self.service.readiness=lambda a:True
        self.assertTrue(self.service.execution_ready('custom'))
        for index,value in ((1,'retester'),(4,'main'),(6,False)):
            row=list(ROW);row[index]=value;self.store.get.return_value=Agent(*row)
            self.assertFalse(self.service.execution_ready(row[1]))
    def test_create_real_store_returns_source_wire(self):
        db=SQL([ROW,(7,),1]);self.service.store=AgentStore(db)
        response=self.call(method='POST',data={'key':'custom','name':'Custom'})
        self.assertEqual(response.status,200);self.assertEqual(response.json()['id'],'1')
        self.assertEqual(db.events,['begin','commit'])
    def test_patch_custom_only_and_missing(self):
        self.assertEqual(self.call('/custom','PATCH',{'name':'Changed'}).json(),{'ok':True})
        self.store.update_metadata.assert_called_once_with('custom','Changed','')
        row=list(ROW);row[5]=True;self.store.get.return_value=Agent(*row)
        self.assertEqual(self.call('/custom','PATCH',{'name':'Changed'}).status,400)
        self.store.get.return_value=None;self.assertEqual(self.call('/custom').status,404)
    def test_malformed_duplicate_and_unavailable_no_write(self):
        for data in (b'{"name":"a","name":"b"}',{'name':'x','enabled':True},{'name':''}):
            self.assertEqual(self.call('/custom','PATCH',data).status,400)
        self.assertEqual(self.call('/custom?x=1').status,400)
        for method,suffix in [('DELETE',''),('PUT','/config'),('PUT','/prompt'),('POST','/prompt/reset'),('GET','/triggers')]:
            self.assertEqual(self.call('/custom'+suffix,method).status,503)
        self.store.update_metadata.assert_not_called()
    def test_detail_global_vars_and_no_fake_task_defaults(self):
        self.store.detail.return_value=dict(prompt='fixture',variables=[{'name':'Now','description':'stale'}],versions=[],visibility={'mcp':None,'skill':[]},llm_profiles=[])
        result=self.call('/custom');self.assertEqual(result.status,200)
        self.assertEqual([v['name'] for v in result.json()['variables']],['Now','DataDir'])
        self.assertFalse(result.json()['task_timeout_wrapup_supported'])
        row=list(ROW);row[1]='worker';self.store.get.return_value=Agent(*row)
        self.assertEqual(self.call('/worker').status,503)
    def test_count_failure_not_fabricated_zero(self):
        self.store.binding_counts.side_effect=RuntimeError('private SQL detail')
        result=self.call();self.assertEqual(result.status,500)
        self.assertNotIn('private',result.body.decode())

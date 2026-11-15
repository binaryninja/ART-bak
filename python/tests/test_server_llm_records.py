"""Authorized recording HTTP over real scoped store and fake SQL only."""
import unittest
from datetime import datetime,timezone
from artex.server.core import Router,Request
from artex.server.llm_records import LLMRecordService
from artex.db.llm_records import LLMRecordStore
from artex.db.postgres import NoRowsError
from test_agents import SQL

ROW=(7,datetime(2026,1,1,tzinfo=timezone.utc),'model','profile','conv-1','task1','agent',20,3,4,1,2,'ok','')
FULL=ROW+('normalized secret','reply','raw secret','SSE body')

class RecordHTTPTests(unittest.TestCase):
    def setUp(self):
        self.db=SQL();self.store=LLMRecordStore(self.db,task_id='task1');self.allowed=[]
        self.service=LLMRecordService(lambda *a:self.store,authorize=self.authorize,redact=self.redact)
        self.router=Router(lambda req:req.header('Authorization')=='fixture');self.service.register(self.router)
    def authorize(self,request,action,selection):self.allowed.append((action,selection));return True
    def redact(self,request,row,bodies):
        if bodies:
            for name in ('request_body','raw_request'):row[name]='[redacted]'
        return row
    def call(self,suffix='',method='GET',auth=True):
        return self.router.dispatch(Request(method,'/api/llm/records'+suffix,{'Authorization':'fixture'} if auth else {}))
    def test_auth_before_scope_and_db(self):
        self.assertEqual(self.call(auth=False).status,401);self.assertEqual(self.allowed,[]);self.assertEqual(self.db.calls,[])
    def test_list_scoped_sql_and_exact_metadata(self):
        self.db.results=[(1,),[ROW]]
        result=self.call('?task=task1&session=conv-1&model=model&page=2&size=10')
        self.assertEqual(result.status,200);self.assertEqual(result.json()['total'],1)
        self.assertEqual(result.json()['records'][0]['id'],7)
        self.assertNotIn('raw_request',result.json()['records'][0])
        self.assertEqual(self.db.calls[1].params,('model','%conv-1%','task1',10,20))
        self.assertEqual(self.allowed[-1][0],'record_metadata')
        self.assertEqual(self.allowed[-1][1].record_session,'conv-1')
    def test_detail_redaction_and_separate_body_permission(self):
        self.db.results=[FULL]
        result=self.call('/7');self.assertEqual(result.status,200)
        self.assertEqual(result.json()['raw_request'],'[redacted]')
        self.assertEqual(result.json()['raw_response'],'SSE body')
        self.assertEqual(self.db.calls[0].params,(7,'task1'))
        self.assertEqual(self.allowed[-1][0],'record_body')
    def test_detail_body_denied(self):
        self.service.authorize=lambda req,action,sel:action!='record_body';self.db.results=[FULL]
        response=self.call('/7');self.assertEqual(response.status,403)
        self.assertNotIn('secret',response.body.decode())
    def test_malformed_or_conflicting_scope_no_fallback(self):
        for target in ('?task=task2','?task=task1&task=task2','?task=','?page=-1','?size=0','?size=1001','?unknown=1','/-1','/7?task=task1'):
            with self.subTest(target=target):self.assertIn(self.call(target).status,(400,403))
        self.assertEqual(self.db.calls,[])
    def test_missing_store_policy_redactor(self):
        for field in ('resolve','authorize','redact'):
            old=getattr(self.service,field);setattr(self.service,field,None)
            self.assertEqual(self.call().status,503);setattr(self.service,field,old)
        self.assertEqual(self.db.calls,[])
    def test_scoped_result_escape_or_redactor_identity_change_denied(self):
        changed=list(FULL);changed[5]='other-task';self.db.results=[tuple(changed)]
        self.assertEqual(self.call('/7').status,403)
        self.service.redact=lambda req,row,bodies:dict(row,id=123);self.db.results=[FULL]
        self.assertEqual(self.call('/7').status,503)
    def test_missing_vs_database_error_distinct(self):
        self.db.results=[NoRowsError()];self.assertEqual(self.call('/7').status,404)
        self.db.results=[RuntimeError('secret database host')]
        response=self.call('/7');self.assertEqual(response.status,500);self.assertNotIn('secret',response.body.decode())
    def test_tasks_and_token_ledger_shapes(self):
        self.db.results=[[('task1',2)]];self.assertEqual(self.call('/tasks').json(),{'tasks':[{'task_id':'task1','count':2}]})
        self.assertEqual(self.db.calls[-1].params,('task1',))
        self.db.results=[[('m',2,3,4,5,6)]]
        result=self.call('/by-model?task=task1')
        self.assertEqual(result.status,200);self.assertEqual(result.json()['models'][0]['cache_write_tokens'],6)
        self.assertEqual(self.call('/by-model').status,400)
    def test_unsupported_delete_and_archive_totals(self):
        self.assertEqual(self.call('?task=task1','DELETE').status,503)
        result=self.router.dispatch(Request('GET','/api/tokens/usage',{'Authorization':'fixture'}))
        self.assertEqual(result.status,503);self.assertEqual(self.db.calls,[])

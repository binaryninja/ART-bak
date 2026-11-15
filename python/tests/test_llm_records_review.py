"""Independent recording read-boundary tests; fake SQL, no real captures."""
import unittest
from artex.db.llm_records import LLMRecordStore
from artex.server.llm_records import LLMRecordService
from artex.server.core import Router,Request
from test_server_llm_records import ROW,FULL
from test_agents import SQL

class RecordingReview(unittest.TestCase):
    def setup_service(self,*,scope='task1',authorize=lambda *args:True,redact=lambda req,row,body:row):
        self.db=SQL();self.store=LLMRecordStore(self.db,task_id=scope)
        service=LLMRecordService(lambda *a:self.store,authorize=authorize,redact=redact)
        self.router=Router(lambda req:True);service.register(self.router);return service
    def request(self,path):return self.router.dispatch(Request('GET',path))
    def test_wildcard_session_never_replaces_fixed_task(self):
        self.setup_service();self.db.results=[(1,),[ROW]]
        response=self.request('/api/llm/records?session=%25_')
        self.assertEqual(response.status,200)
        for query in self.db.calls:
            self.assertIn("COALESCE(task_id,'') = $2",query.sql)
            self.assertEqual(query.params[:2],('%%_%','task1'))
    def test_global_store_requires_explicit_second_policy(self):
        calls=[]
        def authorize(req,operation,selection):
            calls.append(selection)
            return len(calls)==1 or selection.store_task is not None
        self.setup_service(scope=None,authorize=authorize)
        response=self.request('/api/llm/records')
        self.assertEqual(response.status,403);self.assertEqual(len(calls),2);self.assertEqual(self.db.calls,[])
    def test_list_strips_body_even_if_store_returns_detail_shape(self):
        observed=[]
        def redact(req,row,body):observed.append((row.copy(),body));return row
        self.setup_service(redact=redact);self.db.results=[(1,),[FULL]]
        response=self.request('/api/llm/records')
        self.assertEqual(response.status,200)
        self.assertFalse(observed[0][1]);self.assertNotIn('request_body',observed[0][0])
        self.assertNotIn('secret',response.body.decode())
        self.assertNotIn('raw_request',self.db.calls[-1].sql)
    def test_redactor_cannot_add_back_hidden_body(self):
        self.setup_service(redact=lambda req,row,body:dict(row,raw_request='added'))
        self.db.results=[(1,),[ROW]]
        response=self.request('/api/llm/records')
        self.assertEqual(response.status,503);self.assertNotIn('added',response.body.decode())
    def test_denied_metadata_does_not_leak_total_or_partial_records(self):
        self.setup_service(authorize=lambda req,operation,selection:operation!='record_metadata')
        self.db.results=[(999,),[ROW]]
        response=self.request('/api/llm/records')
        self.assertEqual(response.status,403);self.assertNotIn('999',response.body.decode())
    def test_count_query_failure_not_zero_or_page_fallback(self):
        self.setup_service();self.db.results=[RuntimeError('secret connection')]
        response=self.request('/api/llm/records')
        self.assertEqual(response.status,500);self.assertNotIn('secret',response.body.decode());self.assertEqual(len(self.db.calls),1)

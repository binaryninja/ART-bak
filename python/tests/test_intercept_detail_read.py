import json,unittest
from contextlib import contextmanager
from datetime import datetime,timezone
from unittest.mock import patch
from artex.db.intercept_history import InspectionScope
from artex.db.intercept_detail_read import InterceptDetailReader
from artex.server.intercept_detail_read import InterceptDetailReadService
from artex.server.core import Request

class Tests(unittest.TestCase):
    def setUp(self):
        self.queries=[];self.allowed=True;self.raw=True;self.after_read=None;self.after_guard=None
        self.row=(True,7,None,12,'opaque-task','planner','Bash','{"command":"synthetic"}','denied','raw reason',None,datetime(2026,1,1,tzinfo=timezone.utc),'model','conversation','assistant','rule',None)
        self.rows=[self.row]
        class DB:
            def fetch_all(db,q):
                self.queries.append(q)
                if self.after_read:self.after_read()
                return self.rows
        self.reader=InterceptDetailReader(DB(),InspectionScope('workspace','opaque-task'))
        @contextmanager
        def guard(scope):
            yield
            if self.after_guard:self.after_guard()
        self.service=InterceptDetailReadService(self.reader,authorize=lambda req,s,id:self.allowed,raw_authorize=lambda req,s,id:self.raw,current=lambda s:True,lifecycle_guard=guard)
        self.request=Request('GET','/api/intercept/history/7')
    def test_source_fields_null_old_audit_and_bound_scope(self):
        response=self.service.handle(self.request);self.assertEqual(response.status,200)
        data=response.json();self.assertEqual(data['task_id'],'opaque-task');self.assertIsNone(data['audit']);self.assertIsNone(data['rule_id']);self.assertIsNone(data['decided_at'])
        self.assertEqual(data['tool_input'],{'command':'synthetic'});self.assertEqual(data['created_at'],'2026-01-01T00:00:00Z')
        self.assertEqual(self.queries[0].params,(7,'opaque-task',1048576));self.assertIn('CASE WHEN bytes<=$3',self.queries[0].sql)
    def test_audit_recorded_projection_and_null_context(self):
        audit={'correlation':'exact','input_digest':'hash','user_message':'hi','initial_action':'deny','initial_reason':'reason','execution_status':'not_executed','context':[{'kind':'tool','text':'raw','tool':'Bash','is_error':True}],'model_input':{'messages':[]},'unknown':'ignored'}
        self.rows=[(*self.row[:-1],json.dumps(audit))]
        data=self.service.handle(self.request).json()['audit']
        self.assertEqual(data['context'],audit['context']);self.assertEqual(data['model_input'],audit['model_input']);self.assertNotIn('unknown',data);self.assertNotIn('output',data)
        self.rows=[(*self.row[:-1],'null')];self.assertIsNone(self.service.handle(self.request).json()['audit'])
    def test_no_row_and_bad_selector(self):
        self.rows=[];self.assertEqual(self.service.handle(self.request).status,404)
        for value in ('0','-1','9223372036854775808','7/execution','7?task=else','%37'):
            self.assertEqual(self.service.handle(Request('GET','/api/intercept/history/'+value)).status,400)
    def test_raw_denial_before_sql_and_after_read(self):
        self.raw=False;self.assertEqual(self.service.handle(self.request).status,403);self.assertFalse(self.queries)
        self.raw=True;self.after_read=lambda:setattr(self,'raw',False)
        self.assertEqual(self.service.handle(self.request).status,403)
    def test_guard_and_serializer_revocation(self):
        self.after_guard=lambda:setattr(self,'allowed',False)
        self.assertEqual(self.service.handle(self.request).status,403)
        self.after_guard=None;self.allowed=True
        from artex.server.core import json_response
        def encode(*a):
            response=json_response(*a);self.allowed=False;return response
        with patch('artex.server.intercept_detail_read.json_response',encode):self.assertEqual(self.service.handle(self.request).status,403)
    def test_overflow_malformed_and_foreign_task_fail_closed(self):
        for row in ((False,*self.row[1:]),(*self.row[:4],'foreign',*self.row[5:]),(*self.row[:-1],'{broken'),(*self.row[:-1],'{"context":12}')):
            self.rows=[row];response=self.service.handle(self.request);self.assertEqual(response.status,500);self.assertNotIn(b'raw reason',response.body)
    def test_scope_rebinding_and_original_policy_swap(self):
        self.after_read=lambda:setattr(self.reader,'scope',InspectionScope('other'))
        self.assertEqual(self.service.handle(self.request).status,500)
        self.reader.scope=self.reader._pins[1]
        self.after_read=lambda:setattr(self.service,'raw_authorize',lambda *a:True)
        self.assertEqual(self.service.handle(self.request).status,403)

    def test_nested_raw_json_bounds_before_serialization(self):
        for raw in ('{"model_input":'+ '['*66+'0'+']'*66+'}', '{"model_input":1e999}'):
            self.rows=[(*self.row[:-1],raw)]
            with patch('artex.server.intercept_detail_read.json_response',side_effect=AssertionError('must not serialize')):
                self.assertEqual(self.service.handle(self.request).status,500)

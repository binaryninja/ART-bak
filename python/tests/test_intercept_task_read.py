import unittest
from contextlib import contextmanager
from unittest.mock import patch
from artex.db.intercept_history import InterceptHistoryStore,InspectionScope
from artex.server.intercept_task_read import TaskInterceptReadService
from artex.server.core import Request,json_response
from test_intercept_history import ROW

class Tests(unittest.TestCase):
    def setUp(self):
        self.rows=[ROW];self.queries=[];self.allowed=True;self.raw=True;self.after_guard=None
        class DB:
            def fetch_all(db,q):self.queries.append(q);return self.rows
        @contextmanager
        def guard(scope):
            yield
            if self.after_guard:self.after_guard()
        self.store=InterceptHistoryStore(DB(),InspectionScope('workspace','task-text'),authorize=lambda *a:self.allowed,raw_authorize=lambda *a:self.raw,current=lambda *a:True,lifecycle_guard=guard,max_rows=2)
        self.service=TaskInterceptReadService(self.store,authorize=lambda *a:self.allowed)
    def request(self,suffix='task-text'):return self.service.handle(Request('GET','/api/intercept/task/'+suffix))
    def test_legacy_unpaged_source_shape_and_bounded_query(self):
        response=self.request();self.assertEqual(response.status,200)
        self.assertEqual(set(response.json()),{'items','total'});self.assertEqual(response.json()['items'][0]['tool_input'],{'x':'inert'})
        q=self.queries[0];self.assertEqual(q.params[:2],('task-text',None));self.assertEqual(q.params[4:6],(3,0));self.assertIn('rule_name',q.sql)
    def test_pages_filter_source_normalization(self):
        r=self.request('task-text?page=0&size=-1&status=pending')
        # max_rows2 intentionally refuses a requested page20 instead of truncating.
        self.assertEqual(r.status,500)
        r=self.request('task-text?page=2&size=1&decision_source=model');self.assertEqual(r.status,200)
        self.assertEqual(r.json()['page'],2);self.assertEqual(r.json()['page_size'],1)
        self.assertEqual(self.queries[-1].params[3:6],('model',1,1))
    def test_unpaged_overflow_never_returns_partial_rows(self):
        self.rows=[(3,*ROW[1:])]*3
        self.assertEqual(self.request().status,500)
    def test_exact_text_scope_no_active_or_numeric_fallback(self):
        self.assertEqual(self.request('5').status,403)
        self.assertEqual(self.request('task%2Ftext').status,400)
        self.assertEqual(self.request('task-text?task=other').status,400)
        self.assertFalse(self.queries)
    def test_empty_and_raw_denial(self):
        self.raw=False;self.assertEqual(self.request().status,403);self.assertFalse(self.queries)
        self.raw=True;self.rows=[(0,)+(None,)*15]
        self.assertEqual(self.request().json(),{'items':[],'total':0})
    def test_final_guard_and_serialization_denial(self):
        self.after_guard=lambda:setattr(self,'raw',False)
        self.assertEqual(self.request().status,403)
        self.after_guard=None;self.raw=True
        def encode(*a):
            response=json_response(*a);self.allowed=False;return response
        with patch('artex.server.intercept_task_read.json_response',encode):self.assertEqual(self.request().status,403)
    def test_original_scope_and_policy_pins(self):
        self.store.scope=InspectionScope('workspace','other')
        self.assertEqual(self.request().status,403);self.assertFalse(self.queries)
    def test_global_store_cannot_select_unpaged_task(self):
        self.store.scope=self.store._scope=InspectionScope('workspace')
        with self.assertRaises(ValueError):self.store.read('task_all')
        self.assertFalse(self.queries)

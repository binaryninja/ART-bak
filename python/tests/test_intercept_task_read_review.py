"""Independent scoped task history contract tests with inert DB rows."""
import unittest
from contextlib import contextmanager
from threading import Lock
from unittest.mock import patch
from artex.server.core import Request,json_response
from artex.server.intercept_task_read import parse_request,TaskInterceptReadService
from artex.db.intercept_history import InterceptHistoryStore,InspectionScope
import test_intercept_task_read as fixture

class TaskReadReview(unittest.TestCase):
    def fixture(self):
        f=fixture.Tests();f.setUp();self.addCleanup(f.doCleanups);return f
    def test_opaque_encoded_key_no_numeric_or_double_decode(self):
        self.assertEqual(parse_request(Request('GET','/api/intercept/task/team%3Arun%20A')).task_key,'team:run A')
        self.assertEqual(parse_request(Request('GET','/api/intercept/task/a%252Fb')).task_key,'a%2Fb')
        f=self.fixture()
        for suffix in ('task%ZZ','task%FF','task%00text','task%5Ctext','task-text?size=1&size=2'):
            with self.subTest(suffix=suffix):self.assertEqual(f.request(suffix).status,400)
        self.assertFalse(f.queries)
    def test_no_nested_guard_and_after_exit_raw_revoke(self):
        f=self.fixture();lock=Lock()
        @contextmanager
        def guard(scope):
            if not lock.acquire(False):raise AssertionError('Nested lifecycle guard')
            try:yield
            finally:lock.release();f.raw=False
        store=InterceptHistoryStore(f.store.executor,f.store.scope,authorize=lambda *a:True,raw_authorize=lambda *a:f.raw,current=lambda *a:True,lifecycle_guard=guard,max_rows=2)
        service=TaskInterceptReadService(store,authorize=lambda *a:True)
        response=service.handle(Request('GET','/api/intercept/task/task-text'))
        self.assertEqual(response.status,403);self.assertEqual(len(f.queries),1)
    def test_last_serializer_raw_revocation_withholds_json(self):
        f=self.fixture()
        def encode(*args):
            response=json_response(*args);f.raw=False;return response
        with patch('artex.server.intercept_task_read.json_response',encode):response=f.request()
        self.assertEqual(response.status,403);self.assertNotIn(b'inert',response.body)
    def test_final_raw_cannot_replace_original_request_grant(self):
        f=self.fixture();calls=[]
        def raw(*args):
            calls.append(True)
            if f.queries:f.service.authorize=lambda *a:True
            return True
        f.store.raw=raw
        f.service=TaskInterceptReadService(f.store,authorize=lambda *a:True)
        self.assertEqual(f.request().status,403)
    def test_driver_json_nonprimitive_surrogate_and_depth_fail_closed(self):
        for payload in ({'x':float('inf')},{'\ud800':'x'}, {'x':object()}):
            f=self.fixture();row=list(f.rows[0]);row[7]=payload;f.rows=[tuple(row)]
            self.assertEqual(f.request().status,500)

if __name__=='__main__':unittest.main()

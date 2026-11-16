import unittest,json
import test_metadata_detail_setup as transport_fixture
import test_intercept_task_read as task_fixture
from artex.server.intercept_read_setup import bind_metadata_reads

class Tests(unittest.TestCase):
    def setUp(self):
        self.t=transport_fixture.Tests();self.t.setUp();self.addCleanup(self.t.doCleanups)
        self.h=task_fixture.Tests();self.h.setUp();self.addCleanup(self.h.doCleanups)
        self.t.bundle=bind_metadata_reads(self.t.f.original,task_history_service=self.h.service,authorize_global=lambda r,k:k=='task_history')
    def test_exact_optional_legacy_and_paged(self):
        self.assertFalse(self.h.queries)
        self.assertEqual(self.t.request('/api/intercept/task/task-text',token=False)[0],401);self.assertFalse(self.h.queries)
        status,body=self.t.request('/api/intercept/task/task-text');self.assertEqual(status,200)
        self.assertEqual(set(json.loads(body)),{'items','total'})
        self.assertEqual(self.t.request('/api/intercept/task/task-text?page=1&size=1')[0],200)
        self.assertEqual(self.h.queries[-1].params[:2],('task-text',None))
    def test_scope_and_final_raw_denial(self):
        self.assertEqual(self.t.request('/api/intercept/task/other')[0],403);self.assertFalse(self.h.queries)
        with self.assertRaises(RuntimeError):self.t.request('/api/intercept/task/task-text',start=lambda:setattr(self.h,'raw',False))
        self.assertEqual(len(self.h.queries),1)
    def test_read_only_and_mixed_conflict(self):
        self.assertEqual(self.t.request('/api/intercept/task/task-text',method='POST')[0],405)
        self.assertEqual(self.t.request('/api/intercept/task/task-text',method='HEAD'),(400,b''))
        self.t.f.original.router.add('HEAD','/api/inter{suffix}/task/{key}',lambda r,p:None)
        with self.assertRaises(ValueError):bind_metadata_reads(self.t.f.original,task_history_service=self.h.service,authorize_global=lambda *a:True)
        self.assertFalse(self.h.queries)
    def test_callback_swap_prebind_refused(self):
        self.h.service.validate_disclosure=lambda *a:None
        with self.assertRaises(ValueError):bind_metadata_reads(self.t.f.original,task_history_service=self.h.service,authorize_global=lambda *a:True)
        self.assertFalse(self.h.queries)

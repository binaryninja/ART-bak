import unittest,json
import test_metadata_detail_setup as transport_fixture
import test_intercept_execution_read as navigation_fixture
from artex.server.intercept_read_setup import bind_metadata_reads

class Tests(unittest.TestCase):
    def setUp(self):
        self.t=transport_fixture.Tests();self.t.setUp();self.addCleanup(self.t.doCleanups)
        self.n=navigation_fixture.Tests();self.n.setUp();self.addCleanup(self.n.doCleanups)
        self.t.bundle=bind_metadata_reads(self.t.f.original,execution_service=self.n.service,authorize_global=lambda r,k:k=='execution')
        self.path='/api/intercept/history/7/execution'
    def test_optional_jwt_and_original_pair(self):
        self.assertFalse(self.n.queries)
        self.assertEqual(self.t.request(self.path,token=False)[0],401);self.assertFalse(self.n.queries)
        status,body=self.t.request(self.path+'?conversation=999')
        self.assertEqual(status,200);self.assertEqual(json.loads(body)['task_id'],'5')
        self.assertFalse(any(q.sql.startswith('SELECT EXISTS') for q in self.n.queries))
    def test_start_response_parent_deletion_blocks_body(self):
        with self.assertRaises(RuntimeError):self.t.request(self.path,start=lambda:setattr(self.n,'task_live',False))
    def test_start_response_activity_change_blocks_body(self):
        def change():self.n.activity[0]=(*self.n.activity[0][:8],'changed',*self.n.activity[0][9:])
        with self.assertRaises(RuntimeError):self.t.request(self.path,start=change)
    def test_semantic_error_is_also_fenced(self):
        self.n.detail=None;self.n.hint_exists=False
        self.assertEqual(self.t.request(self.path+'?conversation=12')[0],410)
        with self.assertRaises(RuntimeError):self.t.request(self.path+'?conversation=12',start=lambda:setattr(self.n,'parent_allowed',False))
    def test_mixed_route_conflict_and_method_denial(self):
        self.assertEqual(self.t.request(self.path,method='POST')[0],405)
        self.t.f.original.router.add('HEAD','/a{rest}/intercept/history/{id}/exec{suffix}',lambda r,p:None)
        with self.assertRaises(ValueError):bind_metadata_reads(self.t.f.original,execution_service=self.n.service,authorize_global=lambda *a:True)

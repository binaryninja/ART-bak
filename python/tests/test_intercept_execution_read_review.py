"""Independent recorded-navigation review; no tool execution or real records."""
import unittest
from unittest.mock import patch
from artex.server.core import Request,json_response
from artex.server.intercept_execution_read import InterceptExecutionReadService
import test_intercept_execution_read as fixture

class NavigationReview(unittest.TestCase):
    def fixture(self):
        f=fixture.Tests();f.setUp();self.addCleanup(f.doCleanups);return f
    def test_parent_capability_mutation_cannot_retarget_query(self):
        f=self.fixture()
        def parent(req,scope,id,p):object.__setattr__(p,'id',99);return True
        f.service=InterceptExecutionReadService(f.reader,authorize=lambda *a:True,raw_authorize=lambda *a:True,parent_authorize=parent,current=lambda *a:True,lifecycle_guard=f.service.guard)
        self.assertEqual(f.service.handle(f.request).status,403)
        self.assertFalse(any(q.sql.startswith('SELECT exploration_id') or 'FROM activity WHERE' in q.sql for q in f.queries))
    def test_guard_exit_original_approval_remap_cannot_release_snapshot(self):
        f=self.fixture()
        f.guard_exit=lambda:setattr(f,'detail',(*f.detail[:4],'6',*f.detail[5:]))
        response=f.service.handle(f.request)
        self.assertEqual(response.status,403);self.assertNotIn(b'synthetic',response.body)
    def test_serializer_activity_change_refuses_instead_of_retargeting(self):
        f=self.fixture()
        def encode(*args):
            response=json_response(*args)
            f.activity=[(*row[:8],'changed secret',*row[9:]) for row in f.activity]
            return response
        with patch('artex.server.intercept_execution_read.json_response',encode):response=f.service.handle(f.request)
        self.assertEqual(response.status,403);self.assertNotIn(b'synthetic',response.body)
    def test_existing_record_ignores_hint_no_existence_query(self):
        f=self.fixture();response=f.service.handle(Request('GET',f.request.target+'?conversation=999'))
        self.assertEqual(response.status,200);self.assertIsNone(response.json()['conversation_id'])
        self.assertFalse(any(q.sql.startswith('SELECT EXISTS') for q in f.queries))
        self.assertTrue(all(p.kind=='task' for p in f.parents))
    def test_final_parent_revocation_withholds_activity(self):
        f=self.fixture();f.guard_exit=lambda:setattr(f,'parent_allowed',False)
        response=f.service.handle(f.request)
        self.assertEqual(response.status,403);self.assertNotIn(b'result',response.body)
    def test_pair_worker_tool_segment_mismatches_never_match_command_text(self):
        for field,value in ((3,'other'),(5,'OtherTool'),(10,9)):
            f=self.fixture();row=list(f.activity[1]);row[field]=value;f.activity[1]=tuple(row)
            self.assertEqual(f.service.handle(f.request).status,409)

if __name__=='__main__':unittest.main()

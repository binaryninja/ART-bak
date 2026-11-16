"""Actual store/service wrapper regression: no SQL repeats or late outer grant."""
import unittest
from contextlib import nullcontext
import test_task_findings as fixture
from artex.db.task_findings import TaskFindingsStore
from artex.server.task_findings import TaskFindingsService
from artex.server.task_runtime_reads import TaskRuntimeReads,TaskReadScope
from artex.server.core import Request,Router
class Tests(unittest.TestCase):
    def make(self,mode):
        self.f=fixture.Tests();self.f.make();self.source=True;self.projected=False;self.calls=[]
        self.f.coverage.authorize=lambda *a:self.source
        self.f.service=TaskFindingsService(TaskFindingsStore(self.f.coverage,asset_authorize=lambda *a:True),authorize=lambda *a:True)
        def outer(req,s,op):
            self.calls.append(op)
            if self.projected:
                if mode=='revoke':self.source=False
                elif mode=='replace':self.reads.authorize=lambda *a:True
            return True
        self.reads=TaskRuntimeReads(self.f.f.db,TaskReadScope(5,9,'r1'),authorize=outer,current=lambda s:True,
            lifecycle_guard=lambda s:nullcontext(),runtime_snapshot=lambda s:None,findings_service=self.f.service)
    def run_projection(self):
        from unittest.mock import patch
        from artex.server import task_findings
        original=task_findings.json_response
        def project(*args):
            value=original(*args);self.projected=True;return value
        with patch.object(task_findings,'json_response',project):
            return self.reads._read(Request('GET','/api/exploration/findings?task=5'),'findings',Router(lambda r:True))
    def test_final_outer_callback_revokes_source_no_disclosure(self):
        self.make('revoke');r=self.run_projection()
        self.assertTrue(self.projected);self.assertEqual(r.status,403);self.assertNotIn(b'synthetic',r.body)
    def test_outer_callback_identity_replacement_refused(self):
        self.make('replace');r=self.run_projection()
        self.assertTrue(self.projected);self.assertNotEqual(r.status,200);self.assertNotIn(b'synthetic',r.body)
    def test_positive_actual_projection_does_not_repeat_sql(self):
        self.make('none');r=self.run_projection()
        self.assertEqual(r.status,200);self.assertIn(b'synthetic',r.body)
        self.assertEqual(sum(q is not None for _,q in self.f.f.db.calls),4)

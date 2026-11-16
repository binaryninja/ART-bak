"""Published findings final-disclosure regression; synthetic SQL only."""
import unittest
from contextlib import contextmanager
from unittest.mock import patch
import test_task_findings as fixture
import test_task_coverage as context
from artex.db.task_findings import TaskFindingsStore
from artex.server.task_findings import TaskFindingsService
from artex.server import task_findings
from artex.server.core import Request

class FindingsFinalBoundaryReview(unittest.TestCase):
    def test_final_asset_callback_revokes_source(self):
        self.run_case('asset')
    def test_final_guard_exit_revokes_source(self):
        self.run_case('guard')
    def run_case(self,mode):
        state={'source':True,'serialized':False};events=[]
        @contextmanager
        def guard(s):
            yield
            if state['serialized'] and mode=='guard':state['source']=False;events.append('guard')
        def assets(*a):
            if state['serialized'] and mode=='asset':state['source']=False;events.append('asset')
            return True
        f=context.CoverageTests()
        coverage=f.make([context.PARENT,[],[fixture.ROW],[fixture.ASSET]],
            authorize=lambda *a:state['source'],lifecycle_guard=guard)
        store=TaskFindingsStore(coverage,asset_authorize=assets)
        service=TaskFindingsService(store,authorize=lambda *a:True)
        original=task_findings.json_response
        def serialize(*a,**kw):
            response=original(*a,**kw);state['serialized']=True;return response
        with patch.object(task_findings,'json_response',serialize):
            response=service.handle(Request('GET','/api/exploration/findings?task=5'))
        self.assertTrue(events)
        self.assertEqual(response.status,403)
        self.assertNotIn(b'synthetic',response.body)

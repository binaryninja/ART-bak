"""Independent actual coverage service/store disclosure contract."""
import unittest
from contextlib import nullcontext
from unittest.mock import patch
from artex.server.task_coverage_reads import TaskCoverageReads
from artex.server.task_runtime_reads import TaskReadScope
from artex.server.core import Request
from test_tasks_store import Fake
from test_task_coverage import asset

class CoverageReview(unittest.TestCase):
    def make(self,*,asset_grant=None):
        self.source=True;self.request=True;self.selections=[]
        self.db=Fake([[(5,9,True)],[],[asset(3,'ip',ip='192.0.2.1')]])
        return TaskCoverageReads(self.db,TaskReadScope(5,9,'r'),authorize=lambda *a:self.source,
            request_authorize=lambda *a:self.request,asset_authorize=asset_grant or (lambda s,selection,ids:self.selections.append((selection,ids)) or True),
            current=lambda s:True,lifecycle_guard=lambda s:nullcontext())
    def test_source_revoked_during_serialization_hides_graph(self):
        svc=self.make();from artex.server import task_coverage
        original=task_coverage.json_response;called=[]
        def serialize(*a,**kw):
            value=original(*a,**kw);called.append(True);self.source=False;return value
        with patch.object(task_coverage,'json_response',serialize):
            result=svc.handle('graph',Request('GET','/api/tasks/5/coverage-graph'))
        self.assertTrue(called);self.assertEqual(result.status,403);self.assertNotIn(b'192.0.2.1',result.body)
    def test_asset_callback_revokes_source_before_disclosure(self):
        def asset_grant(s,selection,ids):self.source=False;return True
        svc=self.make(asset_grant=asset_grant)
        result=svc.handle('graph',Request('GET','/api/tasks/5/coverage-graph'))
        self.assertEqual(result.status,403);self.assertNotIn(b'192.0.2.1',result.body)
    def test_graph_assets_are_concrete_selected_ids(self):
        svc=self.make();result=svc.handle('graph',Request('GET','/api/tasks/5/coverage-graph'))
        self.assertEqual(result.status,200)
        self.assertEqual([(s.action,s.asset_id,ids) for s,ids in self.selections],[('graph',None,(3,))])

"""Code-owned components over inert SQL; no inferred authority or activity."""
import unittest
from contextlib import nullcontext
from artex.server.task_read_builder import TaskReadBuilder,TaskReadServices
from artex.server.task_runtime_reads import TaskReadScope
from artex.db.task_activity_read import ActivitySelection,ActivityDenied
from artex.server.core import Request
from test_tasks_store import Fake

class Tests(unittest.TestCase):
    def setUp(self):
        self.db=Fake([]);self.calls=[];self.scope=TaskReadScope(5,9,'read-policy')
        def yes(*a):self.calls.append(a);return True
        self.args=dict(request_authorize=yes,context_authorize=yes,asset_authorize=yes,
            runtime_snapshot=lambda s,selector:None,activity_authorize=yes,activity_raw_authorize=yes,
            activity_request_authorize=yes,stream_authorize=yes)
    def build(self,**changes):
        self.args.update(changes);self.builder=TaskReadBuilder(self.db,**self.args)
        return self.builder.build(self.scope,current=lambda s:True,lifecycle_guard=lambda s:nullcontext())
    def test_build_inert_actual_components_exact_shared_scope(self):
        services=self.build();self.assertIsInstance(services,TaskReadServices)
        self.assertEqual(self.db.calls,[]);self.assertEqual(self.calls,[])
        self.assertIs(services.reads.executor,self.db);self.assertIs(services.activity.store.executor,self.db)
        self.assertEqual(services.activity.store.scope.task_id,5)
        self.assertEqual(services.reads.findings_service.store.scope.exploration_id,9)
    def test_raw_activity_denial_does_not_become_read_allow(self):
        services=self.build(activity_raw_authorize=lambda s,selection:False)
        result=services.activity.handle('list',Request('GET','/api/exploration/activity?task=5&since=13'),{})
        self.assertEqual(result.status,403);self.assertEqual(self.db.calls,[])
    def test_activity_selection_preserves_original_cursor(self):
        selected=[]
        services=self.build(activity_request_authorize=lambda req,s,selection:selected.append(selection) or False)
        result=services.activity.handle('list',Request('GET','/api/exploration/activity?task=5&since=13&intent=7'),{})
        self.assertEqual(result.status,403)
        self.assertEqual((selected[0].since,selected[0].intent),(13,7));self.assertEqual(self.db.calls,[])
    def test_foreign_scope_cannot_retarget_existing_activity(self):
        services=self.build()
        object.__setattr__(self.scope,'task_id',6)
        with self.assertRaises(PermissionError):services.activity.store.check(services.activity.store.pins())
        self.assertEqual(self.db.calls,[])
    def test_required_independent_grant_not_defaulted(self):
        with self.assertRaises(ValueError):self.build(asset_authorize=None)
        self.assertEqual(self.db.calls,[])
    def test_perrequest_bundle_not_cached_across_tasks(self):
        first=self.build()
        second=self.builder.build(TaskReadScope(6,10,'read-policy'),current=lambda s:True,lifecycle_guard=lambda s:nullcontext())
        self.assertIsNot(first.reads,second.reads)
        self.assertEqual(first.activity.store.scope.task_id,5);self.assertEqual(second.activity.store.scope.task_id,6)
        self.assertEqual(self.db.calls,[])

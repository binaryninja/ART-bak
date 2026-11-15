"""Explicit composition over actual authored components; no live transport."""
import unittest,tempfile
from contextlib import nullcontext
from dataclasses import replace
import test_task_providers as profiles
from artex.agent.task_runtime import TaskRuntimeAssembly
from artex.chat_transcripts import ChatTranscriptAdapter
from artex.transcripts import TranscriptStore
from artex.agent.runtime import Grant
from artex.agent.engine import EngineScope,TaskEngineHandle
from artex.manager import CancellationToken,RuntimeUnavailable
from artex.agent.goals import GoalScope,GoalPlanner

class Tests(unittest.TestCase):
    def setUp(self):
        self.p=profiles.Tests();self.p.setUp();self.p.db.transaction=lambda:nullcontext(self.p.db);self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.transcript=ChatTranscriptAdapter(TranscriptStore(self.tmp.name))
        self.policy_calls=[]
        self.assembly=TaskRuntimeAssembly(self.p.db,providers=self.p.resolver,transcript=self.transcript,
            prompts={'goals':'Explicit goal system','planner':'Explicit planner system','worker':'Explicit worker system'},
            policy_revision='policy-1',current=lambda *a:self.policy_calls.append(a) or True,
            lifecycle_guard=lambda *a:nullcontext(),authorize=lambda *a:Grant(True,True,True),
            asset_authorize=lambda *a:Grant(True,True,True),validate_input=lambda *a:True,
            graph_catalog={'planner':[],'worker':[]})
    def test_constructor_inert_configuration_no_provider_and_provisional_matches(self):
        self.assertEqual(self.p.reads,[]);self.assertEqual(self.policy_calls,[])
        provisional=self.assembly.bootstrap_configuration({'provisional':True,'options':{'llm_profile_ids':[2]}})
        actual=self.assembly.bootstrap_configuration(self.p.snapshot)
        self.assertEqual(actual,provisional);self.assertEqual(self.p.calls,[]);self.assertEqual(self.p.construct,[])
        self.assertNotIn('inert-not-a-key',repr(actual))
    def test_roles_are_distinct_and_config_change_invalidates(self):
        conf=self.assembly.bootstrap_configuration(self.p.snapshot)
        self.assertTrue(self.assembly._bootstrap_current(5,9,conf.policy_revision))
        self.p.rows[2]=replace(self.p.rows[2],model='different')
        self.assertFalse(self.assembly._bootstrap_current(5,9,conf.policy_revision))
        self.assertEqual(self.p.calls,[])
    def test_actual_goal_factory_uses_owned_callbacks(self):
        conf=self.assembly.bootstrap_configuration(self.p.snapshot)
        scope=GoalScope(5,9,71,conf.policy_revision,conf.model,conf.system)
        current=lambda s:False;guard=lambda s:nullcontext()
        planner=self.assembly._goals(scope,current,guard)
        self.assertIsInstance(planner,GoalPlanner);self.assertIs(planner.current,current)
        self.assertIs(planner.guard,guard);self.assertIs(planner.store.executor,self.p.db)
        with self.assertRaises(Exception):planner.resolver(scope,conf.model)
        self.assertEqual(self.p.calls,[])
    def test_actual_engine_assembled_without_call_or_owner_access(self):
        conf=self.assembly.bootstrap_configuration(self.p.snapshot);token=CancellationToken()
        scope=EngineScope(5,9,71,conf.policy_revision)
        def current(*args):raise AssertionError('inert child cannot execute yet')
        guard=lambda s:nullcontext()
        handle=self.assembly.engine_factory(self.p.snapshot,token,scope,current,guard)
        self.assertIsInstance(handle,TaskEngineHandle);self.assertIs(handle.current,current)
        self.assertIs(handle.guard,guard);self.assertIs(handle.token,token)
        self.assertIs(handle.worker.store.executor,handle.store.executor)
        self.assertNotEqual(handle.worker_model.identity,handle.planner_scope.model.identity)
        self.assertEqual(self.p.calls,[]);self.assertEqual(self.p.construct,[])
    def test_config_drift_prevents_engine_construction(self):
        conf=self.assembly.bootstrap_configuration(self.p.snapshot)
        self.p.rows[2]=replace(self.p.rows[2],model='changed')
        with self.assertRaises(RuntimeUnavailable):self.assembly.engine_factory(self.p.snapshot,CancellationToken(),EngineScope(5,9,71,conf.policy_revision),lambda *a:True,lambda *a:nullcontext())
    def test_executor_rebind_and_initialized_adoption_fail_closed(self):
        self.p.tasks.executor=object()
        with self.assertRaises(RuntimeUnavailable):self.assembly.validate_bindings()
        with self.assertRaises(RuntimeUnavailable):self.assembly.initialized_readiness({})

    def test_host_policy_revocation_stops_planner_boundary(self):
        conf=self.assembly.bootstrap_configuration(self.p.snapshot)
        scope=EngineScope(5,9,71,conf.policy_revision)
        allowed=[True]
        # Use original callback identity with mutable policy state.
        self.policy_calls.clear()
        original=self.assembly._current
        # Rebuilding is explicit; replacing an assembled callback is forbidden.
        a=TaskRuntimeAssembly(self.p.db,providers=self.p.resolver,transcript=self.transcript,
            prompts=self.assembly._prompts,policy_revision='policy-1',current=lambda *args:allowed[0],
            lifecycle_guard=lambda *args:nullcontext(),authorize=lambda *args:Grant(True,True,True),
            asset_authorize=lambda *args:Grant(True,True,True),validate_input=lambda *args:True,
            graph_catalog={'planner':[],'worker':[]})
        conf=a.bootstrap_configuration(self.p.snapshot)
        handle=a.engine_factory(self.p.snapshot,CancellationToken(),EngineScope(5,9,71,conf.policy_revision),lambda *a:True,lambda *a:nullcontext())
        self.assertTrue(handle.planner.current(handle.planner_scope))
        allowed[0]=False
        self.assertFalse(handle.planner.current(handle.planner_scope))
        self.assertEqual(self.p.calls,[])

class OuterPolicyReview(unittest.TestCase):
    def test_completed_graph_cannot_settle_after_bootstrap_policy_revoked(self):
        import test_bootstrap_handle as fixture
        f=fixture.BootstrapHandleTests();f.setUp()
        try:
            allowed=[True]
            f.coordinator.current=lambda *args:allowed[0]
            h=f.start();f.advance(h);f.f.met()
            allowed[0]=False
            from artex.agent.bootstrap import BootstrapUnresolved
            with self.assertRaises(BootstrapUnresolved):h.settle_completed(f.manager.complete)
            self.assertNotEqual(f.manager.status('5'),'done')
        finally:
            if hasattr(f,'tearDown'):f.tearDown()
            f.doCleanups()

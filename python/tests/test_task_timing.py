import unittest
from artex.agent.task_timing import TaskTimingPolicy,PreparedTaskTiming,TimingUnavailable
from artex.agent.engine import TaskEngineHandle
from artex.agent.chat import ChatBinding
from artex.agent.runtime import Grant
import test_bootstrap_handle as fixture

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.BootstrapHandleTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.calls=[];self.timing=None;self.clock=lambda:100.
        from datetime import datetime,timezone
        self.policy=TaskTimingPolicy('timing-v1',self.clock,lambda:datetime.now(timezone.utc),
            lambda *a:self.calls.append('stamp') or True,lambda *a:self.calls.append('dispatch') or Grant(True,True,True))
        old=self.f.child_factory
        def child(row,token,scope,current,guard):
            base=old(row,token,scope,current,guard)
            # Authored fixture configures existing inert components, not active work.
            base.planner.clock=base.planner._clock=self.clock
            base.worker.clock=base.worker._clock=self.clock
            self.timing=PreparedTaskTiming(scope,self.policy,configuration_current=lambda s:True)
            return TaskEngineHandle(scope,token,store=base.store,planner=base.planner,planner_scope=base.planner_scope,
                worker=base.worker,worker_model=base.worker_model,worker_system=base.worker_system,
                owner=base.owner,current=current,lifecycle_guard=guard,single_process=True,timing=self.timing)
        self.f.child_factory=child
    def prepare(self):
        self.h=self.f.start();self.f.advance(self.h);return self.h
    def test_zero_binding_inert_no_kernel_or_grants(self):
        h=self.prepare();self.assertEqual(h.phase,'engine');self.assertIsNone(self.timing.kernel);self.assertEqual(self.calls,[])
        binding=ChatBinding(h._child.planner_scope.model,self.f.f.pp)
        self.assertIs(self.timing.wrap(h._child.planner_scope,'planner',binding),binding)
    def test_rebind_denied(self):
        h=self.prepare()
        with self.assertRaises(TimingUnavailable):h._child.bind_timing(self.f.manager,h)
    def test_wrong_clock_retains_child_unresolved(self):
        original=self.f.child_factory
        def wrong(*a):
            child=original(*a);child.worker.clock=lambda:0.;return child
        self.f.child_factory=wrong;h=self.f.start()
        with self.assertRaises(Exception):self.f.advance(h)
        self.assertTrue(h.unresolved);self.assertIsNotNone(h._child);self.assertEqual(self.calls,[])
    def test_policy_callback_rebind_denied(self):
        h=self.prepare();object.__setattr__(self.policy,'clock',lambda:0.)
        with self.assertRaises(TimingUnavailable):self.timing.validate_bindings()
    def test_goal_role_denied(self):
        h=self.prepare();binding=ChatBinding(h._child.planner_scope.model,self.f.f.pp)
        with self.assertRaises(TimingUnavailable):self.timing.wrap(h._child.planner_scope,'goals',binding)
    def test_original_child_replacement_denied(self):
        h=self.prepare();h._child=object()
        with self.assertRaises(TimingUnavailable):self.timing.validate_bindings()

    def test_role_scope_pairing_rejected_before_configuration_callback(self):
        h=self.prepare();scope=h._child.planner_scope
        binding=ChatBinding(scope.model,self.f.f.pp)
        with self.assertRaises(TimingUnavailable):self.timing.wrap(scope,'worker',binding)
        from artex.agent.worker import WorkerScope
        worker=WorkerScope(scope.task_id,scope.exploration_id,scope.run_id,scope.revision,11,1,'owner',scope.model,scope.system)
        with self.assertRaises(TimingUnavailable):self.timing.wrap(worker,'planner',binding)
        self.assertIs(self.timing.wrap(worker,'worker',binding),binding)

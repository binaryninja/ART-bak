"""Independent inert post-prepare timing bindings; positive pins are synthetic."""
import unittest
from dataclasses import replace
from artex.agent.chat import ChatBinding
from artex.agent.task_timing import TimingUnavailable
import test_task_timing as fixture

class TimingReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_synthetic_positive_binding_creates_one_kernel_without_stamp(self):
        f=self.f;original=f.f.child_factory
        def child(*args):
            h=f.h
            h._configuration=(*h._configuration[:-1],10);h._task['timeout_seconds']=10
            f.f.manager._tasks['5']['timeout_seconds']=10
            return original(*args)
        f.f.child_factory=child;f.h=f.f.start();f.f.advance(f.h)
        timing=f.timing;kernel=timing.kernel
        self.assertIsNotNone(kernel);self.assertIsNone(timing.store.lease)
        self.assertEqual(f.calls,[])
        planner=f.h._child.planner_scope
        one=timing.wrap(planner,'planner',ChatBinding(planner.model,f.f.f.pp))
        from artex.agent.worker import WorkerScope
        model=replace(planner.model,identity='distinct-worker',model='worker-model',profile_id=22)
        worker=WorkerScope(planner.task_id,planner.exploration_id,planner.run_id,planner.revision,3,0,'fixture',model,'worker')
        two=timing.wrap(worker,'worker',ChatBinding(model,f.f.f.wp))
        self.assertIs(one.provider.dispatch_clock.generation_clock,kernel)
        self.assertIs(two.provider.dispatch_clock.generation_clock,kernel)
        self.assertEqual(f.calls,[]);self.assertIsNone(timing.store.lease)
    def test_partial_bind_failure_cannot_retry_or_release_outer(self):
        f=self.f;original=f.f.child_factory
        def child(*args):
            value=original(*args);value.worker.clock=lambda:99.;return value
        f.f.child_factory=child;h=f.f.start()
        with self.assertRaises(Exception):f.f.advance(h)
        self.assertTrue(h.unresolved);self.assertIsNotNone(h._child)
        with self.assertRaises(TimingUnavailable):f.timing.bind(f.f.manager,h,h._child)
        self.assertFalse(f.f.host.drain(0).all_settled)
    def test_configuration_callback_replacement_during_construction_denied(self):
        f=self.f;original=f.f.child_factory
        def child(*args):
            result=original(*args)
            def current(scope):
                f.h._child=object()
                return True
            # No modification to private pins: replacement itself must fail closed.
            f.timing.configuration_current=current
            return result
        f.f.child_factory=child;f.h=f.f.start()
        with self.assertRaises(Exception):f.f.advance(f.h)
        self.assertTrue(f.h.unresolved)
    def test_planner_scope_cannot_be_relabelled_worker_even_same_model(self):
        f=self.f;h=f.prepare();scope=h._child.planner_scope
        binding=ChatBinding(scope.model,f.f.f.pp)
        with self.assertRaises(TimingUnavailable):f.timing.wrap(scope,'worker',binding)
        self.assertEqual(f.calls,[])

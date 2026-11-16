"""Independent opt-in assembly timing pins, no timed admission or transport."""
import unittest
from dataclasses import replace
from artex.manager import RuntimeUnavailable
import test_task_runtime_timing as fixture

class AssemblyTimingReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_resolver_callback_mutating_timing_policy_cannot_return_binding(self):
        f=self.f;a=f.make(f.policy);engine=f.engine(a)
        original=a.providers.bind
        def bind(*args,**kwargs):
            binding=original(*args,**kwargs)
            object.__setattr__(f.policy,'dispatch_authorize',lambda *a:True)
            return binding
        a.providers.bind=bind
        with self.assertRaises(RuntimeUnavailable):
            engine.planner.provider(engine.planner_scope,engine.planner_scope.model)
        self.assertEqual(f.f.p.construct,[]);self.assertEqual(f.calls,[])
    def test_clock_change_after_engine_creation_denies_worker_resolver(self):
        f=self.f;a=f.make(f.policy);engine=f.engine(a)
        from artex.agent.worker import WorkerScope
        scope=WorkerScope(engine.scope.task_id,engine.scope.exploration_id,engine.scope.run_id,
            engine.scope.revision,3,0,engine.owner,engine.worker_model,engine.worker_system)
        object.__setattr__(f.policy,'clock',lambda:100.)
        with self.assertRaises(RuntimeUnavailable):engine.worker.provider(scope,scope.model)
        self.assertEqual(f.f.p.construct,[]);self.assertEqual(f.calls,[])

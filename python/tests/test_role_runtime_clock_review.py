"""Independent role clock pins across prepared wake and model gate callbacks."""
import unittest
import test_role_runtime_clock as fixture
from artex.agent.runtime import Grant

class RoleClockReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.addCleanup(self.f.doCleanups)
    def test_prepared_planner_wake_rejects_late_equal_epoch_clock(self):
        f=self.f.planner(lambda:100.)
        wake=f.core.prepare(f.scope,'prepared')
        f.core.clock=lambda:100.
        with self.assertRaises(Exception):f.core.run(wake)
        self.assertEqual(f.provider.calls,[])
    def test_worker_model_grant_cannot_replace_context_clock_owner(self):
        f=self.f.worker(lambda:100.)
        original=f.worker.authorize
        def grant(context,action):
            result=original(context,action)
            if action.kind=='model':f.worker.clock=lambda:100.
            return result
        f.worker.authorize=grant
        with self.assertRaises(Exception):f.worker.run(f.scope,'gate')
        self.assertEqual(f.provider.calls,[])
    def test_planner_gate_clock_swap_never_reaches_transport(self):
        f=self.f.planner(lambda:100.)
        original=f.core.authorize
        def grant(context,action):
            result=original(context,action)
            if action.kind=='model':f.core.clock=lambda:100.
            return result
        f.core.authorize=grant
        with self.assertRaises(Exception):f.core.run(f.core.prepare(f.scope,'gate'))
        self.assertEqual(f.provider.calls,[])

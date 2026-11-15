"""Independent deadline budget tests with inert authored fixtures only."""
import unittest
from datetime import timedelta
import test_dispatch_deadline as fixture
from artex.agent.deadline import TaskBudgetExpired
class Review(unittest.TestCase):
    def make(self):
        f=fixture.DispatchDeadlineTests();f.setUp();self.addCleanup(f.doCleanups);return f
    def test_final_profile_callback_elapsed_time_cannot_return_expired_budget(self):
        f=self.make();calls=[]
        def current(model):
            calls.append(1)
            if len(calls)==3:f.mono+=11;f.wall+=timedelta(seconds=11)
            return True
        f.helper=f.make(binding_current=current)
        with self.assertRaises(TaskBudgetExpired):f.call()
    def test_final_profile_callback_time_deducted_from_remaining(self):
        f=self.make();calls=[]
        def current(model):
            calls.append(1)
            if len(calls)==3:f.mono+=3;f.wall+=timedelta(seconds=3)
            return True
        f.helper=f.make(binding_current=current)
        result=f.call()
        self.assertEqual(result.remaining_seconds,7)
        self.assertEqual(result.monotonic_deadline,110)
    def test_final_wall_clamp_reports_task_limited(self):
        f=self.make();calls=[]
        def current(model):
            calls.append(1)
            if len(calls)==3:f.wall+=timedelta(seconds=8)
            return True
        f.helper=f.make(binding_current=current)
        result=f.call(own_budget_seconds=5)
        self.assertEqual(result.remaining_seconds,2)
        self.assertTrue(result.task_limited)

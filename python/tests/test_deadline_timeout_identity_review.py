"""Explicit synthetic positive prepared pins, never positive runtime enablement."""
import unittest
import test_dispatch_deadline as fixture
from artex.agent.deadline import DispatchClockUnavailable

class Review(unittest.TestCase):
    def test_zero_helper_cannot_bypass_positive_prepared_configuration(self):
        f=fixture.DispatchDeadlineTests();f.setUp();self.addCleanup(f.doCleanups)
        f.helper=f.make(0)
        with self.assertRaises(DispatchClockUnavailable):f.call()
        self.assertEqual(f.f.sql,[])
    def test_public_positive_stamp_cannot_bypass_original_zero_pin(self):
        import test_task_deadline as baseline
        from artex.db.task_deadline import DeadlineConflict
        f=baseline.DeadlineTests();f.setUp();self.addCleanup(f.doCleanups)
        f.timeout=10;f.f.f.manager._tasks['5']['timeout_seconds']=10
        with self.assertRaises(DeadlineConflict):f.store.stamp(10)
        self.assertEqual(f.sql,[])
        self.assertEqual(f.f.f.manager.deadline_owners(),())

    def test_public_positive_stamp_registers_real_owner(self):
        f=fixture.DispatchDeadlineTests();f.setUp();self.addCleanup(f.doCleanups)
        clock=f.f.store.stamp(10)
        lease=f.f.store.lease
        self.assertIs(lease.clock,clock)
        self.assertTrue(f.f.f.f.manager.validate_deadline_owner(lease))
        self.assertEqual(f.f.f.f.manager.deadline_owners()[0].state,'attached')

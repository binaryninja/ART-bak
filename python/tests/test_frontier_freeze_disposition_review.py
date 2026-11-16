"""Independent freeze/poll handoff tests; actual owned fixture, synthetic timeout pins."""
import unittest
import test_frontier_runner as fixture

class FrontierFreezeReview(unittest.TestCase):
    def make(self):
        f=fixture.Tests();self.addCleanup(f.doCleanups)
        x=f.make(True);x.host.runner.bind_frontier_completion(x.handle,x.component)
        return x
    def freeze(self,f):
        return f.host.freeze_deadline(f.handle.task_id,f.handle.token,f.handle,f.f.d.lease,timeout=0)
    def test_standalone_freeze_without_registered_observer_is_not_defer_authority(self):
        f=self.make();freeze,drained=self.freeze(f);self.assertTrue(drained)
        before=len(f.f.queries)
        with self.assertRaises(Exception):f.host.poll()
        self.assertIn(f.handle,f.host.runner.handles())
        self.assertFalse(f.component._used);self.assertIsNone(f.component._store)
        self.assertEqual(len(f.f.queries),before)
    def test_quarantined_freeze_remains_error_and_retained(self):
        f=self.make();freeze,drained=self.freeze(f)
        f.host.manager.quarantine_deadline_freeze(freeze)
        with self.assertRaises(Exception):f.host.poll()
        self.assertIn(f.handle,f.host.runner.handles());self.assertIsNone(f.component._store)
    def test_copied_lease_cannot_supply_defer_authority(self):
        from copy import copy
        f=self.make();freeze,drained=self.freeze(f)
        f.f.d._lease=copy(f.f.d.lease)
        with self.assertRaises(Exception):f.host.poll()
        self.assertIn(f.handle,f.host.runner.handles());self.assertIsNone(f.component._store)


import test_frontier_registered_expiry as registered
class RegisteredFreezeReview(unittest.TestCase):
    def setUp(self):
        self.f=registered.RegisteredExpiryTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_exact_registered_freeze_defers_repeatably_without_queries(self):
        f=self.f.f;before=len(f.f.f.f.db.calls)
        for _ in range(2):
            result=f.bundle.host.poll()
            self.assertFalse(result.all_settled);self.assertIn(f.handle,f.bundle.runner.handles())
        self.assertEqual(len(f.f.f.f.db.calls),before)
        self.assertFalse(f.actions.frontier._used)
    def test_failed_observer_cannot_suppress_frontier_errors(self):
        f=self.f.f;f.actions.observer._failed=True
        with self.assertRaises(Exception):f.bundle.host.poll()
        self.assertIn(f.handle,f.bundle.runner.handles());self.assertFalse(f.actions.frontier._used)
    def test_forged_transfer_is_not_defer_authority(self):
        f=self.f.f;f.actions.observer._transfer=object()
        with self.assertRaises(Exception):f.bundle.host.poll()
        self.assertIn(f.handle,f.bundle.runner.handles());self.assertFalse(f.actions.frontier._used)
    def test_copied_observer_lease_denies_even_with_original_freeze(self):
        from copy import copy
        f=self.f.f;f.actions.observer._lease=copy(f.actions.observer._lease)
        with self.assertRaises(Exception):f.bundle.host.poll()
        self.assertIn(f.handle,f.bundle.runner.handles())

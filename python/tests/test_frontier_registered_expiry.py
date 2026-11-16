"""Real host/registration/observer ownership; inert SQL, synthetic timeout pins."""
import unittest
from copy import copy
import test_timed_engine_branch as base

class RegisteredExpiryTests(unittest.TestCase):
    def setUp(self):
        self.f=base.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        f=self.f;clock=f.stamp_original_clock();db=f.f.f.f.db;old=db.fetch_all
        def query(q):
            if q.sql.startswith('SELECT timeout_seconds,first_run_at,deadline_at,deadline_at<=clock_timestamp()'):
                return [(10,clock.first_run_at,clock.deadline_at,True)]
            return old(q)
        db.fetch_all=query
        self.assertEqual(f.actions.observer.tick().phase,'ready')
    def test_registered_observer_freeze_defers_without_frontier_sql(self):
        f=self.f;before=len(f.f.f.f.db.calls)
        result=f.bundle.host.poll()
        self.assertFalse(result.all_settled);self.assertIn(f.handle,f.bundle.runner.handles())
        self.assertFalse(f.actions.frontier._used);self.assertIsNone(f.actions.frontier._store)
        self.assertEqual(len(f.f.f.f.db.calls),before)
    def test_quarantined_observer_freeze_remains_error(self):
        f=self.f;f.bundle.manager.quarantine_deadline_freeze(f.actions.observer._freeze)
        with self.assertRaises(Exception):f.bundle.host.poll()
        self.assertIn(f.handle,f.bundle.runner.handles());self.assertFalse(f.actions.frontier._used)
    def test_copied_freeze_is_not_retained_authority(self):
        f=self.f;f.actions.observer._freeze=copy(f.actions.observer._freeze)
        with self.assertRaises(Exception):f.bundle.host.poll()
        self.assertIn(f.handle,f.bundle.runner.handles());self.assertFalse(f.actions.frontier._used)
    def test_replaced_manager_predicate_does_not_supply_authority(self):
        f=self.f;f.bundle.manager.validate_deadline_observation=lambda *a:False
        with self.assertRaises(Exception):f.bundle.host.poll()
        self.assertFalse(f.actions.frontier._used)

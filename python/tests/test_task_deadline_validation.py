"""Read-only follow-up clock checks never stamp or adopt another generation."""
import unittest
from artex.db.task_deadline import DeadlineConflict
import test_dispatch_deadline as fixture

class DeadlineValidationTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.DispatchDeadlineTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.f.store.stamp(10)
        self.f.f.sql.clear()
    def test_readonly_lock_order_full_identity_and_no_update(self):
        value=self.f.f.store.validate_current(10)
        queries=self.f.f.sql
        self.assertIn('FOR SHARE',queries[0].sql);self.assertIn('FOR SHARE',queries[1].sql)
        self.assertEqual(queries[1].params[0:3],(5,9,71))
        self.assertEqual(value.deadline_at,self.f.deadline)
        self.assertFalse(any(q.sql.startswith('UPDATE') for q in queries))
    def test_missing_journal_blocks_cached_use_without_update(self):
        original=self.f.f.db.fetch_all
        self.f.f.db.fetch_all=lambda q:[] if q.sql.startswith('SELECT 1 FROM public.artex_task_bootstrap') else original(q)
        with self.assertRaises(DeadlineConflict):self.f.f.store.validate_current(10)
        self.assertFalse(any(q.sql.startswith('UPDATE') for q in self.f.f.sql))
    def test_authority_revocation_after_read_blocks_return(self):
        original=self.f.f.db.fetch_all
        def query(q):
            result=original(q)
            if q.sql.startswith('SELECT 1 FROM public.artex_task_bootstrap'):self.f.f.handle.token.cancel()
            return result
        self.f.f.db.fetch_all=query
        with self.assertRaises(DeadlineConflict):self.f.f.store.validate_current(10)

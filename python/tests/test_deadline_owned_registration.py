"""Real authored manager/handle, synthetic SQL clocks; no timeout runtime enabled."""
import unittest
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
import test_dispatch_deadline as fixture
from artex.db.task_deadline import DeadlineConflict,DeadlineUncertain
from artex.manager import TaskConflict

class OwnedClockTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.DispatchDeadlineTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.store=self.f.f.store;self.manager=self.f.f.f.f.manager;self.host=self.f.f.f.f.host
    def test_positive_public_stamp_registers_and_unowned_read_rejects(self):
        with self.assertRaises(DeadlineConflict):self.store.validate_current(10)
        self.assertEqual(self.f.f.sql,[])
        result=self.store.stamp(10)
        self.assertIs(self.store.lease.clock,result)
        self.assertTrue(self.manager.validate_deadline_owner(self.store.lease))
        self.assertEqual(self.store.validate_current(10),result)
        with self.assertRaises(DeadlineConflict):self.store.stamp(10)

    def test_stamp_registers_exact_lease_and_helper_revalidates_without_restamp(self):
        self.f.call();lease=self.store.lease
        self.assertTrue(self.manager.validate_deadline_owner(lease))
        self.assertIs(self.store.validate_lease(lease),lease.clock)
        self.assertEqual(self.manager.deadline_owner_phase(lease),'attached')
        self.f.call()
        self.assertEqual(sum(q.sql.startswith('UPDATE tasks SET first_run_at') for q in self.f.f.sql),1)
        with self.assertRaises(DeadlineConflict):self.store.stamp_owned(10)
    def test_pause_keeps_readonly_lease_but_not_execution_authority(self):
        self.f.call();lease=self.store.lease;handle=self.f.f.handle
        self.host.pause('5')
        self.assertEqual(self.manager.deadline_owner_phase(lease),'suspended')
        self.assertIs(self.store.validate_lease(lease),lease.clock)
        self.assertFalse(self.manager.owns_generation('5',handle.token,handle,allow_cancelled=True))
        with self.assertRaises(DeadlineConflict):self.store.validate_current(10)
        self.assertFalse(self.manager.complete('5',handle.token))
        with self.assertRaises(TaskConflict):self.manager.start('5')
    def test_no_arbitrary_receipt_or_copied_lease_adoption(self):
        self.f.call();lease=self.store.lease
        copied=replace(lease)
        self.assertFalse(self.manager.validate_deadline_owner(copied))
        with self.assertRaises(DeadlineConflict):self.store.validate_lease(copied)
        with self.assertRaises(DeadlineConflict):self.store.consume_stamp_evidence(object(),lease.clock)
    def test_clean_prewrite_failure_releases_reservation(self):
        self.f.f.timeout=11
        with self.assertRaises(DeadlineConflict):self.store.stamp_owned(10)
        self.assertEqual(self.manager.deadline_owners(),())
        self.assertFalse(any(q.sql.startswith('UPDATE tasks SET first_run_at') for q in self.f.f.sql))
    def test_write_exception_quarantines_and_cannot_retry(self):
        original=self.f.f.db.fetch_all
        def fetch(q):
            if q.sql.startswith('UPDATE tasks SET first_run_at'):raise RuntimeError('synthetic SQL uncertainty')
            return original(q)
        self.f.f.db.fetch_all=fetch
        with self.assertRaises(DeadlineUncertain):self.store.stamp_owned(10)
        self.assertEqual(self.manager.deadline_owners()[0].state,'quarantined')
        count=len(self.f.f.sql)
        with self.assertRaises(DeadlineUncertain):self.store.stamp_owned(10)
        self.assertEqual(len(self.f.f.sql),count)
    def test_cancel_after_commit_keeps_quarantined_registration(self):
        original=self.f.f.db.transaction
        @contextmanager
        def tx():
            with original() as value:yield value
            self.f.f.handle.token.cancel('synthetic after commit')
        self.f.f.db.transaction=tx
        with self.assertRaises(DeadlineUncertain):self.store.stamp_owned(10)
        self.assertEqual(self.manager.deadline_owners()[0].state,'quarantined')
        self.assertIsNone(self.store.lease)
    def test_lease_clock_or_journal_change_rejected_without_update(self):
        self.f.call();lease=self.store.lease;self.f.f.sql.clear()
        self.f.deadline+=timedelta(seconds=1)
        with self.assertRaises(DeadlineConflict):self.store.validate_lease(lease)
        self.assertFalse(any(q.sql.startswith('UPDATE') for q in self.f.f.sql))
    def test_registry_sql_only_timeout_change_cannot_adopt_prepared_zero(self):
        handle=self.f.f.handle
        handle._configuration=(*handle._configuration[:-1],0)
        handle._task['timeout_seconds']=0
        self.f.f.sql.clear()
        with self.assertRaises(DeadlineConflict):self.store.stamp_owned(10)
        self.assertEqual(self.f.f.sql,[]);self.assertEqual(self.manager.deadline_owners(),())
    def test_stamp_policy_replacement_after_commit_cannot_install(self):
        original=self.f.f.db.transaction;live=[True]
        self.store.authorize=lambda *a:live[0]
        @contextmanager
        def tx():
            with original() as value:yield value
            live[0]=False;self.store.authorize=lambda *a:True
        self.f.f.db.transaction=tx
        with self.assertRaises(DeadlineUncertain):self.store.stamp_owned(10)
        self.assertEqual(self.manager.deadline_owners()[0].state,'quarantined')
        self.assertIsNone(self.store.lease)
    def test_suspended_lease_policy_replacement_during_read_hides_clock(self):
        self.f.call();lease=self.store.lease;self.host.pause('5')
        original=self.f.f.db.fetch_all;live=[True]
        self.store.authorize=lambda *a:live[0]
        def fetch(query):
            result=original(query)
            if query.sql.startswith('SELECT 1 FROM public.artex_task_bootstrap'):
                live[0]=False;self.store.authorize=lambda *a:True
            return result
        self.f.f.db.fetch_all=fetch
        with self.assertRaises(DeadlineConflict):self.store.validate_lease(lease)

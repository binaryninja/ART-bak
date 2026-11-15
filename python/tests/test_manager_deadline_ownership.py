"""Synthetic ownership handshake, NOT positive-timeout runtime enablement.

Prepare through real authored bootstrap first, then inject a synthetic timed task
row/clock without dispatching a provider. All persistence is an inert executor.
"""
from contextlib import contextmanager
from datetime import timedelta
import unittest
from artex.manager import TaskConflict,DeadlineReservation,DeadlineLease
from artex.db.task_deadline import DeadlineConflict,DeadlineUncertain
import test_task_deadline as fixture


class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.DeadlineTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.manager=self.f.f.f.manager;self.host=self.f.f.f.host;self.runner=self.f.f.runner
        self.store=self.f.store;self.h=self.f.handle
        # Explicit synthetic prepared pin: production constructors still reject this.
        self.h._configuration=(*self.h._configuration[:-1],30)
        self.h._task['timeout_seconds']=30
        self.f.timeout=30;self.manager._tasks['5']['timeout_seconds']=30
        row=list(self.f.db.row);row[18]=30;self.f.db.row=tuple(row)
        self.deadline=self.f.clock+timedelta(seconds=30)
        original=self.f.db.fetch_all
        def query(q):
            if q.sql.startswith('UPDATE tasks SET first_run_at'):
                self.f.sql.append(q);return [(self.f.clock,self.deadline)]
            if q.sql.startswith('SELECT timeout_seconds,first_run_at,deadline_at'):
                self.f.sql.append(q);return [(30,self.f.clock,self.deadline)]
            return original(q)
        self.f.db.fetch_all=query
    def reserve(self):
        return self.manager.reserve_deadline_owner('5',self.h.token,self.h,store=self.store,timeout_seconds=30)
    def test_pending_reservation_blocks_pause_retirement_and_duplicate(self):
        r=self.reserve()
        self.assertEqual(self.manager.deadline_owners()[0].state,'reserved')
        with self.assertRaises(TaskConflict):self.host.pause('5')
        self.assertFalse(self.h.token.cancelled)
        self.assertFalse(self.manager.acknowledge_deadline_retirement('5',self.h.token,self.h))
        with self.assertRaises(TaskConflict):self.reserve()
        with self.assertRaises(TaskConflict):self.manager.abort_deadline_reservation(DeadlineReservation('5'))
        self.manager.abort_deadline_reservation(r);self.assertEqual(self.manager.deadline_owners(),())
    def test_attempted_reservation_cannot_be_dropped(self):
        r=self.reserve();self.manager.mark_deadline_attempted(r)
        with self.assertRaises(TaskConflict):self.manager.abort_deadline_reservation(r)
        self.manager.quarantine_deadline_owner(r)
        self.assertEqual(self.manager.deadline_owners()[0].state,'quarantined')
        with self.assertRaises(TaskConflict):self.host.pause('5')
    def test_arbitrary_receipt_is_not_committed_evidence(self):
        r=self.reserve();self.manager.mark_deadline_attempted(r)
        with self.assertRaises(DeadlineConflict):self.manager.install_deadline_owner(r,object())
        self.assertEqual(self.manager.deadline_owners()[0].state,'quarantined')
    def test_real_store_handshake_pause_inventory_and_no_resume_adoption(self):
        clock=self.store.stamp_owned(30);lease=self.store.lease
        self.assertIs(lease.clock,clock);self.assertTrue(self.manager.validate_deadline_owner(lease))
        self.assertFalse(self.manager.validate_deadline_owner(DeadlineLease('5',lease.receipt,clock)))
        self.host.pause('5')
        self.assertEqual(self.manager.deadline_owner_phase(lease),'suspended')
        self.assertFalse(self.manager.owns_generation('5',self.h.token,self.h,allow_cancelled=True))
        self.assertIs(self.store.validate_lease(lease),clock)
        poll=self.host.poll();self.assertEqual(poll.settled,());self.assertEqual(self.runner.handles(),())
        self.assertEqual(self.manager.deadline_owners()[0].state,'suspended')
        self.assertFalse(self.manager.complete('5',self.h.token,'timeout'))
        self.f.sql.clear()
        with self.assertRaises(TaskConflict):self.host.start('5')
        self.assertEqual(self.f.sql,[])
    def test_pause_persistence_failure_quarantines_retained_owner(self):
        self.store.stamp_owned(30)
        def fail(*args):raise RuntimeError('synthetic persistence failure')
        self.manager.repository.apply_pause=fail
        with self.assertRaises(RuntimeError):self.host.pause('5')
        self.assertEqual(self.manager.deadline_owners()[0].state,'quarantined')
        self.assertFalse(self.manager.acknowledge_deadline_retirement('5',self.h.token,self.h))
    def test_handle_and_evidence_callbacks_outside_manager_lock(self):
        original=self.store.validate_reservation_owner
        def validate():
            self.assertFalse(self.manager._lock._is_owned());return original()
        self.store.validate_reservation_owner=validate
        consume=self.store.consume_stamp_evidence
        def evidence(*args):
            self.assertFalse(self.manager._lock._is_owned());return consume(*args)
        self.store.consume_stamp_evidence=evidence
        self.store.stamp_owned(30)
    def test_cancel_after_commit_before_install_quarantines(self):
        original=self.f.db.transaction
        @contextmanager
        def transaction():
            with original() as tx:yield tx
            self.h.token.cancel('synthetic pause race')
        self.f.db.transaction=transaction
        with self.assertRaises(DeadlineUncertain):self.store.stamp_owned(30)
        self.assertEqual(self.manager.deadline_owners()[0].state,'quarantined')
        self.assertFalse(self.manager.acknowledge_deadline_retirement('5',self.h.token,self.h))

    def test_pause_sql_outside_registry_lock(self):
        self.store.stamp_owned(30)
        original=self.manager.repository.apply_pause
        def pause(*args):
            self.assertFalse(self.manager._lock._is_owned())
            return original(*args)
        self.manager.repository.apply_pause=pause
        self.host.pause('5')
        self.assertEqual(self.manager.deadline_owners()[0].state,'suspended')

    def test_duplicate_concurrent_reservation_has_single_owner(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        barrier=Barrier(2)
        def reserve():
            barrier.wait(timeout=2)
            try:return self.reserve()
            except TaskConflict:return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(reserve) for _ in range(2)]
            results=[future.result(timeout=3) for future in futures]
        owners=[r for r in results if r is not None]
        self.assertEqual(len(owners),1);self.assertEqual(len(self.manager.deadline_owners()),1)
        self.manager.abort_deadline_reservation(owners[0])

    def test_retained_lease_rejects_changed_bootstrap_configuration(self):
        self.store.stamp_owned(30);self.h.policy_revision='replacement'
        self.assertFalse(self.manager.validate_deadline_owner(self.store.lease))
        self.assertFalse(self.manager.acknowledge_deadline_retirement('5',self.h.token,self.h))

    def test_registry_timeout_alone_cannot_upgrade_original_prepared_budget(self):
        self.h._configuration=(*self.h._configuration[:-1],0)
        self.h._task['timeout_seconds']=0
        with self.assertRaises(TaskConflict):self.reserve()
        self.assertEqual(self.manager.deadline_owners(),())
        self.assertEqual(self.f.sql,[])

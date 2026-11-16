"""Synthetic positive prepared pin only; real runtime positive gates unchanged."""
import unittest
from dataclasses import replace
from artex.agent.expiry_observer import OwnedExpiryObserver
from artex.agent.deadline import OwnedGenerationClock
from artex.db.task_deadline import DeadlineConflict
import test_dispatch_deadline as fixture

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.DispatchDeadlineTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.store=self.f.f.store;self.host=self.f.f.f.f.host;self.handle=self.store.handle
        self.kernel=OwnedGenerationClock(self.store,10,monotonic_clock=self.f.helper.monotonic_clock)
        self.f.helper=self.f.make(generation_clock=self.kernel,monotonic_clock=self.kernel.monotonic_clock)
        self.expired=False;self.allowed=True
        old=self.f.f.db.fetch_all
        def query(q):
            if 'deadline_at<=clock_timestamp()' in q.sql:
                self.f.f.sql.append(q)
                return [(10,self.f.f.clock,self.f.deadline,self.expired)]
            return old(q)
        self.f.f.db.fetch_all=query
        self.observer=OwnedExpiryObserver(self.host,self.kernel,authorize_observation=lambda r:self.allowed,authorize_freeze=lambda r:True)
    def stamp(self):self.f.call()
    def test_constructor_unstamped_no_sql(self):
        self.assertEqual(self.observer.tick().phase,'unstamped');self.assertEqual(self.f.f.sql,[])
    def test_local_early_expiry_waits_for_database(self):
        self.stamp();self.f.mono=111
        result=self.observer.tick();self.assertEqual(result.phase,'waiting_database');self.assertTrue(result.drained)
        self.assertEqual(self.host.manager.deadline_owners()[0].state,'drained')
        self.expired=True;self.assertEqual(self.observer.tick().phase,'ready')
        self.assertEqual(self.host.manager.deadline_owners()[0].state,'drained')
    def test_database_expiry_freezes_before_local_ceiling(self):
        self.stamp();self.expired=True
        self.assertEqual(self.observer.tick().phase,'ready')
        self.assertFalse(self.handle.token.cancelled)
    def test_pending_drain_retains_without_assessment(self):
        self.stamp();self.expired=True;old=self.handle.wait;self.handle.wait=lambda timeout:False
        self.assertEqual(self.observer.tick().phase,'draining')
        self.assertEqual(self.host.manager.deadline_owners()[0].state,'freezing')
        self.handle.wait=old;self.assertEqual(self.observer.tick().phase,'ready')
    def test_suspended_original_clock_not_reset(self):
        self.stamp();self.host.pause('5');self.expired=True
        self.assertEqual(self.observer.tick().phase,'ready')
        self.assertTrue(self.handle.token.cancelled)
    def test_copied_lease_and_freeze_denied(self):
        self.stamp();lease=self.store.lease
        with self.assertRaises(DeadlineConflict):self.store.observe_expiry(replace(lease),token=self.handle.token,handle=self.handle)
        self.expired=True;self.observer.tick()
        with self.assertRaises(Exception):self.store.observe_expiry(lease,token=self.handle.token,handle=self.handle,freeze=replace(self.observer._freeze))
    def test_revoked_read_no_freeze(self):
        self.stamp();self.expired=True;self.allowed=False
        with self.assertRaises(Exception):self.observer.tick()
        self.assertEqual(self.host.manager.deadline_owners()[0].state,'attached')

class BlockedPumpTests(unittest.TestCase):
    def test_independent_tick_freezes_blocked_actual_planner_then_cleanup_drains(self):
        import test_inflight_cleanup as actual
        from artex.db.task_deadline import TaskDeadlineStore
        from datetime import datetime,timezone,timedelta
        from threading import Event
        from concurrent.futures import ThreadPoolExecutor
        f=actual.Tests();f.setUp();self.addCleanup(f.doCleanups)
        h=f.h;manager=f.f.manager
        # Protocol-only synthetic prepared timeout, never a public admission.
        h._configuration=(*h._configuration[:-1],10);h._task['timeout_seconds']=10
        manager._tasks['5']['timeout_seconds']=10
        start=datetime(2026,1,1,tzinfo=timezone.utc);end=start+timedelta(seconds=10)
        old=f.f.db.fetch_all
        def fetch(q):
            if 'deadline_at<=clock_timestamp()' in q.sql:return [(10,start,end,True)]
            if q.sql.startswith('SELECT timeout_seconds,first_run_at'):return [(10,start,end)]
            if q.sql.startswith('SELECT timeout_seconds'):return [(10,)]
            if q.sql.startswith('SELECT 1 FROM public.artex_task_bootstrap'):return [(1,)]
            if q.sql.startswith('UPDATE tasks SET first_run_at'):return [(start,end)]
            return old(q)
        f.f.db.fetch_all=fetch
        store=TaskDeadlineStore(f.f.db,manager=manager,handle=h,receipt=h._receipt,
            lifecycle_guard=h.coordinator.guard,authorize=lambda *a:True)
        kernel=OwnedGenerationClock(store,10,monotonic_clock=lambda:100.)
        kernel.read_or_stamp()
        observer=OwnedExpiryObserver(f.f.host,kernel,authorize_observation=lambda r:True,authorize_freeze=lambda r:True)
        entered=Event();release=Event()
        def block():
            entered.set()
            if not release.wait(2):raise RuntimeError('Fixture release timeout')
        f.f.f.pp.hook=block
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(f.f.advance,h,1)
            try:
                self.assertTrue(entered.wait(1))
                self.assertEqual(observer.tick().phase,'draining')
                self.assertFalse(future.done())
                self.assertTrue(manager._has_cleanup('5'))
            finally:release.set()
            self.assertEqual(future.result(timeout=2).action,'planner')
        self.assertFalse(h.unresolved);self.assertFalse(manager._has_cleanup('5'))
        self.assertEqual(observer.tick().phase,'ready')
        self.assertEqual(len(f.f.f.pp.calls),1)

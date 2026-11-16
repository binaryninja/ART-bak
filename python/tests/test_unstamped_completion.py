"""Actual installed manager/handle; synthetic positive clock only, no gate change."""
import unittest
from contextlib import contextmanager
from dataclasses import asdict,replace
from datetime import datetime,timezone
from artex.db.unstamped_completion import *
from artex.db.early_completion import EarlyCompletionGrant
from artex.manager import TaskConflict
import test_dispatch_deadline as fixture

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.DispatchDeadlineTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.d=self.f.f.store;self.manager=self.d.manager
        self.nodes=[];self.status='running';self.saved=None;self.clocks=(None,None);self.allowed=True;self.fail_insert=False;self.queries=[]
        self.completed=datetime(2026,1,1,tzinfo=timezone.utc);old=self.d.executor.fetch_all
        def fetch(q):
            self.queries.append(q)
            if q.sql.startswith('SELECT first_run_at,deadline_at,timeout_seconds FROM tasks'):
                return [(*self.clocks,10)] if self.status=='running' else []
            if q.sql.startswith('SELECT status,completed_at,first_run_at,deadline_at,timeout_seconds FROM tasks'):
                return [(self.status,self.completed,*self.clocks,10)]
            if q.sql.startswith('SELECT id,kind,CASE WHEN octet_length(state)'):return self.nodes
            if q.sql.startswith('SELECT task_id FROM public.artex_unstamped_completion'):return [] if self.saved is None else [(5,)]
            if q.sql.startswith('SELECT '+FIELDS+' FROM public.artex_unstamped_completion'):return [] if self.saved is None else [self.saved]
            if q.sql.startswith("UPDATE tasks SET status='done'"):
                if self.clocks!=(None,None) or self.status!='running':return []
                self.status='done';return [(self.completed,)]
            if q.sql.startswith('INSERT INTO public.artex_unstamped_completion'):
                if self.fail_insert:raise RuntimeError('secret')
                self.saved=q.params;return [q.params]
            return old(q)
        @contextmanager
        def tx():
            before=self.status,self.saved
            try:yield self.d.executor
            except BaseException:self.status,self.saved=before;raise
        self.d.executor.fetch_all=fetch;self.d.executor.transaction=tx
        self.permit=EarlyCompletionGrant(self.d.receipt)
        self.store=UnstampedCompletionStore(self.d,grant=self.permit,authorize=lambda *a:self.permit if self.allowed else None)
    def test_empty_done_preserves_null_clock_and_no_lease(self):
        result=self.store.settle()
        self.assertEqual(result.clock_state,'unstamped');self.assertEqual(result.reason,'frontier_drained')
        self.assertEqual(self.clocks,(None,None));self.assertIsNone(self.d.lease)
        self.assertEqual(self.manager.deadline_owners(),());self.assertEqual(self.status,'done')
        self.assertFalse(any('SET first_run_at' in q.sql for q in self.queries))
    def test_abandoned_met_paused_no_proof_or_resume(self):
        self.nodes=[(1,'goal','abandoned'),(2,'goal','met'),(3,'intent','paused')];original=list(self.nodes)
        self.store.settle();self.assertEqual(self.nodes,original)
        self.assertFalse(any('UPDATE exploration' in q.sql for q in self.queries))
    def test_unstamped_reservation_excludes_first_stamp_and_execution(self):
        cap=self.manager.reserve_unstamped_completion(self.store)
        self.assertFalse(self.manager.owns_generation('5',self.d.handle.token,self.d.handle))
        with self.assertRaises(Exception):self.d.stamp_owned(10)
        self.assertIsNone(self.d.lease)
        self.assertTrue(self.manager.validate_unstamped_completion(cap,self.store))
        with self.assertRaises(TaskConflict):self.manager.start('5')
        with self.assertRaises(TaskConflict):self.manager.pause('5')
        self.assertFalse(self.manager.complete('5',self.d.handle.token))
        self.assertFalse(self.manager.acknowledge_deadline_retirement('5',self.d.handle.token,self.d.handle))
    def test_first_stamp_reservation_excludes_unstamped_completion(self):
        self.manager.reserve_deadline_owner('5',self.d.handle.token,self.d.handle,store=self.d,timeout_seconds=10)
        with self.assertRaises(UnstampedCompletionConflict):self.store.settle()
        self.assertEqual(self.status,'running');self.assertIsNone(self.saved)
    def test_partial_or_existing_clock_never_cleared(self):
        for clocks in ((self.completed,None),(None,self.completed),(self.completed,self.completed)):
            f=Tests();f.setUp()
            try:
                f.clocks=clocks
                with self.assertRaises(UnstampedCompletionConflict):f.store.settle()
                self.assertEqual(f.clocks,clocks);self.assertIsNone(f.saved)
            finally:f.doCleanups()
    def test_open_frontier_refuses_and_retains(self):
        self.nodes=[(1,'intent','running')]
        with self.assertRaises(UnstampedCompletionConflict):self.store.settle()
        self.assertEqual(self.manager.deadline_owners()[0].state,'quarantined')
        self.assertEqual(self.f.f.f.f.host._deadline_inventory()[0].state,'quarantined')
    def test_insert_failure_is_uncertain_and_rollback_preserves_null(self):
        self.fail_insert=True
        with self.assertRaises(UnstampedCompletionUncertain):self.store.settle()
        self.assertEqual(self.status,'running');self.assertEqual(self.clocks,(None,None));self.assertIsNone(self.saved)
        self.assertEqual(self.manager.deadline_owners()[0].state,'quarantined')
    def test_final_read_revoke_keeps_committed_done_unacknowledged(self):
        old=self.d.executor.fetch_all;reads=[]
        def fetch(q):
            value=old(q)
            if q.sql.startswith('SELECT '+FIELDS+' FROM public.artex_unstamped_completion'):
                reads.append(q)
                if len(reads)==4:self.permit.revoke()
            return value
        self.d.executor.fetch_all=fetch
        with self.assertRaises(UnstampedCompletionUncertain):self.store.settle()
        self.assertEqual(self.status,'done');self.assertEqual(self.manager.lifecycle_snapshot('5')['status'],'running')
        self.assertEqual(self.manager.deadline_owners()[0].state,'quarantined')
    def test_final_authority_mutates_task_before_verify(self):
        calls=[]
        def authorize(r,op):
            if op=='write':
                calls.append(op)
                if len(calls)==5:self.status='failed'
            return self.permit
        self.store=UnstampedCompletionStore(self.d,grant=self.permit,authorize=authorize)
        with self.assertRaises(UnstampedCompletionUncertain):self.store.settle()
        self.assertEqual(self.status,'failed');self.assertEqual(self.manager.deadline_owners()[0].state,'quarantined')
    def test_install_is_never_implicit(self):
        with self.assertRaises(SchemaRefused):install_unstamped_completion(self.d.executor,expected_database='fixture',expected_schema='public')
    def test_concurrent_first_stamp_and_completion_have_single_reservation_winner(self):
        from threading import Barrier
        from concurrent.futures import ThreadPoolExecutor
        start=Barrier(2)
        def reserve_completion():
            start.wait()
            try:return ('completion',self.manager.reserve_unstamped_completion(self.store))
            except Exception:return ('refused',None)
        def reserve_stamp():
            start.wait()
            try:return ('stamp',self.manager.reserve_deadline_owner('5',self.d.handle.token,self.d.handle,store=self.d,timeout_seconds=10))
            except Exception:return ('refused',None)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(reserve_completion);b=pool.submit(reserve_stamp)
            results=[a.result(timeout=2),b.result(timeout=2)]
        self.assertEqual(sum(kind!='refused' for kind,cap in results),1)
        self.assertEqual(len(self.manager.deadline_owners()),1)
        self.assertIsNone(self.d.lease);self.assertFalse(self.queries)

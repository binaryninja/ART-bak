"""Actual installed manager/handle; synthetic positive clock only, no gate change."""
import unittest
from contextlib import contextmanager
from dataclasses import asdict,replace
from datetime import datetime,timezone
from artex.db.early_completion import *
import test_dispatch_deadline as fixture

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.DispatchDeadlineTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.d=self.f.f.store;self.f.call();self.manager=self.d.manager
        self.nodes=[];self.status='running';self.saved=None;self.expired=False;self.allowed=True;self.fail_insert=False;self.queries=[]
        self.completed=datetime(2026,1,1,tzinfo=timezone.utc);old=self.d.executor.fetch_all
        def fetch(q):
            self.queries.append(q);c=self.d.lease.clock
            if q.sql.startswith('SELECT first_run_at,deadline_at,timeout_seconds FROM tasks'):
                return [(c.first_run_at,c.deadline_at,c.timeout_seconds)] if self.status=='running' and not self.expired else []
            if q.sql.startswith('SELECT status,completed_at,first_run_at,deadline_at,timeout_seconds FROM tasks'):
                return [(self.status,self.completed,c.first_run_at,c.deadline_at,c.timeout_seconds)]
            if q.sql.startswith('SELECT id,kind,CASE WHEN octet_length(state)'):return self.nodes
            if q.sql.startswith('SELECT task_id FROM public.artex_early_completion'):return [] if self.saved is None else [(5,)]
            if q.sql.startswith('SELECT '+FIELDS+' FROM public.artex_early_completion'):return [] if self.saved is None else [self.saved]
            if q.sql.startswith("UPDATE tasks SET status='done'"):
                if self.expired or self.status!='running':return []
                self.status='done';return [(self.completed,)]
            if q.sql.startswith('INSERT INTO public.artex_early_completion'):
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
        self.store=EarlyCompletionStore(self.d,grant=self.permit,authorize=lambda *a:self.permit if self.allowed else None)
    def test_empty_frontier_done_not_achievement(self):
        result=self.store.settle();self.assertEqual(result.reason,'frontier_drained');self.assertEqual(result.status,'done')
        self.assertEqual(self.saved,tuple(asdict(result).values()));self.assertEqual(self.manager.deadline_owners(),())
        self.assertEqual(self.manager.lifecycle_snapshot('5')['status'],'done')
        with self.assertRaises(EarlyCompletionConflict):self.store.settle()
    def test_abandoned_and_paused_match_source(self):
        self.nodes=[(1,'goal','abandoned'),(2,'intent','paused')]
        self.assertEqual(self.store.settle().status,'done')
    def test_met_needs_no_confirmed_proof(self):
        self.nodes=[(1,'goal','met')];self.assertEqual(self.store.settle().reason,'frontier_drained')
    def test_open_goal_and_open_running_intent_block(self):
        for kind,state in [('goal','open'),('intent','open'),('intent','running')]:
            f=Tests();f.setUp()
            try:
                f.nodes=[(1,kind,state)]
                with self.assertRaises(EarlyCompletionConflict):f.store.settle()
                self.assertIsNone(f.saved);self.assertEqual(f.status,'running')
            finally:f.doCleanups()
    def test_expired_cannot_fake_early_completion(self):
        self.expired=True
        with self.assertRaises(EarlyCompletionConflict):self.store.settle()
        self.assertEqual(self.status,'running');self.assertEqual(self.manager.deadline_owners()[0].state,'quarantined')
    def test_failed_receipt_insert_rolls_back_but_retains(self):
        self.fail_insert=True
        with self.assertRaises(EarlyCompletionUncertain):self.store.settle()
        self.assertEqual(self.status,'running');self.assertIsNone(self.saved)
        self.assertEqual(self.manager.deadline_owners()[0].state,'quarantined')
    def test_write_grant_frontier_change_no_task_write(self):
        def grant(r,op):
            if op=='write':self.nodes=[(1,'goal','open')]
            return self.permit
        self.store=EarlyCompletionStore(self.d,grant=self.permit,authorize=grant)
        with self.assertRaises(EarlyCompletionConflict):self.store.settle()
        self.assertEqual(self.status,'running')
    def test_last_grant_terminal_drift_retains_uncertainty(self):
        def grant(r,op):
            if self.saved is not None:self.status='failed'
            return self.permit
        self.store=EarlyCompletionStore(self.d,grant=self.permit,authorize=grant)
        with self.assertRaises(EarlyCompletionUncertain):self.store.settle()
        self.assertEqual(self.manager.deadline_owners()[0].state,'quarantined')
    def test_denial_and_copied_lease_no_sql(self):
        self.allowed=False
        with self.assertRaises(EarlyCompletionConflict):self.store.settle()
        self.assertFalse(self.queries)
        other=EarlyCompletionStore(self.d,grant=self.permit,authorize=lambda *a:self.permit);other.lease=replace(self.d.lease)
        with self.assertRaises(EarlyCompletionConflict):other.settle()
    def test_busy_handle_no_write_and_no_retirement(self):
        self.d.handle.wait=lambda timeout:False
        with self.assertRaises(EarlyCompletionConflict):self.store.settle()
        self.assertFalse(self.queries);self.assertEqual(self.manager.deadline_owners()[0].state,'quarantined')
    def test_no_implicit_schema_install(self):
        with self.assertRaises(SchemaRefused):install_early_completion(self.d.executor,expected_database='fixture',expected_schema='public')

    def test_final_write_callback_cannot_change_committed_task(self):
        calls=[]
        def grant(r,op):
            if op=='write':
                calls.append(op)
                if len(calls)==5:self.status='failed'
            return self.permit
        self.store=EarlyCompletionStore(self.d,grant=self.permit,authorize=grant)
        with self.assertRaises(EarlyCompletionUncertain):self.store.settle()
        self.assertEqual(self.status,'failed');self.assertIsNotNone(self.saved)
        self.assertEqual(self.manager.deadline_owners()[0].state,'quarantined')
        self.assertEqual(self.manager.lifecycle_snapshot('5')['status'],'running')
        with self.assertRaises(EarlyCompletionConflict):self.store.settle()

    def test_boolean_or_wrong_permit_policy_rejected_before_reservation(self):
        for value in (True,EarlyCompletionGrant(self.d.receipt)):
            store=EarlyCompletionStore(self.d,grant=self.permit,authorize=lambda *a:value)
            with self.assertRaises(EarlyCompletionConflict):store.settle()
            self.assertEqual(self.manager.deadline_owners()[0].state,'attached')
        self.assertFalse(self.queries)
    def test_copied_wrong_scope_revoked_and_no_reenable(self):
        import copy
        for permit in (copy.copy(self.permit),EarlyCompletionGrant(replace(self.d.receipt,run_id=999))):
            with self.assertRaises(EarlyCompletionConflict):EarlyCompletionStore(self.d,grant=permit,authorize=lambda *a:permit)
        self.permit.revoke();self.permit.revoke()
        with self.assertRaises(AttributeError):self.permit._revoked=False
        with self.assertRaises(EarlyCompletionConflict):self.store.settle()
        self.assertFalse(self.queries)
    def test_revocation_linearizes_with_acknowledgement(self):
        from threading import Event,Thread
        entered=Event();revoked=Event();observed=[]
        def revoke():
            entered.set();self.permit.revoke();revoked.set()
        with self.permit._acknowledge(self.d.receipt):
            t=Thread(target=revoke);t.start();self.assertTrue(entered.wait(1))
            self.assertFalse(revoked.wait(.05))
            self.permit._check(self.d.receipt,'write')
        t.join(1);self.assertFalse(t.is_alive());self.assertTrue(revoked.is_set())
        with self.assertRaises(EarlyCompletionConflict):
            with self.permit._acknowledge(self.d.receipt):observed.append(True)
        self.assertEqual(observed,[])

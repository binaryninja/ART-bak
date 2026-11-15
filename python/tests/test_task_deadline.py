import unittest
from contextlib import contextmanager
from datetime import datetime,timezone
from artex.db.task_deadline import TaskDeadlineStore,DeadlineConflict,DeadlineUncertain
from artex.agent.bootstrap import Receipt
import test_engine_runner_bootstrap as fixture

class DeadlineTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.EngineBootstrapTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.f.host.start('5');self.handle=self.f.runner.handles()[0];self.f.f.advance(self.handle)
        self.db=self.f.f.db;self.sql=[];self.clock=datetime(2026,1,1,tzinfo=timezone.utc);self.timeout=0
        self.receipt=Receipt(*list(self.db.receipt)[:-1],tuple(self.db.receipt[-1])) if not isinstance(self.db.receipt,dict) else None
        # The fixture exposes a source-shaped journal row.
        if self.receipt is None:raise AssertionError('Unexpected fixture')
        original=self.db.fetch_all
        def query(q):
            self.sql.append(q)
            if q.sql.startswith('SELECT timeout_seconds'):return [(self.timeout,)]
            if q.sql.startswith('SELECT 1 FROM public.artex_task_bootstrap'):return [(1,)]
            if q.sql.startswith('UPDATE tasks SET first_run_at'):return [(self.clock,None)]
            return original(q)
        self.db.fetch_all=query
        @contextmanager
        def guard(r):yield
        self.store=TaskDeadlineStore(self.db,manager=self.f.f.manager,handle=self.handle,receipt=self.receipt,lifecycle_guard=self.handle.coordinator.guard,authorize=lambda *a:True)
    def test_lock_order_full_generation_and_source_stamp_sql(self):
        result=self.store.stamp(0);self.assertEqual(result.first_run_at,self.clock)
        self.assertIn('FOR UPDATE',self.sql[0].sql);self.assertIn('FOR SHARE',self.sql[1].sql)
        self.assertEqual(self.sql[1].params,(5,9,71,self.receipt.request_id,self.receipt.config_digest,self.receipt.owner_token,self.receipt.revision))
        self.assertIn('WHEN first_run_at IS NOT NULL THEN deadline_at',self.sql[2].sql)
        self.assertEqual(self.sql[2].params,(5,9,0))
    def test_wrong_configuration_before_write(self):
        with self.assertRaises(DeadlineConflict):self.store.stamp(1)
        self.assertEqual(len(self.sql),0)
    def test_wrong_generation_before_sql(self):
        from dataclasses import replace
        self.store.receipt=replace(self.receipt,run_id=72)
        with self.assertRaises(DeadlineConflict):self.store.stamp(0)
        self.assertEqual(self.sql,[])
    def test_stale_journal_before_write(self):
        old=self.db.fetch_all
        self.db.fetch_all=lambda q:[] if q.sql.startswith('SELECT 1 FROM public.artex_task_bootstrap') else old(q)
        with self.assertRaises(DeadlineConflict):self.store.stamp(0)
        self.assertFalse(any(q.sql.startswith('UPDATE') for q in self.sql))
    def test_authorization_callback_revokes_before_sql(self):
        def deny(*a):self.handle.token.cancel();return True
        self.store.authorize=deny
        with self.assertRaises(DeadlineConflict):self.store.stamp(0)
        self.assertEqual(self.sql,[])
    def test_postcommit_authority_loss_reports_reconciliation(self):
        original=self.db.transaction
        @contextmanager
        def transaction():
            with original() as tx:yield tx
            self.handle.token.cancel()
        self.db.transaction=transaction
        with self.assertRaises(DeadlineUncertain):self.store.stamp(0)
        self.assertTrue(any(q.sql.startswith('UPDATE') for q in self.sql))
    def test_independent_guard_rejected_at_construction(self):
        from contextlib import nullcontext
        with self.assertRaises(ValueError):
            TaskDeadlineStore(self.db,manager=self.f.f.manager,handle=self.handle,receipt=self.receipt,
                lifecycle_guard=lambda *a:nullcontext(),authorize=lambda *a:True)
    def test_guard_rebind_rejected_before_sql(self):
        self.handle.coordinator.guard=lambda *a:None
        with self.assertRaises(DeadlineConflict):self.store.stamp(0)
        self.assertEqual(self.sql,[])

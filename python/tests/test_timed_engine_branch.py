"""Actual owned post-prepare registration. Positive pins remain synthetic only."""
import unittest
from dataclasses import replace
from artex.agent.owned_finalization import FinalizationPlan
from artex.db.final_assessment import FinalConfiguration
from artex.agent.final_assessment import CATALOG_DIGEST
from artex.agent.runtime import Limits,Grant
from artex.agent.chat import ChatBinding
from artex.agent.engine import EngineUnavailable
from artex.sidequestion import Model
import test_timed_lifecycle as base

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=base.ActualRegistrationTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        model=Model('final-inert',identity='final-inert')
        config=FinalConfiguration(model,'Inspect only','final-policy',CATALOG_DIGEST,Limits())
        plan=FinalizationPlan(config,self.f.f.runtime.transcript,lambda *a:True,lambda *a:True,
            lambda r,m:ChatBinding(m,object()),lambda *a:Grant(True,True,True),lambda *a:True,
            lambda *a:True,lambda *a:True,self.f.policy.timing_policy.clock)
        self.f.policy=replace(self.f.policy,final_plan=plan)
        self.bundle=self.f.prepare();self.handle=self.bundle.runner.handles()[0]
        self.engine=self.handle._child;self.actions=self.bundle.timed_lifecycle.actions(self.handle)
    def test_full_original_registration_binds_once_without_stamp(self):
        self.assertIs(self.engine._timed_execution[1],self.actions)
        self.assertEqual(self.actions.timing.execution_status(),'unstamped')
        self.assertIsNone(self.actions.timing.store.lease)
        self.assertEqual(self.f.f.p.calls,[1,1])
        with self.assertRaises(EngineUnavailable):self.engine.bind_timed_execution(self.bundle.timed_lifecycle,self.actions)
    def test_copied_registration_denied(self):
        with self.assertRaises(Exception):self.bundle.timed_lifecycle.validate_execution_registration(self.handle,replace(self.actions))
    def test_preclaim_busy_no_sql_or_event_consumption(self):
        self.engine.notify('hint1','hint')
        before=len(self.f.f.f.db.calls);calls=len(self.f.f.p.calls)
        kernel=self.actions.timing.kernel
        kernel._lock.acquire()
        try:
            value=self.bundle.host.advance_owned(lambda:self.bundle.runner.advance('5',self.handle.token,1.))
        finally:kernel._lock.release()
        self.assertEqual(value.value.action,'busy')
        self.assertEqual(len(self.f.f.f.db.calls),before)
        self.assertEqual(len(self.f.f.p.calls),calls)
        self.assertEqual(len(self.engine._events),1)
        self.assertIsNone(self.actions.timing.store.lease)
    def test_sealed_registry_refuses_execution(self):
        self.bundle.manual_actions.stop_new_actions()
        with self.assertRaises(Exception):self.bundle.host.advance_owned(lambda:self.bundle.runner.advance('5',self.handle.token,1.))
        self.assertEqual(self.f.f.p.calls,[1,1]);self.assertIsNone(self.actions.timing.store.lease)
    def test_timed_cannot_use_ordinary_completion_callback(self):
        called=[]
        self.assertFalse(self.engine.settle_completed(lambda *a:called.append(a) or True))
        self.assertEqual(called,[])

    def stamp_original_clock(self):
        from datetime import datetime,timezone,timedelta
        db=self.f.f.f.db;original=db.fetch_all
        first=datetime(2026,1,1,tzinfo=timezone.utc);deadline=first+timedelta(seconds=10)
        def query(q):
            if q.sql.startswith('SELECT timeout_seconds FROM tasks'):return [(10,)]
            if q.sql.startswith('UPDATE tasks SET first_run_at'):return [(first,deadline)]
            if q.sql.startswith('SELECT timeout_seconds,first_run_at,deadline_at FROM tasks'):return [(10,first,deadline)]
            return original(q)
        db.fetch_all=query
        clock=self.actions.timing.kernel.read_or_stamp()
        self.assertIs(self.actions.timing.store.lease.clock,clock)
        return clock
    def test_expired_original_owned_clock_waits_without_claim_or_model(self):
        self.stamp_original_clock()
        kernel=self.actions.timing.kernel;kernel.narrow_deadline(99.,100.)
        self.engine.notify('hint2','hint');before=len(self.f.f.f.db.calls)
        value=self.bundle.host.advance_owned(lambda:self.bundle.runner.advance('5',self.handle.token,1.))
        self.assertEqual(value.value.action,'expiry_wait')
        self.assertEqual(len(self.f.f.f.db.calls),before)
        self.assertEqual(self.f.f.p.calls,[1,1]);self.assertEqual(len(self.engine._events),1)
        self.assertFalse(self.engine.unresolved)
    def test_exact_timed_sql_snapshot_and_partial_clock_refusal(self):
        db=self.f.f.f.db;old=db.fetch_all;queries=[];partial=[False]
        def query(q):
            if q.sql.startswith('SELECT t.id,t.first_run_at,t.deadline_at'):
                queries.append(q);return [(5,None,'not-null' if partial[0] else None)]
            return old(q)
        db.fetch_all=query
        db.rows=[n for n in db.rows if n[1]=='fact']
        value=self.bundle.host.advance_owned(lambda:self.bundle.runner.advance('5',self.handle.token,1.))
        self.assertEqual(value.value.action,'idle')
        self.assertFalse(value.value.completion_ready)
        r=self.actions.receipt
        self.assertEqual(queries[-1].params,(5,9,10,r.run_id,r.request_id,r.config_digest,r.owner_token,r.revision))
        partial[0]=True
        with self.assertRaises(Exception):self.bundle.host.advance_owned(lambda:self.bundle.runner.advance('5',self.handle.token,2.))
        self.assertIsNone(self.actions.timing.store.lease)

    def test_admitted_expiry_returns_wait_only_after_canonical_cleanup(self):
        from artex.agent.deadline import TaskBudgetExpired
        self.stamp_original_clock()
        self.actions.timing.kernel.narrow_deadline(110.,100.)
        db=self.f.f.f.db;old=db.fetch_all;clock=self.actions.timing.store.lease.clock
        def query(q):
            if q.sql.startswith('SELECT t.id,t.first_run_at,t.deadline_at'):
                return [(5,clock.first_run_at,clock.deadline_at)]
            if q.sql.startswith('SELECT task_id FROM public.artex_task_bootstrap'):
                r=self.actions.receipt
                return [(5,)] if q.params==(r.task_id,r.exploration_id,r.run_id,r.request_id,r.config_digest,r.owner_token,r.revision) else []
            return old(q)
        db.fetch_all=query
        calls=[]
        def transport(config):
            def invoke(*args):calls.append(1);raise TaskBudgetExpired()
            return invoke
        self.f.f.p.factory._transport_factory=transport;self.f.f.p.factory.invalidate()
        value=self.bundle.host.advance_owned(lambda:self.bundle.runner.advance('5',self.handle.token,1.))
        self.assertEqual(calls,[1]);self.assertEqual(value.value.action,'expiry_wait')
        self.assertFalse(self.engine.unresolved)
        self.assertEqual(self.engine.cleanup._records,{})

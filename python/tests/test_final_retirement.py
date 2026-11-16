"""Actual registered finalization→private retirement; synthetic positive config only.

No issued admission is mocked; the existing prerequisite fixture pins positive
configuration before preparing its actual owned engine and lifecycle.
"""
import unittest,time
from contextlib import contextmanager
from dataclasses import asdict,replace
from datetime import datetime,timezone
from artex.agent.owned_finalization import FinalizationPlan,FinalizationUnavailable
from artex.agent.final_assessment import CATALOG_DIGEST
from artex.agent.runtime import Limits,Grant
from artex.agent.chat import ChatBinding
from artex.db.final_assessment import FinalConfiguration,FinalReceipt,COLS
from artex.db.final_settlement import FIELDS
from artex.sidequestion import Model
import test_timed_lifecycle as base

class FinalRetirementTests(unittest.TestCase):
    def setUp(self):
        self.base=base.ActualRegistrationTests();self.base.setUp();self.addCleanup(self.base.doCleanups)
        self.calls=[];self.hook=None;owner=self
        class Provider:
            def complete(self,context,request):
                owner.calls.append(request)
                if owner.hook:owner.hook()
                return {'role':'assistant','content':[{'type':'text','text':'Read-only assessment.'}]},'end_turn',{}
        self.provider=Provider();model=Model('final-inert',identity='final-inert')
        config=FinalConfiguration(model,'Inspect only','final-policy',CATALOG_DIGEST,Limits())
        plan=FinalizationPlan(config,self.base.f.runtime.transcript,lambda *a:True,lambda *a:True,
            lambda r,m:ChatBinding(m,self.provider),lambda *a:Grant(True,True,True),lambda *a:True,
            lambda *a:True,lambda *a:True,self.base.policy.timing_policy.clock)
        self.base.policy=replace(self.base.policy,final_plan=plan)
        self.bundle=self.base.prepare();self.handle=self.bundle.runner.handles()[0]
        self.manager=self.bundle.manager;self.actions=self.bundle.timed_lifecycle.actions(self.handle)
        self.controller=self.actions.finalizer
        self.assertIsNotNone(self.controller._retirement_binding)
        self.db=self.base.f.f.db;self.expired=True
        from datetime import timedelta
        first=datetime(2026,1,1,tzinfo=timezone.utc);deadline=first+timedelta(seconds=10)
        original=self.db.fetch_all
        def stamp(q):
            if q.sql.startswith('SELECT timeout_seconds FROM tasks'):return [(10,)]
            if q.sql.startswith('UPDATE tasks SET first_run_at'):return [(first,deadline)]
            if q.sql.startswith('SELECT timeout_seconds,first_run_at,deadline_at FROM tasks'):return [(10,first,deadline)]
            return original(q)
        self.db.fetch_all=stamp
        self.actions.timing.kernel.read_or_stamp()
        self.row=None;self.saved=None;self.status='running';self.sql=[];self.calls=[];self.hook=None;self.fail_insert=False
        self.nodes=[(1,'goal','met'),(2,'fact','confirmed')];self.edges=[(2,1)]
        oldall=self.db.fetch_all;oldone=self.db.fetch_one;clock=self.actions.timing.store.lease.clock
        def all(q):
            self.sql.append(q)
            if q.sql.startswith('SELECT timeout_seconds,first_run_at,deadline_at,deadline_at<='):
                return [(clock.timeout_seconds,clock.first_run_at,clock.deadline_at,self.expired)]
            if q.sql.startswith('SELECT first_run_at,deadline_at,timeout_seconds,deadline_at<='):
                return [(clock.first_run_at,clock.deadline_at,clock.timeout_seconds,self.expired)] if self.status=='running' else []
            if q.sql.startswith('SELECT task_id FROM public.artex_task_bootstrap'):return [(5,)]
            if q.sql.startswith('SELECT '+COLS):return [tuple(asdict(self.row).values())] if self.row else []
            if q.sql.startswith('UPDATE public.artex_final_assessment'):
                if self.row.revision!=q.params[-1] or self.row.phase!=q.params[-2]:return []
                self.row=replace(self.row,phase=q.params[0],revision=self.row.revision+1,transcript_turn=q.params[1],result_digest=q.params[2]);return [tuple(asdict(self.row).values())]
            if q.sql.startswith('SELECT status,completed_at,first_run_at,deadline_at,timeout_seconds FROM tasks'):
                return [(self.status,datetime(2026,1,1,tzinfo=timezone.utc),clock.first_run_at,clock.deadline_at,clock.timeout_seconds)]
            if q.sql.startswith('SELECT '+FIELDS+' FROM public.artex_final_settlement'):return [] if self.saved is None else [self.saved]
            if 'FROM public.artex_final_assessment WHERE attempt_id=' in q.sql:return [tuple(asdict(self.row).values())]
            if q.sql.startswith('SELECT attempt_id FROM public.artex_final_settlement'):return [] if self.saved is None else [(self.saved[0],)]
            if q.sql.startswith('SELECT source_task_id FROM task_relations'):return []
            if q.sql.startswith('SELECT id,kind,state FROM exploration_nodes'):return self.nodes
            if q.sql.startswith('SELECT src_id,dst_id FROM exploration_edges'):return self.edges
            if q.sql.startswith('UPDATE tasks SET status=$1,completed_at='):
                if self.status!='running':return []
                self.status=q.params[0];return [(datetime(2026,1,1,tzinfo=timezone.utc),)]
            if q.sql.startswith('INSERT INTO public.artex_final_settlement'):
                if self.fail_insert:raise RuntimeError('private failure')
                self.saved=q.params;return [q.params]
            return oldall(q)
        def one(q):
            self.sql.append(q)
            if q.sql.startswith('INSERT INTO public.artex_final_assessment'):
                self.row=FinalReceipt(101,*q.params,'reserved',1,None,None);return tuple(asdict(self.row).values())
            return oldone(q)
        @contextmanager
        def tx():
            old=(self.row,self.status,self.saved)
            try:yield self.db
            except BaseException:self.row,self.status,self.saved=old;raise
        self.db.fetch_all=all;self.db.fetch_one=one;self.db.transaction=tx
    def settle(self):
        result=self.controller.run_once()
        self.assertEqual(result.phase,'settled')
        return result.receipt
    def test_original_retirement_private_receipt_and_pending(self):
        receipt=self.settle()
        self.assertIs(self.controller.retirement_result(self.bundle.runner),receipt)
        self.assertEqual(self.bundle.manual_actions.snapshot().pending_removal,1)
        self.assertFalse(self.controller.confirm_runner_removal())
        self.assertEqual(len(self.calls),1)
    def test_mutated_consumed_cap_denied(self):
        self.settle();object.__setattr__(self.controller.settlement._cap,'task_id','6')
        with self.assertRaises(FinalizationUnavailable):self.controller.retirement_result(self.bundle.runner)
    def test_copied_public_result_denied(self):
        self.settle();self.controller.result=replace(self.controller.result)
        with self.assertRaises(FinalizationUnavailable):self.controller.retirement_result(self.bundle.runner)
    def test_reintroduced_execution_owner_denied(self):
        original=self.manager._runs['5'];self.settle();self.manager._runs['5']=original
        with self.assertRaises(FinalizationUnavailable):self.controller.retirement_result(self.bundle.runner)
    def test_foreign_runner_and_semantic_receipt_denied(self):
        receipt=self.settle()
        with self.assertRaises(FinalizationUnavailable):self.controller.retirement_result(object())
        object.__setattr__(receipt,'status','timeout')
        with self.assertRaises(FinalizationUnavailable):self.controller.retirement_result(self.bundle.runner)
    def test_accessor_no_sql_or_policy_callbacks(self):
        receipt=self.settle();before=len(self.sql)
        self.assertIs(self.controller.retirement_result(self.bundle.runner),receipt)
        self.assertEqual(len(self.sql),before)
    def test_copied_private_receipt_and_reintroduced_evidence_denied(self):
        receipt=self.settle();store=self.controller.settlement
        store._result=replace(receipt)
        with self.assertRaises(FinalizationUnavailable):self.controller.retirement_result(self.bundle.runner)
        store._result=receipt;store._evidence=object()
        with self.assertRaises(FinalizationUnavailable):self.controller.retirement_result(self.bundle.runner)

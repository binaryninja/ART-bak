"""Actual manual observer→restricted final→settlement, inert SQL/provider only."""
import unittest,tempfile,time
from contextlib import contextmanager
from dataclasses import asdict,replace
from datetime import datetime,timezone
from artex.agent.owned_finalization import *
from artex.agent.final_assessment import CATALOG_DIGEST
from artex.agent.chat import ChatBinding
from artex.agent.runtime import Grant,Limits
from artex.db.final_assessment import FinalReceipt,COLS
from artex.db.final_settlement import FIELDS
from artex.chat_transcripts import ChatTranscriptAdapter
from artex.transcripts import TranscriptStore
import test_expiry_observer as fixture

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups);self.f.stamp();self.f.expired=True
        self.db=self.f.store.executor;self.manager=self.f.host.manager;self.handle=self.f.handle
        self.row=None;self.saved=None;self.status='running';self.sql=[];self.calls=[];self.hook=None;self.fail_insert=False
        self.nodes=[(1,'goal','met'),(2,'fact','confirmed')];self.edges=[(2,1)]
        oldall=self.db.fetch_all;oldone=self.db.fetch_one;clock=self.f.store.lease.clock
        def all(q):
            self.sql.append(q)
            if q.sql.startswith('SELECT first_run_at,deadline_at,timeout_seconds,deadline_at<='):
                return [(clock.first_run_at,clock.deadline_at,clock.timeout_seconds,self.f.expired)] if self.status=='running' else []
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
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.transcript=ChatTranscriptAdapter(TranscriptStore(self.tmp.name));owner=self
        class Provider:
            def complete(self,context,request):
                owner.calls.append(request)
                if owner.hook:owner.hook()
                return {'role':'assistant','content':[{'type':'text','text':'Existing evidence only.'}]},'end_turn',{}
        self.provider=Provider()
        self.config=FinalConfiguration(self.handle.model,'Assess existing evidence only','final-v1',CATALOG_DIGEST,Limits())
        self.plan=FinalizationPlan(self.config,self.transcript,lambda r:True,lambda *a:True,
            lambda r,m:ChatBinding(m,self.provider),lambda *a:Grant(True,True,True),lambda r:True,lambda *a:True,lambda *a:True,time.monotonic)
        self.controller=OwnedFinalization(self.f.observer,self.plan)
    def test_actual_final_settlement_and_transferred_observer(self):
        self.assertEqual(self.calls,[]);value=self.controller.run_once()
        self.assertEqual(value.phase,'settled');self.assertEqual(value.receipt.status,'done');self.assertEqual(len(self.calls),1)
        self.assertEqual(self.f.observer.tick().phase,'transferred');self.assertEqual(self.manager.deadline_owners(),())
        with self.assertRaises(FinalizationUnavailable):self.controller.run_once()
    def test_open_goal_timeout_not_model_claim(self):
        self.nodes=[(1,'goal','open')];self.edges=[]
        self.assertEqual(self.controller.run_once().receipt.status,'timeout')
    def test_assessing_tick_does_not_quarantine(self):
        states=[];self.hook=lambda:states.append(self.f.observer.tick().phase)
        self.controller.run_once();self.assertEqual(states,['transferred'])
    def test_unexpired_wait_does_not_allocate(self):
        self.f.expired=False;self.f.f.mono=111
        self.assertEqual(self.controller.run_once().phase,'waiting_database');self.assertIsNone(self.row);self.assertEqual(self.calls,[])
        self.f.expired=True;self.assertEqual(self.controller.run_once().phase,'settled')
    def test_settlement_failure_retains_no_inference_retry(self):
        self.fail_insert=True
        with self.assertRaises(FinalizationUncertain):self.controller.run_once()
        self.assertEqual(self.status,'running');self.assertEqual(len(self.calls),1)
        self.assertEqual(self.manager.deadline_owners()[0].state,'quarantined')
        with self.assertRaises(FinalizationUnavailable):self.controller.run_once()
        self.assertEqual(len(self.calls),1)
    def test_cancel_before_action_no_journal_or_provider(self):
        self.assertTrue(self.controller.cancel())
        with self.assertRaises(FinalizationUnavailable):self.controller.run_once()
        self.assertIsNone(self.row);self.assertEqual(self.calls,[])

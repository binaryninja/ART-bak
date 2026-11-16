"""Synthetic exact-owner SQL/JSONL protocol; not positive timeout admission."""
import unittest
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime,timezone
from artex.db.final_settlement import *
from artex.agent.runtime import RunResult
import test_final_assessment as fixture

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        j=self.f.journal;r=j.start(j.reserve())
        user={'role':'user','content':[{'type':'text','text':'final'}]}
        t=j.transcript.begin_scoped(r.session_id,'final',user,r.transcript_identity())
        j.transcript.commit(r.session_id,t,RunResult('completed',[user,{'role':'assistant','content':[{'type':'text','text':'not authority'}]}],{},1,0))
        self.receipt=j.record_finalized(r,t.id);self.nodes=[(1,'goal','met'),(2,'fact','confirmed')];self.edges=[(2,1)];self.sources=[]
        self.status='running';self.saved=None;self.allowed=True;self.fail_insert=False;self.sql=[]
        old=self.f.db.fetch_all
        def fetch(q):
            self.sql.append(q)
            if q.sql.startswith('SELECT first_run_at,deadline_at,timeout_seconds,deadline_at<=') and self.status!='running':return []
            if q.sql.startswith('SELECT status,completed_at,first_run_at,deadline_at,timeout_seconds FROM tasks'):
                r=self.receipt;return [(self.status,datetime(2026,1,1,tzinfo=timezone.utc),r.first_run_at,r.deadline_at,r.timeout_seconds)]
            if q.sql.startswith('SELECT '+FIELDS+' FROM public.artex_final_settlement'):
                return [] if self.saved is None else [self.saved]
            if 'FROM public.artex_final_assessment WHERE attempt_id=' in q.sql:return [tuple(asdict(self.f.row).values())]
            if q.sql.startswith('SELECT attempt_id FROM public.artex_final_settlement'):return [] if self.saved is None else [(self.saved[0],)]
            if q.sql.startswith('SELECT source_task_id FROM task_relations'):return self.sources
            if q.sql.startswith('SELECT id,kind,state FROM exploration_nodes'):return self.nodes
            if q.sql.startswith('SELECT src_id,dst_id FROM exploration_edges'):return self.edges
            if q.sql.startswith('UPDATE tasks SET status=$1,completed_at='):
                if self.status!='running':return []
                self.status=q.params[0];return [(datetime(2026,1,1,tzinfo=timezone.utc),)]
            if q.sql.startswith('INSERT INTO public.artex_final_settlement'):
                if self.fail_insert:raise RuntimeError('private')
                self.saved=q.params;return [q.params]
            return old(q)
        @contextmanager
        def tx():
            oldstate=(self.status,self.saved)
            try:yield self.f.db
            except BaseException:self.status,self.saved=oldstate;raise
        self.f.db.fetch_all=fetch;self.f.db.transaction=tx
        self.store=FinalSettlementStore(j,self.receipt,authorize=lambda *a:self.allowed)
    def test_proven_local_goals_done_atomic_receipt_and_retire(self):
        result=self.store.settle();self.assertEqual(result.status,'done');self.assertEqual(self.status,'done')
        self.assertEqual(tuple(asdict(result).values()),self.saved)
        self.assertEqual(self.f.manager.deadline_owners(),())
        self.assertTrue(self.f.handle.token.cancelled)
        self.assertEqual(self.f.manager.lifecycle_snapshot('5')['status'],'done')
        self.assertTrue(self.f.manager.acknowledge_deadline_retirement('5',self.f.handle.token,self.f.handle))
        with self.assertRaises(SettlementConflict):self.store.settle()
    def test_open_goal_timeout_not_stopped(self):
        self.nodes=[(1,'goal','open')];self.edges=[]
        self.assertEqual(self.store.settle().status,'timeout')
        self.assertEqual(self.f.manager.lifecycle_snapshot('5')['status'],'timeout')
    def test_unknown_goalless_inherited_or_missing_proof_no_write(self):
        for mode in ('empty','inherited','proof','unknown'):
            with self.subTest(mode=mode):
                # Independent actual-owner fixture for each quarantined attempt.
                f=Tests();f.setUp()
                try:
                    if mode=='empty':f.nodes=[]
                    if mode=='inherited':f.sources=[(6,)]
                    if mode=='proof':f.edges=[]
                    if mode=='unknown':f.nodes=[(1,'goal','deleted')]
                    with self.assertRaises(SettlementConflict):f.store.settle()
                    self.assertEqual(f.status,'running');self.assertIsNone(f.saved)
                    self.assertEqual(f.f.manager.deadline_owners()[0].state,'quarantined')
                finally:f.doCleanups()
    def test_insert_failure_rolls_back_both_and_quarantines(self):
        self.fail_insert=True
        with self.assertRaises(SettlementUncertain):self.store.settle()
        self.assertEqual(self.status,'running');self.assertIsNone(self.saved)
        self.assertEqual(self.f.manager.deadline_owners()[0].state,'quarantined')
    def test_existing_terminal_preserved(self):
        self.status='failed'
        with self.assertRaises(SettlementConflict):self.store.settle()
        self.assertEqual(self.status,'failed');self.assertIsNone(self.saved)
    def test_denied_grant_no_task_write(self):
        self.allowed=False
        with self.assertRaises(SettlementConflict):self.store.settle()
        self.assertEqual(self.status,'running');self.assertIsNone(self.saved)
    def test_install_requires_explicit_fresh_database(self):
        with self.assertRaises(SchemaRefused):install_final_settlement(self.f.db,expected_database='fixture',expected_schema='public')
        self.assertFalse(any('CREATE TABLE' in q.sql for q in self.sql))
    def test_write_grant_cannot_remove_proof_then_commit_done(self):
        def authorize(receipt,operation,status):
            if operation=='write':self.edges=[]
            return True
        self.store=FinalSettlementStore(self.f.journal,self.receipt,authorize=authorize)
        with self.assertRaises(SettlementConflict):self.store.settle()
        self.assertEqual(self.status,'running');self.assertIsNone(self.saved)
    def test_postwrite_graph_drift_quarantines_instead_of_retirement(self):
        calls=[0]
        def authorize(receipt,operation,status):
            if operation=='write':
                calls[0]+=1
                if calls[0]==3:self.nodes=[(1,'goal','open')];self.edges=[]
            return True
        self.store=FinalSettlementStore(self.f.journal,self.receipt,authorize=authorize)
        with self.assertRaises(SettlementUncertain):self.store.settle()
        self.assertEqual(self.status,'done') # SQL committed; never report rollback.
        self.assertEqual(self.f.manager.deadline_owners()[0].state,'quarantined')
        self.assertEqual(self.f.manager.lifecycle_snapshot('5')['status'],'running')

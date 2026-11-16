"""Journal protocol on actual retained registry, inert SQL/JSONL only."""
import unittest,tempfile
from contextlib import contextmanager
from dataclasses import asdict,replace
from copy import deepcopy
from artex.db.final_assessment import *
from artex.agent.runtime import RunResult
from artex.manager import TaskConflict
from artex.chat_transcripts import ChatTranscriptAdapter
from artex.transcripts import TranscriptStore
import test_deadline_freeze as freeze_fixture

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=freeze_fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.freeze,_=self.f.freeze();self.manager=self.f.manager;self.handle=self.f.handle;self.lease=self.f.lease
        self.config=FinalConfiguration(self.handle.model,'Restricted final assessment','policy-final','a'*64,Limits())
        self.cap=self.manager.reserve_deadline_assessment(self.freeze,self.handle.token,self.handle,self.lease,self.config.digest)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.transcript=ChatTranscriptAdapter(TranscriptStore(self.tmp.name));self.db=self.f.f.store.executor
        self.row=None;self.calls=[];self.sql_fail=False;self.allowed=True
        original_all=self.db.fetch_all;original_one=self.db.fetch_one
        def all(q):
            self.calls.append(q)
            if q.sql.startswith('SELECT first_run_at,deadline_at,timeout_seconds,deadline_at<='):
                c=self.lease.clock;return [(c.first_run_at,c.deadline_at,c.timeout_seconds,True)]
            if q.sql.startswith('SELECT task_id FROM public.artex_task_bootstrap'):return [(5,)]
            if q.sql.startswith('SELECT '+COLS):return [tuple(asdict(self.row).values())] if self.row else []
            if q.sql.startswith('UPDATE public.artex_final_assessment'):
                if self.sql_fail:raise RuntimeError('private SQL error')
                if self.row.revision!=q.params[-1] or self.row.phase!=q.params[-2]:return []
                self.row=replace(self.row,phase=q.params[0],revision=self.row.revision+1,transcript_turn=q.params[1],result_digest=q.params[2])
                return [tuple(asdict(self.row).values())]
            return original_all(q)
        def one(q):
            self.calls.append(q)
            if q.sql.startswith('INSERT INTO public.artex_final_assessment'):
                self.row=FinalReceipt(101,*q.params,'reserved',1,None,None)
                return tuple(asdict(self.row).values())
            return original_one(q)
        @contextmanager
        def tx():
            old=self.row
            try:yield self.db
            except BaseException:self.row=old;raise
        self.db.fetch_all=all;self.db.fetch_one=one;self.db.transaction=tx
        self.journal=FinalAssessmentJournal(self.db,manager=self.manager,assessment=self.cap,freeze=self.freeze,
            handle=self.handle,token=self.handle.token,lease=self.lease,configuration=self.config,transcript=self.transcript,
            lifecycle_guard=self.handle.coordinator.guard,authorize=lambda cap,op:self.allowed)
    def test_reserved_started_verified_receipt_not_task_terminal(self):
        reserved=self.journal.reserve();self.assertEqual(reserved.phase,'reserved')
        self.assertIs(self.journal.reserve(),reserved)
        running=self.journal.start(reserved)
        user={'role':'user','content':[{'type':'text','text':'assess existing evidence'}]}
        turn=self.transcript.begin_scoped(running.session_id,'final-one',user,running.transcript_identity())
        result=RunResult('completed',[user,{'role':'assistant','content':[{'type':'text','text':'No new work'}]}],{},1,0)
        self.transcript.commit(running.session_id,turn,result)
        final=self.journal.record_finalized(running,turn.id)
        self.assertEqual(final.phase,'finalized');self.assertEqual(final.transcript_turn,turn.id)
        self.assertEqual(self.journal.reconcile(),final)
        self.assertFalse(self.manager.complete('5',self.handle.token,'done'))
        self.assertFalse(self.manager.owns_generation('5',self.handle.token,self.handle))
        self.assertEqual(self.manager.deadline_owners()[0].state,'assessing')
    def test_lease_or_copied_freeze_cannot_reserve(self):
        with self.assertRaises(TaskConflict):self.manager.reserve_deadline_assessment(replace(self.freeze),self.handle.token,self.handle,self.lease,self.config.digest)
        with self.assertRaises(TaskConflict):self.manager.reserve_deadline_assessment(self.freeze,self.handle.token,self.handle,self.lease,'b'*64)
    def test_missing_transcript_no_finalize(self):
        running=self.journal.start(self.journal.reserve())
        with self.assertRaises(AssessmentConflict):self.journal.record_finalized(running,'a'*32)
        self.assertEqual(self.row.phase,'running')
    def test_pending_attempt_not_adopted_by_new_journal(self):
        self.journal.reserve()
        other=FinalAssessmentJournal(self.db,manager=self.manager,assessment=self.cap,freeze=self.freeze,handle=self.handle,
            token=self.handle.token,lease=self.lease,configuration=self.config,transcript=self.transcript,
            lifecycle_guard=self.handle.coordinator.guard,authorize=lambda *a:True)
        with self.assertRaises(AssessmentConflict):other.reserve()
    def test_copied_receipt_and_stale_revision_no_start(self):
        r=self.journal.reserve()
        with self.assertRaises(AssessmentConflict):self.journal.start(replace(r))
        self.row=replace(self.row,revision=9)
        with self.assertRaises(AssessmentConflict):self.journal.start(r)
    def test_post_attempt_uncertainty_quarantines_and_read_reconcile_only(self):
        r=self.journal.reserve();self.sql_fail=True
        with self.assertRaises(AssessmentUncertain) as err:self.journal.start(r)
        self.assertNotIn('private',str(err.exception))
        self.assertEqual(self.manager.deadline_owners()[0].state,'quarantined')
        self.sql_fail=False
        with self.assertRaises((AssessmentConflict,AssessmentUncertain)):self.journal.start(r)
        self.assertEqual(self.journal.reconcile(),r)
        self.assertEqual(self.manager.deadline_owners()[0].state,'quarantined')
    def test_denied_before_any_journal_sql(self):
        self.allowed=False
        with self.assertRaises(AssessmentConflict):self.journal.reserve()
        self.assertEqual(self.calls,[])

    def test_replaced_guard_never_entered(self):
        entered=[]
        @contextmanager
        def replacement(*args):
            entered.append(True);yield
        self.journal.guard=replacement
        with self.assertRaises(AssessmentConflict):self.journal.reserve()
        self.assertEqual(entered,[]);self.assertEqual(self.calls,[])
    def test_quarantine_uses_original_freeze_after_callback_rebind(self):
        r=self.journal.reserve();old=self.db.fetch_all
        def failing(q):
            if q.sql.startswith('UPDATE public.artex_final_assessment'):
                self.journal.freeze=object()
                raise RuntimeError('private failure')
            return old(q)
        self.db.fetch_all=failing
        with self.assertRaises(AssessmentUncertain) as error:self.journal.start(r)
        self.assertNotIn('private',str(error.exception))
        self.assertEqual(self.manager.deadline_owners()[0].state,'quarantined')

class InstallerTests(unittest.TestCase):
    def test_explicit_database_authority_required_without_sql(self):
        class DB:
            def transaction(self):raise AssertionError('no SQL')
        for args in ({'expected_database':'x','expected_schema':'public'}, {'expected_database':'x','expected_schema':'other','allow_create':True}):
            with self.assertRaises(SchemaRefused):install_final_assessment(DB(),**args)
    def test_fresh_additive_install_only(self):
        class DB:
            def __init__(self):self.scripts=[];self.existing=False
            @contextmanager
            def transaction(self):yield self
            def fetch_one(self,q):
                if q.sql==IDENTITY_SQL:return ('testdb',150000,'off','on',0)
                if q.sql.startswith('SELECT to_regclass'):return ('exists' if self.existing else None,None,'tasks','explorations','bootstrap')
                return (None,)
            def execute_script(self,s):self.scripts.append(s)
        db=DB();install_final_assessment(db,expected_database='testdb',expected_schema='public',allow_create=True)
        self.assertEqual(db.scripts[-1],DDL);self.assertNotIn('ALTER TABLE',DDL)
        db.scripts=[];db.existing=True
        with self.assertRaises(SchemaRefused):install_final_assessment(db,expected_database='testdb',expected_schema='public',allow_create=True)
        self.assertNotIn(DDL,db.scripts)

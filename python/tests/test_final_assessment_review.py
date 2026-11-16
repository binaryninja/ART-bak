"""Independent journal-only authority tests; no assessment runner execution."""
import unittest,contextlib
from unittest.mock import patch
from dataclasses import replace
import test_final_assessment as fixture
from artex.db.final_assessment import AssessmentConflict,AssessmentUncertain

class FinalJournalReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_guard_exit_revocation_cannot_return_reserved_success(self):
        f=self.f;original=contextlib._GeneratorContextManager.__exit__;called=[]
        def exiting(cm,*args):
            result=original(cm,*args)
            if cm.gen.gi_code is f.journal.guard.__wrapped__.__code__:
                called.append(True);f.allowed=False
            return result
        with patch.object(contextlib._GeneratorContextManager,'__exit__',exiting):
            with self.assertRaises(AssessmentUncertain):f.journal.reserve()
        self.assertTrue(called)
        self.assertEqual(f.manager.deadline_owners()[0].state,'quarantined')
    def test_transaction_exit_revocation_after_start_is_uncertain(self):
        f=self.f;r=f.journal.reserve();original=f.db.transaction
        @contextlib.contextmanager
        def tx():
            with original() as value:yield value
            f.allowed=False
        f.db.transaction=tx
        with self.assertRaises(AssessmentUncertain):f.journal.start(r)
        self.assertEqual(f.manager.deadline_owners()[0].state,'quarantined')
    def test_foreign_bootstrap_row_cannot_allocate_attempt(self):
        f=self.f;original=f.db.fetch_all
        def fetch(q):
            if q.sql.startswith('SELECT task_id FROM public.artex_task_bootstrap'):return []
            return original(q)
        f.db.fetch_all=fetch
        with self.assertRaises(AssessmentConflict):f.journal.reserve()
        self.assertIsNone(f.row)
        self.assertFalse(any(q.sql.startswith('INSERT INTO') for q in f.calls))
    def test_unexpired_clock_cannot_allocate_attempt(self):
        f=self.f;original=f.db.fetch_all
        def fetch(q):
            rows=original(q)
            if q.sql.startswith('SELECT first_run_at,deadline_at,timeout_seconds,deadline_at<='):
                return [(*rows[0][:3],False)]
            return rows
        f.db.fetch_all=fetch
        with self.assertRaises(AssessmentConflict):f.journal.reserve()
        self.assertIsNone(f.row)
    def test_original_transcript_recovery_binding_change_blocks_reserve(self):
        f=self.f;f.transcript.recovery_guard=lambda *a:contextlib.nullcontext()
        with self.assertRaises(AssessmentConflict):f.journal.reserve()
        self.assertIsNone(f.row);self.assertEqual(f.calls,[])
    def test_canonical_evidence_removed_before_final_cas_blocks_finalized(self):
        from artex.agent.runtime import RunResult
        f=self.f;r=f.journal.start(f.journal.reserve())
        user={'role':'user','content':[{'type':'text','text':'inert assessment'}]}
        turn=f.transcript.begin_scoped(r.session_id,'fixture-final',user,r.transcript_identity())
        f.transcript.commit(r.session_id,turn,RunResult('completed',[user],{},1,0))
        original=f.db.fetch_all;removed=[]
        def fetch(q):
            if q.sql.startswith('SELECT first_run_at,deadline_at,timeout_seconds,deadline_at<=') and not removed:
                with f.transcript.store._locked(r.session_id,f.transcript.agent_id) as (directory,name):
                    f.transcript.store._write(directory,name,[])
                removed.append(True)
            return original(q)
        f.db.fetch_all=fetch
        with self.assertRaises((AssessmentConflict,AssessmentUncertain)):
            f.journal.record_finalized(r,turn.id)
        self.assertTrue(removed)
        self.assertNotEqual(f.row.phase,'finalized')
    def test_replaced_guard_never_invoked_before_binding_check(self):
        f=self.f;called=[]
        @contextlib.contextmanager
        def replacement(*args):
            called.append(True);yield
        f.journal.guard=replacement
        with self.assertRaises(AssessmentConflict):f.journal.reserve()
        self.assertEqual(called,[])
        self.assertEqual(f.calls,[])
    def test_transition_and_inspection_guard_exit_revocation(self):
        for operation in ('start','inspect'):
            with self.subTest(operation=operation):
                f=fixture.Tests();f.setUp();self.addCleanup(f.doCleanups)
                reserved=f.journal.reserve();original=contextlib._GeneratorContextManager.__exit__
                def exiting(cm,*args):
                    result=original(cm,*args)
                    if cm.gen.gi_code is f.journal.guard.__wrapped__.__code__:f.allowed=False
                    return result
                with patch.object(contextlib._GeneratorContextManager,'__exit__',exiting):
                    if operation=='start':
                        with self.assertRaises(AssessmentUncertain):f.journal.start(reserved)
                        self.assertEqual(f.manager.deadline_owners()[0].state,'quarantined')
                    else:
                        with self.assertRaises(AssessmentConflict):f.journal.reconcile()

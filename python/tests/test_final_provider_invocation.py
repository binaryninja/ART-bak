"""Runner-issued final capability tests over real journal and inert transport."""
from copy import copy
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import unittest
from artex.agent.final_assessment import FinalProviderInvocation
from artex.agent.runtime import RuntimeContext
from artex.db.final_assessment import AssessmentConflict,AssessmentUncertain
import test_final_assessment_runner as fixture

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_only_runner_issues_and_live_context_scope_are_exact(self):
        f=self.f;seen=[];original=f.provider.complete
        with self.assertRaises(AssessmentConflict):FinalProviderInvocation(f.runner,object())
        def complete(context,request):
            inv=f.runner._invocation;seen.append(inv)
            self.assertTrue(inv.validate_execution());self.assertTrue(inv.validate_context(context))
            count=len(f.f.calls);self.assertTrue(inv.current(inv.scope));self.assertEqual(len(f.f.calls),count)
            with self.assertRaises(AssessmentConflict):copy(inv).current(inv.scope)
            with self.assertRaises(AssessmentConflict):inv.current(replace(inv.scope))
            copied=RuntimeContext(dict(context),context.token,context.deadline,context.clock)
            with self.assertRaises(AssessmentConflict):inv.validate_context(copied)
            with ThreadPoolExecutor(max_workers=1) as pool:
                with self.assertRaises(AssessmentConflict):pool.submit(inv.current,inv.scope).result(1)
            self.assertGreater(inv.remaining_seconds(),0)
            self.assertEqual(inv.deadline,f.runner._deadline)
            return original(context,request)
        f.provider.complete=complete;f.runner.run()
        with self.assertRaises(AssessmentConflict):seen[0].validate_execution()
    def test_revoke_before_final_record_callback(self):
        f=self.f;original=f.f.journal.record_finalized;observed=[]
        def record(*args):
            inv=f.runner._invocation
            with self.assertRaises(AssessmentConflict):inv.current(inv.scope)
            observed.append(True);return original(*args)
        f.f.journal.record_finalized=record
        f.runner.run();self.assertEqual(observed,[True])
    def test_baseexception_revokes_before_failure_cleanup(self):
        f=self.f;original=f.f.journal._failed;observed=[]
        def failed():
            inv=f.runner._invocation
            with self.assertRaises(AssessmentConflict):inv.current(inv.scope)
            observed.append(True);return original()
        f.f.journal._failed=failed
        def complete(*args):raise KeyboardInterrupt('inert transport interrupt')
        f.provider.complete=complete
        with self.assertRaises(AssessmentUncertain):f.runner.run()
        self.assertEqual(observed,[True]);self.assertTrue(f.runner._invocation_revoked)
    def test_context_token_replacement_denied(self):
        f=self.f;original=f.provider.complete;observed=[]
        def complete(context,request):
            inv=f.runner._invocation;old=context.token
            try:
                context.token=object()
                with self.assertRaises(AssessmentConflict):inv.validate_context(context)
                observed.append(True)
            finally:context.token=old
            return original(context,request)
        f.provider.complete=complete;f.runner.run();self.assertEqual(observed,[True])
    def test_attempt_mutation_denied_without_current_sql(self):
        f=self.f;original=f.provider.complete;observed=[]
        def complete(context,request):
            inv=f.runner._invocation;r=inv.receipt;old=r.attempt_id
            try:
                object.__setattr__(r,'attempt_id',old+1)
                count=len(f.f.calls)
                with self.assertRaises(AssessmentConflict):inv.current(inv.scope)
                self.assertEqual(len(f.f.calls),count);observed.append(True)
            finally:object.__setattr__(r,'attempt_id',old)
            return original(context,request)
        f.provider.complete=complete;f.runner.run();self.assertEqual(observed,[True])

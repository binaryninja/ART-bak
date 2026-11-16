"""Independent exact final-retirement proof and post-removal tail review."""
import unittest
from threading import Event,Thread
from artex.agent.owned_finalization import FinalizationUnavailable
import test_final_retirement as base

class FinalRetirementReview(unittest.TestCase):
    def setUp(self):
        self.f=base.FinalRetirementTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_semantically_equal_numeric_type_mutation_refused(self):
        f=self.f;receipt=f.settle()
        object.__setattr__(receipt,'run_id',float(receipt.run_id))
        with self.assertRaises(FinalizationUnavailable):f.controller.retirement_result(f.bundle.runner)
    def test_journal_receipt_identity_substitution_refused(self):
        from dataclasses import replace
        f=self.f;f.settle();f.controller.journal._receipt=replace(f.controller.journal._receipt)
        with self.assertRaises(FinalizationUnavailable):f.controller.retirement_result(f.bundle.runner)
    def test_accessor_does_not_invoke_sql_or_policy_callbacks(self):
        f=self.f;receipt=f.settle();before=len(f.sql);calls=len(f.calls)
        self.assertIs(f.controller.retirement_result(f.bundle.runner),receipt)
        self.assertEqual(len(f.sql),before);self.assertEqual(len(f.calls),calls)
    def test_after_removal_confirmation_failure_retains_then_retries_without_sql(self):
        f=self.f;f.settle();owner=f.bundle.manual_actions;enter=owner._enter;failed=[]
        def fail_once(component,op):
            if op=='confirm_runner_removal' and not failed:
                failed.append(True);owner.stop_new_actions();raise RuntimeError('inert confirmation failure')
            return enter(component,op)
        owner._enter=fail_once
        with self.assertRaises(RuntimeError):f.bundle.host.poll()
        self.assertNotIn(f.handle,f.bundle.runner.handles())
        self.assertEqual(owner.snapshot().pending_removal,1)
        self.assertIn('5',f.bundle.runner._frontier);self.assertIn(f.handle,f.bundle.runner._final_retirement)
        before=len(f.sql);f.bundle.host.poll()
        self.assertEqual(owner.snapshot().pending_removal,0);self.assertFalse(f.bundle.runner._frontier)
        self.assertEqual(len(f.sql),before);self.assertEqual(len(f.calls),1)
    def test_active_post_retirement_callback_tail_cannot_detach_or_close(self):
        f=self.f;entered=Event();release=Event();errors=[];cancel=f.handle.token.cancel
        def blocked(reason):
            entered.set()
            if not release.wait(3):raise RuntimeError('fixture tail timeout')
            cancel(reason)
        f.handle.token.cancel=blocked
        def settle():
            try:f.settle()
            except BaseException as exc:errors.append(exc)
        t=Thread(target=settle);t.start()
        try:
            self.assertTrue(entered.wait(2));self.assertEqual(f.manager.retirement_snapshot('5')[0],'done')
            with self.assertRaises(FinalizationUnavailable):f.controller.retirement_result(f.bundle.runner)
            self.assertIn(f.handle,f.bundle.runner.handles())
            f.bundle.manual_actions.stop_new_actions()
            self.assertFalse(f.bundle.manual_actions.wait(0).drained)
        finally:release.set();t.join(3)
        self.assertFalse(t.is_alive());self.assertFalse(errors)
        f.bundle.host.poll();self.assertFalse(f.bundle.runner.handles())
        self.assertEqual(len(f.calls),1)
    def test_custom_timezone_never_runs_callback_in_retirement_accessor(self):
        from datetime import tzinfo,timedelta
        f=self.f;receipt=f.settle();calls=[]
        class CustomTZ(tzinfo):
            def utcoffset(self,dt):calls.append('offset');return timedelta(0)
        changed=receipt.completed_at.replace(tzinfo=CustomTZ())
        object.__setattr__(receipt,'completed_at',changed)
        with self.assertRaises(FinalizationUnavailable):f.controller.retirement_result(f.bundle.runner)
        self.assertEqual(calls,[])

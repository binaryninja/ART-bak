"""Independent owned-chain regressions; all runners are inert callbacks."""
import unittest
import test_task_process as fixture
from artex.agent.bootstrap import BootstrapUnresolved
from artex.tools.process import ProcessResult

class ProcessReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def test_launch_grant_revoked_by_later_current_check(self):
        f=self.f
        worker=f.h._child.worker
        original=worker.current
        def current(scope,phase):
            result=original(scope,phase)
            if f.grants:
                f.allowed=False
            return result
        worker.current=current
        f.f.advance(f.h,1)
        self.assertFalse(f.allowed)
        self.assertEqual(f.calls,[], 'Revoked launch authority must not invoke runner')

    def test_baseexception_after_entry_retains_effect_ownership(self):
        f=self.f
        def run(context,launch):raise KeyboardInterrupt('inert callback')
        f.hook=run
        with self.assertRaises(BootstrapUnresolved):f.f.advance(f.h,1)
        self.assertTrue(f.process.unresolved())
        self.assertFalse(f.process.drained())
        self.assertFalse(f.h.wait(0))
        self.assertEqual(f.h._child.worker.store.get(3).node.state,'running')
        with self.assertRaises(BootstrapUnresolved):f.f.advance(f.h,1)
        self.assertEqual(len(f.calls),1)

    def test_boolean_exit_code_is_not_a_drain_receipt(self):
        f=self.f
        f.hook=lambda c,l:ProcessResult(b'',True)
        with self.assertRaises(BootstrapUnresolved):f.f.advance(f.h,1)
        self.assertTrue(f.process.unresolved())
        self.assertFalse(f.h.wait(0))

    def test_policy_swap_during_return_retains_uncertainty(self):
        f=self.f
        def run(c,l):
            object.__setattr__(f.policy,'authorize',lambda *a:None)
            return ProcessResult(b'',0)
        f.hook=run
        with self.assertRaises(BootstrapUnresolved):f.f.advance(f.h,1)
        self.assertTrue(f.process._uncertain)
        self.assertEqual(f.h._child.worker.store.get(3).node.state,'running')
        self.assertEqual(len(f.calls),1)

    def test_successful_drain_receipt_after_cancel_does_not_complete_intent(self):
        f=self.f
        def run(c,l):
            f.h.token.cancel('paused_by_user')
            return ProcessResult(b'',0)
        f.hook=run
        result=f.f.advance(f.h,1)
        self.assertTrue(result.cancelled)
        # Verified runner return drains process effects, not successful intent work.
        self.assertTrue(f.process.drained())
        self.assertFalse(f.process.unresolved())
        self.assertEqual(f.h._child.worker.store.get(3).node.state,'blocked')
        self.assertTrue(f.f.advance(f.h,1).cancelled)
        self.assertEqual(len(f.calls),1)

    def test_final_launch_grant_cancellation_prevents_runner(self):
        f=self.f
        class Grants(list):
            def append(self,launch):
                super().append(launch)
                if len(self)==2:f.h.token.cancel('paused_by_user')
        f.grants=Grants()
        try:f.f.advance(f.h,1)
        except BootstrapUnresolved:pass
        self.assertEqual(len(f.grants),2)
        self.assertEqual(f.calls,[])
        self.assertTrue(f.process.drained())

    def test_final_launch_grant_policy_swap_prevents_runner(self):
        f=self.f
        class Grants(list):
            def append(self,launch):
                super().append(launch)
                if len(self)==2:object.__setattr__(f.policy,'runner',lambda *a:ProcessResult(b'',0))
        f.grants=Grants()
        try:f.f.advance(f.h,1)
        except Exception:pass
        self.assertEqual(len(f.grants),2)
        self.assertEqual(f.calls,[])
        self.assertEqual(f.process._active,0)
        self.assertFalse(f.process._uncertain)

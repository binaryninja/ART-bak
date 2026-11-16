"""Admitted exact worker expiry over actual owned cleanup; no positive admission."""
import unittest
import test_inflight_cleanup as fixture
from artex.agent.deadline import TaskBudgetExpired,DispatchClockUnavailable
from artex.agent.bootstrap import BootstrapUnresolved

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.f.f.seed();self.canonical=[]
        transcript=self.f.h._child.worker.transcript;original=transcript.commit
        def commit(session,turn,result):
            self.canonical.append(result);return original(session,turn,result)
        transcript.commit=commit
        self.f.f.f.wp.hook=self.expire
    @staticmethod
    def expire():raise TaskBudgetExpired()
    def test_typed_admitted_expiry_persists_and_settles_exhausted(self):
        f=self.f;result=f.f.advance(f.h,1)
        self.assertEqual(result.action,'worker')
        self.assertEqual(f.h._child.worker.store.get(3).node.state,'exhausted')
        self.assertEqual([r.reason for r in self.canonical],['task_deadline_expired'])
        self.assertFalse(f.f.manager._has_cleanup('5'));self.assertFalse(f.h.unresolved)
        self.assertEqual(len(f.f.f.wp.calls),1)
    def test_generic_clock_error_remains_blocked(self):
        def fail():raise DispatchClockUnavailable('inert unavailable')
        self.f.f.f.wp.hook=fail;self.f.f.advance(self.f.h,1)
        self.assertEqual(self.f.h._child.worker.store.get(3).node.state,'blocked')
        self.assertNotEqual(self.canonical[0].reason,'task_deadline_expired')
    def test_commit_denial_retains_claim_and_cleanup_cap(self):
        # Deny only once a real turn has been reserved, not before admission.
        def expire():self.f.allowed=False;raise TaskBudgetExpired()
        self.f.f.f.wp.hook=expire
        with self.assertRaises(BootstrapUnresolved):self.f.f.advance(self.f.h,1)
        self.assertEqual(self.f.h._child.worker.store.get(3).node.state,'running')
        self.assertTrue(self.f.f.manager._has_cleanup('5'));self.assertTrue(self.f.h.unresolved)
        self.assertEqual(self.canonical,[])
    def test_worker_cas_failure_retains_cap_after_canonical_expiry(self):
        self.f.f.db.fail_settle=True
        with self.assertRaises(BootstrapUnresolved):self.f.f.advance(self.f.h,1)
        self.assertEqual(self.f.h._child.worker.store.get(3).node.state,'running')
        self.assertEqual(self.canonical[0].reason,'task_deadline_expired')
        self.assertTrue(self.f.f.manager._has_cleanup('5'));self.assertTrue(self.f.h.unresolved)
    def test_explicit_pause_remains_original_cancellation_mapping(self):
        def expire():self.f.f.host.pause('5',timeout=0);raise TaskBudgetExpired()
        self.f.f.f.wp.hook=expire;self.f.f.advance(self.f.h,1)
        self.assertEqual(self.f.h._child.worker.store.get(3).node.state,'open')
        self.assertFalse(self.f.f.manager._has_cleanup('5'))

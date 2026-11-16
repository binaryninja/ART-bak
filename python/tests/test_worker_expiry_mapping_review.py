"""Independent real owned cleanup fixtures; no naturally enabled timed tasks."""
import unittest
import test_worker_task_expiry_cleanup as base
from artex.agent.bootstrap import BootstrapUnresolved
from artex.agent.deadline import TaskBudgetExpired

class Tests(unittest.TestCase):
    def setUp(self):
        self.b=base.Tests();self.b.setUp();self.addCleanup(self.b.doCleanups)
    def test_message_named_expiry_is_not_typed_expiry(self):
        b=self.b
        def fail():raise RuntimeError('task_deadline_expired')
        b.f.f.f.wp.hook=fail;b.f.f.advance(b.f.h,1)
        self.assertEqual(b.f.h._child.worker.store.get(3).node.state,'blocked')
        self.assertNotEqual(b.canonical[0].reason,'task_deadline_expired')
    def test_commit_ack_lost_keeps_canonical_claim_quarantined(self):
        b=self.b;transcript=b.f.h._child.worker.transcript;original=transcript.commit;written=[]
        def commit(*args):
            written.append(original(*args));raise RuntimeError('synthetic lost ack')
        transcript.commit=commit
        with self.assertRaises(BootstrapUnresolved):b.f.f.advance(b.f.h,1)
        self.assertEqual(written,[True])
        self.assertEqual(b.f.h._child.worker.store.get(3).node.state,'running')
        self.assertTrue(b.f.f.manager._has_cleanup('5'));self.assertFalse(b.f.f.host.drain(0).all_settled)
    def test_claim_version_drift_never_exhausts_foreign_version(self):
        b=self.b
        def expire():
            b.f.f.db.versions[3]+=1
            raise TaskBudgetExpired()
        b.f.f.f.wp.hook=expire
        with self.assertRaises(BootstrapUnresolved):b.f.f.advance(b.f.h,1)
        self.assertEqual(b.f.h._child.worker.store.get(3).node.state,'running')
        self.assertTrue(b.f.f.manager._has_cleanup('5'));self.assertTrue(b.f.h.unresolved)

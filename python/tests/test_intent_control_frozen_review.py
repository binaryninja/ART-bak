"""Independent frozen pending-pause checks using original registered fixture."""
import copy
import unittest
from unittest.mock import patch
import test_intent_control_frozen as base
from artex.agent.intent_control import IntentControl

class Review(unittest.TestCase):
    def fixture(self):
        self.allowed=True
        f=base.Tests()
        with patch.object(base,'IntentControl',lambda host,**kw:IntentControl(host,authorize=lambda p:self.allowed)):
            f.setUp()
        self.addCleanup(f.doCleanups)
        self.assertEqual(f.f.bundle.host.control_intent('5',3,'pause',timeout=0).phase,'pending')
        f.expired=True
        self.assertEqual(f.f.actions.observer.tick().phase,'draining')
        return f
    def denied(self,f):
        with self.assertRaises(Exception):f.f.bundle.host.inspect_intent_control('5',3,timeout=0)
        self.assertTrue(f.adapter.retained(f.f.handle))
        self.assertEqual(f.calls,[1])
    def test_copied_freeze_refused(self):
        f=self.fixture();f.finish()
        f.f.actions.observer._freeze=copy.copy(f.f.actions.observer._freeze)
        self.denied(f)
    def test_copied_lease_refused(self):
        f=self.fixture();f.finish()
        f.f.actions.observer._lease=copy.copy(f.f.actions.observer._lease)
        self.denied(f)
    def test_transfer_refused(self):
        f=self.fixture();f.finish()
        f.f.actions.observer._transfer=object()
        self.denied(f)
    def test_revoked_grant_retains_pending(self):
        f=self.fixture();self.allowed=False
        self.denied(f)
        self.assertFalse(f.f.handle.token.cancelled)
    def test_repeat_frozen_pending_does_not_recancel_or_run(self):
        f=self.fixture();worker=f.f.handle._child.worker
        # Pinned cancel method must remain unchanged; observe existing pause
        # cancellation by the canonical terminal result after two pending reads.
        for _ in range(2):
            self.assertEqual(f.f.bundle.host.inspect_intent_control('5',3,timeout=0).phase,'pending')
        self.assertEqual(f.calls,[1]);self.assertFalse(f.f.handle.token.cancelled)
        f.finish()
        result=f.f.bundle.host.inspect_intent_control('5',3,timeout=0)
        self.assertEqual((result.phase,result.state,result.version),('confirmed','paused',2))
        self.assertFalse(f.adapter.retained(f.f.handle));self.assertFalse(f.errors)
        self.assertEqual(f.calls,[1])

if __name__=='__main__':unittest.main()

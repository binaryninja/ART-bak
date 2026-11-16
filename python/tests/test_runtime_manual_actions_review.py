"""Independent listener registry pinning and irreversible sealing."""
import unittest
from unittest.mock import patch
from artex.server.runtime_listener import RuntimeListener,RuntimeResourceOwner
import test_runtime_manual_actions as fixture

class ManualListenerReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_registry_operation_swap_inside_admission_callback_blocks_close(self):
        f=self.f
        def admissions():
            f.manual.cancel_active=lambda:None
            return True
        owner=RuntimeResourceOwner(f.f.pool,admissions,f.f.owner.cancel_owned,f.f.owner.close_resources)
        listener=RuntimeListener(f.bundle,owner)
        with patch.object(f.f.pool,'close') as close:
            result=listener.stop(0)
            self.assertFalse(result.stopped);close.assert_not_called()
        self.assertTrue(f.manual.snapshot().sealed);self.assertNotIn('resources',f.f.calls)
    def test_failed_host_admission_keeps_manual_registry_sealed(self):
        f=self.f
        owner=RuntimeResourceOwner(f.f.pool,lambda:False,f.f.owner.cancel_owned,f.f.owner.close_resources)
        listener=RuntimeListener(f.bundle,owner)
        with patch.object(f.f.pool,'close') as close:
            result=listener.stop(0)
            self.assertEqual(result.reason,'admission_unresolved');close.assert_not_called()
        self.assertTrue(f.manual.snapshot().sealed);self.assertEqual(f.f.calls,[])
    def test_constructor_rejects_preexisting_wait_substitution(self):
        f=self.f
        f.manual.wait=lambda seconds:None
        with self.assertRaises(ValueError):RuntimeListener(f.bundle,f.f.owner)
        self.assertEqual(f.f.calls,[])

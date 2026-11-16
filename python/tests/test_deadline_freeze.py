"""Actual registry/host ownership with synthetic positive clock fixtures only.
Positive production task admission remains rejected; no real models or timers.
"""
import unittest
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from artex.manager import TaskConflict
import test_deadline_owned_registration as fixture

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.OwnedClockTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.f.call();self.manager=self.f.manager;self.host=self.f.host
        self.handle=self.f.f.f.handle;self.lease=self.f.store.lease
    def freeze(self,timeout=0):return self.host.freeze_deadline('5',self.handle.token,self.handle,self.lease,timeout=timeout)
    def test_exact_owned_freeze_drains_without_cancel_or_terminal_authority(self):
        receipt,drained=self.freeze();self.assertTrue(drained)
        self.assertFalse(self.handle.token.cancelled)
        self.assertEqual(self.manager.deadline_owners()[0].state,'drained')
        self.assertFalse(self.manager.owns_generation('5',self.handle.token,self.handle))
        self.assertTrue(self.manager.owns_generation('5',self.handle.token,self.handle,allow_cancelled=True))
        self.assertFalse(self.manager.complete('5',self.handle.token,'timeout'))
        self.assertFalse(self.manager.acknowledge_deadline_retirement('5',self.handle.token,self.handle))
        self.assertFalse(self.manager.validate_deadline_owner(self.lease))
        self.assertIs(self.freeze()[0],receipt)
        with self.assertRaises(TaskConflict):self.host.start('5')
    def test_copied_or_foreign_capability_rejected(self):
        with self.assertRaises(TaskConflict):self.host.freeze_deadline('5',self.handle.token,self.handle,replace(self.lease))
        receipt,_=self.freeze()
        with self.assertRaises(TaskConflict):self.manager.confirm_deadline_drain(replace(receipt),self.handle.token,self.handle)
    def test_bounded_pending_retains_then_explicit_recheck_drains(self):
        original=self.handle.wait;self.handle.wait=lambda timeout:False
        receipt,drained=self.freeze();self.assertFalse(drained)
        self.assertEqual(self.manager.deadline_owners()[0].state,'freezing')
        self.assertFalse(self.manager.owns_generation('5',self.handle.token,self.handle))
        self.handle.wait=original
        again,drained=self.freeze();self.assertIs(again,receipt);self.assertTrue(drained)
    def test_wait_never_holds_registry_lock(self):
        original=self.handle.wait
        with ThreadPoolExecutor(max_workers=1) as pool:
            def wait(timeout):
                self.assertTrue(pool.submit(self.manager.deadline_owners).result(timeout=1))
                return original(timeout)
            self.handle.wait=wait
            self.assertTrue(self.freeze()[1])
    def test_suspended_exact_owner_freeze_retained_not_resumed(self):
        self.host.pause('5')
        self.assertTrue(self.freeze()[1])
        self.assertTrue(self.handle.token.cancelled)
        self.assertFalse(self.manager.complete('5',self.handle.token,'done'))
    def test_existing_token_cancellation_does_not_create_new_authority(self):
        self.handle.token.cancel('external')
        with self.assertRaises(TaskConflict):self.freeze()

    def test_wait_error_retains_quarantined_owner(self):
        def broken(timeout):raise RuntimeError('private callback error')
        self.handle.wait=broken
        with self.assertRaises(TaskConflict) as error:self.freeze()
        self.assertNotIn('private',str(error.exception))
        self.assertEqual(self.manager.deadline_owners()[0].state,'quarantined')
        self.assertFalse(self.manager.complete('5',self.handle.token,'timeout'))

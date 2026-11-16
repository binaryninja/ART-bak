"""Independent freeze protocol tests; synthetic clocks, no timeout enablement."""
import unittest
from dataclasses import replace
import test_deadline_freeze as fixture
from artex.manager import TaskConflict,CancellationToken

class FreezeReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_first_wait_success_cannot_fake_second_drain_evidence(self):
        f=self.f;calls=[]
        def wait(timeout):calls.append(timeout);return len(calls)==1
        f.handle.wait=wait
        receipt,drained=f.freeze()
        self.assertFalse(drained);self.assertEqual(len(calls),2)
        self.assertEqual(f.manager.deadline_owners()[0].state,'freezing')
        self.assertFalse(f.manager.owns_generation('5',f.handle.token,f.handle))
    def test_token_replaced_inside_wait_quarantines(self):
        f=self.f;token=f.handle.token
        def wait(timeout):f.handle.token=CancellationToken();return True
        f.handle.wait=wait
        with self.assertRaises(TaskConflict):f.host.freeze_deadline('5',token,f.handle,f.lease)
        self.assertEqual(f.manager.deadline_owners()[0].state,'quarantined')
        self.assertFalse(f.manager.complete('5',token,'timeout'))
    def test_receipt_replaced_inside_wait_quarantines(self):
        f=self.f
        def wait(timeout):f.f.store.receipt=replace(f.f.store.receipt);return True
        f.handle.wait=wait
        with self.assertRaises(TaskConflict):f.freeze()
        self.assertEqual(f.manager.deadline_owners()[0].state,'quarantined')
    def test_fake_truthy_wait_not_drain(self):
        f=self.f;f.handle.wait=lambda timeout:1
        with self.assertRaises(TaskConflict):f.freeze()
        self.assertEqual(f.manager.deadline_owners()[0].state,'quarantined')
    def test_freeze_does_not_write_or_call_provider(self):
        f=self.f;sql=f.f.f.f.sql
        before=list(sql)
        f.freeze()
        self.assertEqual(sql,before)
        with self.assertRaises(Exception):f.f.f.call()
        self.assertEqual(sql,before)

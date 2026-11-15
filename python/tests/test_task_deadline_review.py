"""Independent exact-generation/persistence-interruption tests; inert SQL."""
import unittest
from contextlib import contextmanager
from artex.db.task_deadline import DeadlineConflict,DeadlineUncertain
import test_task_deadline as fixture

class DeadlineReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.DeadlineTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_changed_bootstrap_policy_rejected_before_stamp(self):
        self.f.handle.policy_revision='different-policy'
        with self.assertRaises(DeadlineConflict):self.f.store.stamp(0)
        self.assertEqual(self.f.sql,[])
    def test_owner_coordinator_executor_rebind_rejected(self):
        self.f.handle.coordinator.journal.executor=object()
        with self.assertRaises(DeadlineConflict):self.f.store.stamp(0)
        self.assertEqual(self.f.sql,[])
    def test_postcommit_baseexception_is_uncertain_not_unclassified(self):
        class Interrupted(BaseException):pass
        original=self.f.db.transaction
        @contextmanager
        def transaction():
            with original() as tx:yield tx
            raise Interrupted('private interrupted storage commit')
        self.f.db.transaction=transaction
        with self.assertRaises(DeadlineUncertain):self.f.store.stamp(0)
    def test_foreign_installed_handle_rejects_before_sql(self):
        from artex.manager import CancellationToken
        self.f.handle.token=CancellationToken()
        with self.assertRaises(DeadlineConflict):self.f.store.stamp(0)
        self.assertEqual(self.f.sql,[])
    def test_alternate_receipt_same_run_never_adopted(self):
        from dataclasses import replace
        from artex.db.task_deadline import TaskDeadlineStore
        other=replace(self.f.receipt,revision=self.f.receipt.revision+1)
        store=TaskDeadlineStore(self.f.db,manager=self.f.f.f.manager,handle=self.f.handle,receipt=other,
            lifecycle_guard=self.f.handle.coordinator.guard,authorize=lambda *a:True)
        with self.assertRaises(DeadlineConflict):store.stamp(0)
        self.assertEqual(self.f.sql,[])
    def test_unshared_guard_rejected_at_construction(self):
        from contextlib import nullcontext
        from artex.db.task_deadline import TaskDeadlineStore
        with self.assertRaises(ValueError):
            TaskDeadlineStore(self.f.db,manager=self.f.f.f.manager,handle=self.f.handle,receipt=self.f.receipt,
                lifecycle_guard=lambda *a:nullcontext(),authorize=lambda *a:True)
        self.assertEqual(self.f.sql,[])
    def test_prewrite_control_flow_interrupt_preserved(self):
        class Interrupted(BaseException):pass
        failure=Interrupted('inert control flow')
        def stop(*a):raise failure
        self.f.store.authorize=stop
        with self.assertRaises(Interrupted) as raised:self.f.store.stamp(0)
        self.assertIs(raised.exception,failure);self.assertEqual(self.f.sql,[])

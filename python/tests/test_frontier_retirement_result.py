"""Exact callback-free retirement evidence; no runner removal or new grants."""
import unittest
from dataclasses import replace
from artex.agent.frontier_completion import FrontierUnavailable
import test_frontier_completion as fixture

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_original_result_both_branches_no_new_callbacks(self):
        for stamped in (False,True):
            if stamped:self.f.setup_branch(True)
            f=self.f;c=f.component;r=c.run_once().receipt
            queries=len(f.f.queries)
            self.assertIs(c.retirement_result(f.host.runner),r)
            self.assertEqual(len(f.f.queries),queries)
            self.assertEqual(f.owner.snapshot().pending_removal,1)
    def test_public_result_copy_is_not_retirement_evidence(self):
        f=self.f;c=f.component;c.run_once();c.result=replace(c.result)
        with self.assertRaises(FrontierUnavailable):c.retirement_result(f.host.runner)
        self.assertIn(f.handle,f.host.runner.handles())
    def test_foreign_runner_and_precompletion_rejected(self):
        f=self.f;c=f.component
        with self.assertRaises(FrontierUnavailable):c.retirement_result(f.host.runner)
        c.run_once()
        with self.assertRaises(FrontierUnavailable):c.retirement_result(object())
    def test_mutated_store_or_registry_receipt_rejected(self):
        f=self.f;c=f.component;c.run_once()
        c._store._result=replace(c.result)
        with self.assertRaises(FrontierUnavailable):c.retirement_result(f.host.runner)
        c._store._result=c.result
        f.host.manager._tasks['5']['status']='failed'
        with self.assertRaises(FrontierUnavailable):c.retirement_result(f.host.runner)
    def test_postack_revoke_does_not_undo_retirement(self):
        f=self.f;c=f.component;r=c.run_once().receipt
        f.f.permit.revoke()
        self.assertIs(c.retirement_result(f.host.runner),r)
        self.assertEqual(f.owner.snapshot().pending_removal,1)
    def test_consumed_continuation_rejected(self):
        f=self.f;c=f.component;c.run_once()
        f.host.runner.settle_completed(f.host.manager.complete)
        self.assertTrue(c.confirm_runner_removal())
        with self.assertRaises(FrontierUnavailable):c.retirement_result(f.host.runner)

    def test_original_atomic_snapshot_cannot_be_replaced(self):
        from artex.manager import TaskManager
        from unittest.mock import patch
        f=self.f;c=f.component;c.run_once()
        with patch.object(TaskManager,'retirement_snapshot',lambda *a:('done',c.result.completed_at.isoformat(),False,False)):
            with self.assertRaises(FrontierUnavailable):c.retirement_result(f.host.runner)
    def test_atomic_snapshot_reports_run_and_owner_without_callbacks(self):
        from artex.manager import TaskManager
        f=self.f;m=f.host.manager
        before=TaskManager.retirement_snapshot(m,'5')
        self.assertEqual(before[2:],(True,False))
        f.component.run_once()
        self.assertEqual(TaskManager.retirement_snapshot(m,'5')[2:],(False,False))

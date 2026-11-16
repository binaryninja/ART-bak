"""Runner tail uses actual registered final settlement; no live provider or DB."""
import unittest
from dataclasses import replace
import test_final_retirement as base

class FinalRunnerRetirementTests(unittest.TestCase):
    def setUp(self):
        self.f=base.FinalRetirementTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_actual_poll_removes_exact_final_handle_and_pending_frontier(self):
        f=self.f;receipt=f.settle();before=len(f.sql)
        f.bundle.host.poll()
        self.assertEqual(f.bundle.runner.handles(),())
        self.assertEqual(f.bundle.runner._frontier,{})
        self.assertEqual(f.bundle.runner._final_retirement,{})
        self.assertEqual(f.bundle.manual_actions.snapshot().pending_removal,0)
        self.assertEqual(len(f.sql),before);self.assertEqual(len(f.calls),1)
        with self.assertRaises(Exception):f.controller.retirement_result(f.bundle.runner)
        f.bundle.host.poll();self.assertEqual(len(f.calls),1)
    def test_sealed_registry_allows_only_committed_removal_continuation(self):
        f=self.f;f.settle();f.bundle.manual_actions.stop_new_actions()
        f.bundle.host.poll()
        self.assertEqual(f.bundle.runner.handles(),())
        self.assertEqual(f.bundle.manual_actions.snapshot().pending_removal,0)
    def test_public_result_copy_does_not_detach_or_replay(self):
        f=self.f;f.settle();f.controller.result=replace(f.controller.result)
        with self.assertRaises(Exception):f.bundle.host.poll()
        self.assertIn(f.handle,f.bundle.runner.handles())
        self.assertEqual(len(f.calls),1);self.assertIn('5',f.bundle.runner._frontier)
    def test_changed_confirmation_method_refuses_before_detach(self):
        f=self.f;f.settle();f.controller.confirm_runner_removal=lambda:True
        with self.assertRaises(Exception):f.bundle.host.poll()
        self.assertIn(f.handle,f.bundle.runner.handles())
        self.assertEqual(len(f.calls),1)

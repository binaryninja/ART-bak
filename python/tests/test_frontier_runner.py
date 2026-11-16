"""Explicit hook over synthetic-positive prepared owners; no positive admission."""
import unittest
from artex.manager import TaskConflict
import test_frontier_completion as fixture

class Tests(unittest.TestCase):
    def make(self,stamped=False):
        f=fixture.Tests();f.setup_branch(stamped);self.addCleanup(f.doCleanups)
        return f
    def test_unstamped_and_stamped_exact_retirement(self):
        for stamped in (False,True):
            f=self.make(stamped);f.host.runner.bind_frontier_completion(f.handle,f.component)
            self.assertFalse(f.host.owns_settlement(f.host.runner))
            result=f.host.poll()
            self.assertEqual(result.settled,('5',));self.assertTrue(result.all_settled)
            self.assertTrue(f.owner.snapshot().drained);self.assertEqual(f.host.runner._frontier,{})
    def test_unbound_positive_does_not_use_zero_completion(self):
        f=self.make();self.assertFalse(f.host.poll().all_settled)
        self.assertEqual(f.f.status,'running');self.assertIsNone(f.f.saved)
    def test_binding_inert_and_no_rebinding(self):
        f=self.make();before=len(f.f.queries)
        f.host.runner.bind_frontier_completion(f.handle,f.component)
        self.assertEqual(len(f.f.queries),before)
        with self.assertRaises(TaskConflict):f.host.runner.bind_frontier_completion(f.handle,f.component)
        self.assertIsNone(f.f.saved)
    def test_expired_retains_original_clock_and_handle(self):
        f=self.make(True);f.expired=True
        f.host.runner.bind_frontier_completion(f.handle,f.component)
        result=f.host.poll();self.assertFalse(result.all_settled)
        self.assertIn(f.handle,f.host.runner.handles());self.assertIsNone(f.component._store)
    def test_public_result_substitution_cannot_remove(self):
        f=self.make();f.host.runner.bind_frontier_completion(f.handle,f.component)
        f.component.result=object();f.component._used=True
        with self.assertRaises(Exception):f.host.poll()
        self.assertIn(f.handle,f.host.runner.handles());self.assertIsNone(f.f.saved)
    def test_seal_after_remove_permits_exact_pending_continuation(self):
        f=self.make();f.host.runner.bind_frontier_completion(f.handle,f.component)
        original=f.owner._enter
        def enter(component,operation):
            if operation=='confirm_runner_removal':f.owner.stop_new_actions()
            return original(component,operation)
        f.owner._enter=enter
        self.assertTrue(f.host.poll().all_settled);self.assertTrue(f.owner.snapshot().drained)
    def test_failed_confirmation_retains_binding_and_retries_no_sql_after_seal(self):
        f=self.make();f.host.runner.bind_frontier_completion(f.handle,f.component)
        original=f.owner._enter;failed=[]
        def enter(component,operation):
            if operation=='confirm_runner_removal' and not failed:
                failed.append(True);f.owner.stop_new_actions();raise RuntimeError('inert continuation failure')
            return original(component,operation)
        f.owner._enter=enter
        with self.assertRaises(RuntimeError):f.host.poll()
        self.assertNotIn(f.handle,f.host.runner.handles())
        self.assertEqual(f.owner.snapshot().pending_removal,1);self.assertIn('5',f.host.runner._frontier)
        before=len(f.f.queries)
        self.assertTrue(f.host.poll().all_settled)
        self.assertEqual(len(f.f.queries),before);self.assertTrue(f.owner.snapshot().drained)

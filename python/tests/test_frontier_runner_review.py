"""Independent frontier hook review; synthetic-positive owners, inert callbacks."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import unittest
import test_frontier_completion as fixture
from artex.manager import TaskConflict

class FrontierRunnerReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def bind(self):
        f=self.f;f.host.runner.bind_frontier_completion(f.handle,f.component)

    def test_direct_runner_call_cannot_use_completion_binding(self):
        f=self.f;self.bind()
        with self.assertRaises(Exception):f.host.runner.settle_completed(f.host.manager.complete)
        self.assertEqual(f.f.status,'running')
        self.assertIsNone(f.f.saved)
        self.assertIn(f.handle,f.host.runner.handles())

    def test_open_frontier_waits_without_allocating_then_exact_removal(self):
        f=self.f;f.f.nodes=[(1,'goal','open')];self.bind()
        for _ in range(2):
            self.assertFalse(f.host.poll().all_settled)
            self.assertIsNone(f.component._store)
            self.assertEqual(f.f.status,'running')
        f.f.nodes=[]
        result=f.host.poll()
        self.assertTrue(result.all_settled)
        self.assertEqual(f.f.status,'done')
        self.assertNotIn(f.handle,f.host.runner.handles())
        self.assertTrue(f.owner.snapshot().drained)

    def test_cancel_tail_after_manager_retirement_stays_owned(self):
        f=self.f;self.bind();entered=Event();release=Event();original=f.handle.token.cancel
        def cancel(reason):
            entered.set()
            if not release.wait(3):raise RuntimeError('inert callback timeout')
            original(reason)
        f.handle.token.cancel=cancel
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending=pool.submit(f.host.poll)
            try:
                self.assertTrue(entered.wait(1))
                self.assertEqual(f.host.manager.deadline_owners(),())
                self.assertIn(f.handle,f.host.runner.handles())
                self.assertFalse(f.owner.snapshot().drained)
                self.assertTrue(f.host.poll().busy)
                self.assertFalse(f.host.drain(0).all_settled)
                with self.assertRaises(TaskConflict):f.host.pause('5',timeout=0)
            finally:release.set()
            self.assertTrue(pending.result(1).all_settled)
        self.assertTrue(f.owner.snapshot().drained)

    def test_commit_uncertainty_no_removal_or_replay(self):
        f=self.f;self.bind();f.f.fail_insert=True
        with self.assertRaises(Exception):f.host.poll()
        self.assertIn(f.handle,f.host.runner.handles())
        self.assertFalse(f.owner.snapshot().drained)
        before=len(f.f.queries)
        try:f.host.poll()
        except Exception:pass
        self.assertEqual(len(f.f.queries),before)
        self.assertIn(f.handle,f.host.runner.handles())

    def test_post_removal_confirmation_tail_remains_owned(self):
        f=self.f;self.bind();entered=Event();release=Event();original=f.owner._enter
        def enter(component,operation):
            if operation=='confirm_runner_removal':
                entered.set()
                if not release.wait(3):raise RuntimeError('inert continuation timeout')
            return original(component,operation)
        f.owner._enter=enter
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending=pool.submit(f.host.poll)
            try:
                self.assertTrue(entered.wait(1))
                self.assertNotIn(f.handle,f.host.runner.handles())
                self.assertEqual(f.owner.snapshot().pending_removal,1)
                f.owner.stop_new_actions()
                self.assertFalse(f.owner.wait(0).drained)
                self.assertTrue(f.host.poll().busy)
                self.assertFalse(f.host.drain(0).all_settled)
            finally:release.set()
            self.assertTrue(pending.result(1).all_settled)
        self.assertTrue(f.owner.snapshot().drained)

    def test_result_substitution_by_final_wait_cannot_remove(self):
        from dataclasses import replace
        f=self.f;original=f.handle.wait;changed=[]
        def wait(timeout):
            result=original(timeout)
            if f.component.result is not None:
                f.component.result=replace(f.component.result);changed.append(True)
            return result
        f.handle.wait=wait;self.bind()
        with self.assertRaises(Exception):f.host.poll()
        self.assertTrue(changed)
        self.assertIn(f.handle,f.host.runner.handles())
        self.assertEqual(f.owner.snapshot().pending_removal,1)

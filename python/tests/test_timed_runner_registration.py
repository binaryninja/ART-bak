"""Owned post-prepare seam; actual app fixture, synthetic positive pins only."""
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from artex.agent.timed_lifecycle import PreparedTimedLifecycle
from artex.manager import TaskConflict
import test_timed_lifecycle as fixture

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.ActualRegistrationTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_registered_but_incomplete_cannot_advance_next_model(self):
        b=self.f.prepare();h=b.runner.handles()[0];calls=list(self.f.f.p.calls)
        with self.assertRaises(Exception):b.host.advance_owned(lambda:b.runner.advance(h.task_id,h.token,2))
        self.assertEqual(self.f.f.p.calls,calls)
        self.assertTrue(b.timed_lifecycle.registered(h));self.assertIn(h,b.runner.handles())
    def test_original_work_slot_same_thread_only_and_finally_cleared(self):
        b=self.f.prepare();observed=[]
        def step():
            observed.append(b.host.owns_work(b.runner))
            with ThreadPoolExecutor(max_workers=1) as pool:
                observed.append(pool.submit(b.host.owns_work,b.runner).result(1))
            raise RuntimeError('inert work failure')
        with self.assertRaises(RuntimeError):b.host.advance_owned(step)
        self.assertEqual(observed,[True,False]);self.assertFalse(b.host.owns_work(b.runner))
        self.assertIsNone(b.host._working_thread)
    def test_pre_entry_registration_failure_retains_even_after_cancel(self):
        with patch.object(PreparedTimedLifecycle,'_owner',side_effect=RuntimeError('inert owner validation failure')):
            with self.assertRaises(Exception):self.f.prepare()
        b=self.f.f.bundle;h=b.runner.handles()[0]
        self.assertFalse(b.timed_lifecycle.failed(h)) # entry never allocated
        self.assertEqual(b.runner._timed_attempts[h],'failed')
        h.token.cancel('fixture')
        self.assertFalse(b.host.poll().all_settled)
        self.assertFalse(b.host.drain(0).all_settled)
        self.assertIn(h,b.runner.handles())
        with self.assertRaises(TaskConflict):b.host.advance_owned(lambda:b.runner.advance(h.task_id,h.token,2))
    def test_partial_products_cannot_retire_after_cancel(self):
        self.f.capacity=1
        with self.assertRaises(Exception):self.f.prepare()
        b=self.f.f.bundle;h=b.runner.handles()[0];h.token.cancel('fixture')
        self.assertFalse(b.host.poll().all_settled);self.assertIn(h,b.runner.handles())
        self.assertTrue(b.timed_lifecycle.failed(h))
    def test_second_install_denied_without_new_products(self):
        b=self.f.prepare();before=b.timed_lifecycle.status()
        with self.assertRaises(TaskConflict):b.runner.install_timed_lifecycle(b.timed_lifecycle)
        self.assertEqual(b.timed_lifecycle.status(),before)

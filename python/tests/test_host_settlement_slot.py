"""Inert settlement callback tails must retain the host lifecycle slot."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import unittest
from artex.manager import TaskConflict
from test_agent_host import host_for
from test_agent_runner import make_runner
from test_manager import Repository,record

class Tests(unittest.TestCase):
    def test_post_retirement_tail_keeps_admission_advance_poll_and_drain_fenced(self):
        host=host_for(make_runner(),Repository(record(1),record(2)))
        host.start('1');self.assertTrue(host.runner.handles()[0].wait(2))
        original=host.runner.settle_completed;entered=Event();release=Event()
        def settle(complete):
            result=original(complete);entered.set()
            if not release.wait(3):raise RuntimeError('fixture timeout')
            return result
        host.runner.settle_completed=settle
        with ThreadPoolExecutor(max_workers=2) as pool:
            future=pool.submit(host.poll)
            try:
                self.assertTrue(entered.wait(1));self.assertEqual(host.manager.status('1'),'done')
                # Different thread must acquire the condition promptly, not wait
                # behind arbitrary settlement callbacks while holding shutdown.
                self.assertTrue(pool.submit(host.poll).result(1).busy)
                self.assertFalse(pool.submit(host.drain,0).result(1).all_settled)
                self.assertTrue(pool.submit(host.advance_owned,lambda:None).result(1).busy)
                with self.assertRaises(TaskConflict):pool.submit(host.start,'2').result(1)
            finally:release.set()
            self.assertTrue(future.result(1).all_settled)
        self.assertTrue(host.start('2'));self.assertTrue(host.runner.handles()[0].wait(2));host.poll()

    def test_same_thread_reentry_refused_and_slot_cleared_after_error(self):
        host=host_for(make_runner());original=host.runner.settle_completed
        def settle(complete):
            with self.assertRaises(TaskConflict):host.poll()
            with self.assertRaises(TaskConflict):host.drain(0)
            with self.assertRaises(TaskConflict):host.pause('1',timeout=0)
            self.assertTrue(host.advance_owned(lambda:None).busy)
            raise KeyboardInterrupt('inert callback')
        host.runner.settle_completed=settle
        with self.assertRaises(KeyboardInterrupt):host.poll()
        self.assertFalse(host._settling);self.assertIsNone(host._settlement_thread)
        host.runner.settle_completed=original
        self.assertTrue(host.poll().all_settled)

    def test_bounded_drain_waiter_notified_when_tail_finishes(self):
        host=host_for(make_runner());entered=Event();release=Event()
        def settle(complete):
            entered.set()
            if not release.wait(3):raise RuntimeError('fixture timeout')
            return ()
        host.runner.settle_completed=settle
        with ThreadPoolExecutor(max_workers=2) as pool:
            future=pool.submit(host.poll)
            try:
                self.assertTrue(entered.wait(1))
                draining=pool.submit(host.drain,2)
            finally:release.set()
            self.assertTrue(future.result(1).all_settled)
            self.assertTrue(draining.result(1).all_settled)

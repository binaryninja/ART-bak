"""Independent settlement-slot races; only inert providers and callbacks."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import unittest
from artex.manager import TaskConflict
from test_agent_host import host_for, make_runner, Repository, record

class HostSettlementReview(unittest.TestCase):
    def make(self):
        host=host_for(make_runner(),Repository(record(1),record(2)))
        host.start('1')
        self.assertTrue(host.runner.handles()[0].wait(2))
        return host

    def test_post_retirement_callback_tail_remains_owned(self):
        h=self.make();entered=Event();release=Event()
        original=h.runner.settle_completed
        def settle(complete):
            result=original(complete)
            self.assertEqual(h.runner.handles(),())
            entered.set()
            if not release.wait(3):raise RuntimeError('test callback release timeout')
            return result
        h.runner.settle_completed=settle
        with ThreadPoolExecutor(max_workers=4) as pool:
            first=pool.submit(h.poll)
            try:
                self.assertTrue(entered.wait(1))
                self.assertTrue(pool.submit(h.poll).result(1).busy)
                drained=pool.submit(h.drain,0.02).result(1)
                self.assertFalse(drained.all_settled)
                self.assertFalse(drained.workers_drained)
                self.assertTrue(drained.settlement.busy)
                with self.assertRaises(TaskConflict):pool.submit(h.start,'2').result(1)
                with self.assertRaises(TaskConflict):pool.submit(h.pause,'1',timeout=0).result(1)
                self.assertTrue(pool.submit(h.advance_owned,lambda:None).result(1).busy)
            finally:release.set()
            self.assertTrue(first.result(1).all_settled)
        self.assertTrue(h.poll().all_settled)

    def test_reentrant_poll_and_drain_refuse_without_deadlock(self):
        h=self.make();original=h.runner.settle_completed;observed=[]
        def settle(complete):
            for operation in (h.poll,lambda:h.drain(0)):
                try:operation()
                except TaskConflict:observed.append('refused')
                else:observed.append('accepted')
            return original(complete)
        h.runner.settle_completed=settle
        self.assertTrue(h.poll().all_settled)
        self.assertEqual(observed,['refused','refused'])

    def test_exception_clears_slot_without_losing_unsettled_handle(self):
        h=self.make();original=h.runner.settle_completed;handle=h.runner.handles()[0]
        def fail(complete):raise RuntimeError('inert failure')
        h.runner.settle_completed=fail
        with self.assertRaises(RuntimeError):h.poll()
        self.assertEqual(h.runner.handles(),(handle,))
        h.runner.settle_completed=original
        self.assertTrue(h.poll().all_settled)
        self.assertFalse(h.advance_owned(lambda:None).busy)

    def test_inventory_tail_after_retirement_keeps_slot_and_wakes_drain(self):
        h=self.make();entered=Event();release=Event();original=h.runner.handles
        def handles():
            result=original()
            if not result:
                entered.set()
                if not release.wait(3):raise RuntimeError('test inventory timeout')
            return result
        h.runner.handles=handles
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(h.poll)
            try:
                self.assertTrue(entered.wait(1))
                self.assertTrue(h.poll().busy)
                self.assertFalse(h.drain(0).workers_drained)
            finally:release.set()
            self.assertTrue(first.result(1).all_settled)
        self.assertTrue(h.drain(0).all_settled)

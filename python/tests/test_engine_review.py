"""Independent serial engine authority and wake retention regressions."""
import unittest
import test_engine as fixture
from test_chat_runtime import text
from artex.db.workers import WorkerStore


class EngineReview(unittest.TestCase):
    setUp=fixture.EngineTests.setUp
    make=fixture.EngineTests.make

    def test_foreign_worker_executor_rejected_even_with_matching_ids(self):
        self.worker.store=WorkerStore(fixture.DB(),5,9,lifetime_guard=self.guard)
        with self.assertRaises(ValueError):self.make()

    def test_notification_during_wake_prepare_is_retained_for_next_cycle(self):
        original=self.planner.prepare
        delivered=False
        def prepare(*args,**kwargs):
            nonlocal delivered
            wake=original(*args,**kwargs)
            if not delivered:
                delivered=True
                self.assertTrue(self.handle.notify('during-prepare','hint',detail='new direction'))
            return wake
        self.planner.prepare=prepare
        self.pp.responses=[text('first empty plan'),text('second observes hint')]
        self.assertEqual(self.handle.advance(0).action,'planner')
        self.assertEqual(self.handle.advance(1).action,'planner')
        self.assertIn('new direction',self.pp.calls[-1]['messages'][0]['content'][0]['text'])
        self.assertFalse(self.handle.notify('during-prepare','hint',detail='new direction'))

    def test_cancel_before_worker_internal_registration_prevents_claim_or_provider(self):
        fixture.EngineTests.seed(self)
        original=self.worker.run
        def run(*args,**kwargs):
            self.handle.cancel()
            return original(*args,**kwargs)
        self.worker.run=run
        with self.assertRaises(fixture.EngineUnresolved):self.handle.advance(0)
        self.assertTrue(self.handle.wait(0))
        self.assertEqual(self.wp.calls,[])
        self.assertEqual(self.db.rows[-1][4],'open')
        self.assertFalse(self.handle.settle_completed(lambda *args:True))

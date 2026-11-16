"""Independent final-action cancellation and postcommit uncertainty regressions.

Uses actual observer/manager/journal/runner/settlement with inert SQL and provider.
Synthetic positive clock pins are fixture-only, not application admission.
"""
from contextlib import contextmanager
from dataclasses import replace
import unittest
from artex.agent.owned_finalization import OwnedFinalization,FinalizationUnavailable,FinalizationUncertain
import test_owned_finalization as fixture


class CancellationReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def assert_no_replay(self):
        calls=len(self.f.calls);sql=len(self.f.sql)
        with self.assertRaises(FinalizationUnavailable):self.f.controller.run_once()
        self.assertEqual(len(self.f.calls),calls);self.assertEqual(len(self.f.sql),sql)
        self.assertEqual(self.f.manager.deadline_owners()[0].state,'quarantined')
    def test_cancel_during_provider_retains_canonical_cancelled_and_no_settle(self):
        f=self.f;accepted=[]
        f.hook=lambda:accepted.append(f.controller.cancel())
        with self.assertRaises(FinalizationUncertain):f.controller.run_once()
        self.assertEqual(accepted,[True]);self.assertEqual(len(f.calls),1)
        self.assertFalse(f.handle.token.cancelled)
        self.assertIsNotNone(f.controller.outcome)
        self.assertEqual(f.controller.outcome.receipt.phase,'finalized')
        self.assertEqual(f.controller.outcome.outcome.result.reason,'cancelled')
        self.assertEqual(f.status,'running');self.assertIsNone(f.saved)
        self.assertFalse(any(q.sql.startswith('UPDATE tasks SET status=') for q in f.sql))
        self.assert_no_replay()
    def test_cancel_after_runner_construction_before_install_never_infers(self):
        from unittest.mock import patch
        from artex.agent.final_assessment import FinalAssessmentRunner
        f=self.f;accepted=[];runner_states=[]
        def construct(*args,**kwargs):
            runner=FinalAssessmentRunner(*args,**kwargs)
            runner_states.append(runner._active)
            self.assertIsNone(f.controller.runner)
            accepted.append(f.controller.cancel())
            return runner
        with patch('artex.agent.owned_finalization.FinalAssessmentRunner',side_effect=construct):
            with self.assertRaises(FinalizationUncertain):f.controller.run_once()
        self.assertEqual(accepted,[True]);self.assertEqual(runner_states,[False])
        self.assertEqual(f.calls,[]);self.assertFalse(f.handle.token.cancelled)
        self.assertEqual(f.status,'running');self.assertIsNone(f.saved)
        self.assert_no_replay()
    def test_cancel_in_provider_resolution_prevents_transport(self):
        f=self.f;original=f.plan.provider_resolver;accepted=[]
        def resolve(*args):
            binding=original(*args)
            accepted.append(f.controller.cancel())
            return binding
        f.plan=replace(f.plan,provider_resolver=resolve)
        f.controller=OwnedFinalization(f.f.observer,f.plan)
        with self.assertRaises(FinalizationUncertain):f.controller.run_once()
        self.assertEqual(accepted,[True]);self.assertEqual(f.calls,[])
        self.assertFalse(f.handle.token.cancelled);self.assertIsNone(f.saved)
        self.assert_no_replay()
    def test_postcommit_exit_failure_retains_committed_effect_and_no_retry(self):
        f=self.f;original=f.db.transaction;failed=[]
        @contextmanager
        def transaction():
            before=f.saved
            with original() as tx:yield tx
            if before is None and f.saved is not None and not failed:
                failed.append(True)
                raise RuntimeError('private postcommit uncertainty')
        f.db.transaction=transaction
        with self.assertRaises(FinalizationUncertain) as error:f.controller.run_once()
        self.assertEqual(failed,[True]);self.assertNotIn('private',str(error.exception))
        self.assertEqual(f.status,'done');self.assertIsNotNone(f.saved)
        self.assertIsNone(f.controller.result);self.assertEqual(len(f.calls),1)
        self.assertFalse(f.handle.token.cancelled)
        self.assert_no_replay()
    def test_cancel_after_commit_before_retirement_retains_uncertainty(self):
        f=self.f;original=f.db.transaction;accepted=[]
        @contextmanager
        def transaction():
            before=f.saved
            with original() as tx:yield tx
            if before is None and f.saved is not None and not accepted:
                accepted.append(f.controller.cancel())
        f.db.transaction=transaction
        with self.assertRaises(FinalizationUncertain):f.controller.run_once()
        self.assertEqual(accepted,[True]);self.assertEqual(f.status,'done');self.assertIsNotNone(f.saved)
        self.assertIsNone(f.controller.result);self.assertEqual(len(f.calls),1)
        self.assertFalse(f.handle.token.cancelled)
        self.assert_no_replay()
    def test_cancel_from_other_thread_while_final_provider_blocked(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        f=self.f;entered=Event();release=Event()
        def block():
            entered.set()
            if not release.wait(3):raise RuntimeError('fixture provider release timeout')
        f.hook=block
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(f.controller.run_once)
            try:
                self.assertTrue(entered.wait(2))
                self.assertTrue(f.controller.cancel())
                self.assertFalse(future.done())
                self.assertFalse(f.handle.token.cancelled)
                self.assertIsNone(f.saved)
            finally:release.set()
            with self.assertRaises(FinalizationUncertain):future.result(timeout=3)
        self.assertEqual(len(f.calls),1)
        self.assertEqual(f.controller.outcome.outcome.result.reason,'cancelled')
        self.assertEqual(f.status,'running')
        self.assert_no_replay()

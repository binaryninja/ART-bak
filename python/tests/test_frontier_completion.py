"""Actual idle prepared owner, synthetic positive pins; no timed admission."""
import unittest
from threading import Event,Thread
from artex.agent.frontier_completion import *
from artex.agent.manual_action_owner import ManualActionOwner,ManualActionUnavailable
from artex.db.early_completion import EarlyCompletionGrant
import test_unstamped_completion as unstamped
import test_early_completion as stamped

class Tests(unittest.TestCase):
    def setUp(self):self.setup_branch(False)
    def setup_branch(self,has_clock):
        f=stamped.Tests() if has_clock else unstamped.Tests();f.setUp();self.addCleanup(f.doCleanups)
        self.f=f;self.host=f.f.f.f.f.host;self.handle=f.d.handle;self.expired=False
        self.owner=ManualActionOwner(self.host)
        old=f.d.executor.fetch_all
        def fetch(q):
            if q.sql.startswith('SELECT timeout_seconds,first_run_at,deadline_at,\nCASE'):
                if has_clock:
                    c=f.d.lease.clock;return [(10,c.first_run_at,c.deadline_at,self.expired)]
                return [(10,*f.clocks,None)]
            return old(q)
        f.d.executor.fetch_all=fetch
        self.policy=FrontierCompletionPolicy(1,f.permit,lambda *a:f.permit)
        self.component=OwnedFrontierCompletion(self.host,self.handle,f.d,self.policy,self.owner)
        self.owner.register_frontier_completion(self.component)
    def test_unstamped_empty_then_exact_existing_runner_removal(self):
        result=self.component.run_once();self.assertEqual(result.phase,'awaiting_runner_removal')
        self.assertEqual(result.receipt.clock_state,'unstamped');self.assertIsNone(self.f.d.lease)
        snap=self.owner.snapshot();self.assertEqual((snap.active,snap.pending_removal,snap.unresolved),(0,1,0));self.assertFalse(snap.drained)
        self.owner.stop_new_actions();self.assertFalse(self.component.confirm_runner_removal())
        # Existing runner public pass sees cancelled clean handle; component never
        # invokes that pass itself or changes inventory.
        self.host.runner.settle_completed(self.host.manager.complete)
        self.assertTrue(self.component.confirm_runner_removal());self.assertTrue(self.owner.wait(0).drained)
        with self.assertRaises(Exception):self.component.run_once()
    def test_stamped_source_paused_abandoned_done(self):
        self.setup_branch(True);self.f.nodes=[(1,'goal','abandoned'),(2,'intent','paused')]
        result=self.component.run_once();self.assertEqual(result.receipt.reason,'frontier_drained')
        self.assertEqual(self.f.nodes,[(1,'goal','abandoned'),(2,'intent','paused')])
    def test_waiting_is_repeatable_and_does_not_allocate(self):
        self.f.nodes=[(1,'goal','open')]
        for _ in range(2):self.assertEqual(self.component.run_once().phase,'waiting')
        self.assertFalse(self.component._used);self.assertIsNone(self.component._store)
        self.assertEqual(self.host.manager.deadline_owners(),());self.assertTrue(self.owner.snapshot().drained)
        self.f.nodes=[];self.assertEqual(self.component.run_once().phase,'awaiting_runner_removal')
    def test_busy_and_expired_are_normal_nonallocating(self):
        old=self.handle.wait;self.handle.wait=lambda timeout:False
        self.assertEqual(self.component.run_once().phase,'busy');self.assertIsNone(self.component._store)
        self.handle.wait=old
        self.setup_branch(True);self.expired=True
        self.assertEqual(self.component.run_once().phase,'expiry_required');self.assertIsNone(self.component._store)
        self.assertEqual(self.host.manager.deadline_owners()[0].state,'attached')
    def test_seal_denies_direct_call_and_foreign_tracker(self):
        self.owner.stop_new_actions()
        with self.assertRaises(ManualActionUnavailable):self.component.run_once()
        self.assertIsNone(self.component._store)
    def test_cancel_before_start_no_write(self):
        self.component.cancel()
        with self.assertRaises(Exception):self.component.run_once()
        self.assertEqual(self.f.status,'running');self.assertIsNone(self.f.saved)
    def test_insert_failure_retains_sticky_uncertainty(self):
        self.f.fail_insert=True
        with self.assertRaises(FrontierUncertain):self.component.run_once()
        self.assertEqual(self.owner.snapshot().unresolved,1)
        with self.assertRaises(FrontierUnavailable):self.component.run_once()
        self.owner.stop_new_actions();self.assertFalse(self.owner.wait(0).drained)
    def test_post_retirement_cancellation_tail_still_active(self):
        entered=Event();release=Event();errors=[];original=self.handle.token.cancel
        def cancel(reason):
            entered.set()
            if not release.wait(2):raise RuntimeError('fixture timeout')
            original(reason)
        self.handle.token.cancel=cancel
        def run():
            try:self.component.run_once()
            except BaseException as exc:errors.append(exc)
        t=Thread(target=run);t.start()
        try:
            self.assertTrue(entered.wait(1));self.assertEqual(self.host.manager.deadline_owners(),())
            self.assertEqual(self.owner.snapshot().active,1)
            self.owner.stop_new_actions();self.assertFalse(self.owner.wait(0).drained)
        finally:release.set();t.join(2)
        self.assertFalse(t.is_alive());self.assertFalse(errors)
        self.assertEqual(self.owner.snapshot().pending_removal,1)

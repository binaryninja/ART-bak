"""Independent exact-owner frontier tracking; only synthetic prepared fixtures."""
import unittest
from contextlib import contextmanager
from artex.agent.frontier_completion import FrontierUnavailable,FrontierUncertain
from artex.agent.manual_action_owner import ManualActionUnavailable
import test_frontier_completion as fixture

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def test_candidate_sql_revocation_no_terminal_reservation(self):
        f=self.f;old=f.f.d.executor.fetch_all;seen=[]
        def fetch(q):
            result=old(q)
            if q.sql.startswith('SELECT id,kind,CASE'):
                seen.append(q);f.f.permit.revoke()
            return result
        f.f.d.executor.fetch_all=fetch
        with self.assertRaises(Exception):f.component.run_once()
        self.assertEqual(len(seen),1);self.assertIsNone(f.component._store)
        self.assertEqual(f.host.manager.deadline_owners(),())
        self.assertEqual(f.f.status,'running');self.assertIsNone(f.f.saved)
        self.assertTrue(f.owner.snapshot().drained)

    def test_inventory_callback_replacement_preserves_pending_after_seal(self):
        f=self.f;f.component.run_once();f.owner.stop_new_actions()
        original=f.host.runner.handles
        f.host.runner.handles=lambda:()
        with self.assertRaises(FrontierUnavailable):f.component.confirm_runner_removal()
        self.assertEqual(f.owner.snapshot().pending_removal,1)
        self.assertFalse(f.owner.wait(0).drained)
        f.host.runner.handles=original
        self.assertFalse(f.component.confirm_runner_removal())
        f.host.runner.settle_completed(f.host.manager.complete)
        self.assertTrue(f.component.confirm_runner_removal())
        self.assertTrue(f.owner.wait(0).drained)
        with self.assertRaises(ManualActionUnavailable):f.component.confirm_runner_removal()

    def test_commit_exit_uncertainty_never_false_drain_or_removal(self):
        f=self.f;old=f.f.d.executor.transaction;calls=[]
        @contextmanager
        def transaction():
            calls.append(True)
            with old() as tx:yield tx
            if len(calls)==2:raise RuntimeError('synthetic lost commit acknowledgement')
        f.f.d.executor.transaction=transaction
        with self.assertRaises(FrontierUncertain):f.component.run_once()
        self.assertEqual(f.f.status,'done');self.assertIsNone(f.component.result)
        self.assertEqual(f.owner.snapshot().unresolved,1)
        self.assertEqual(f.host.manager.deadline_owners()[0].state,'quarantined')
        f.owner.stop_new_actions();self.assertFalse(f.owner.wait(0).drained)
        with self.assertRaises(ManualActionUnavailable):f.component.confirm_runner_removal()

    def test_seal_and_cancel_during_candidate_track_until_exit(self):
        f=self.f;old=f.f.d.executor.fetch_all;seen=[]
        def fetch(q):
            result=old(q)
            if q.sql.startswith('SELECT timeout_seconds,first_run_at'):
                self.assertEqual(f.owner.snapshot().active,1)
                f.owner.stop_new_actions();f.owner.cancel_active()
                self.assertFalse(f.owner.wait(0).drained);seen.append(True)
            return result
        f.f.d.executor.fetch_all=fetch
        with self.assertRaises(Exception):f.component.run_once()
        self.assertEqual(seen,[True]);self.assertTrue(f.owner.wait(0).drained)
        self.assertFalse(f.component._used);self.assertEqual(f.f.status,'running')

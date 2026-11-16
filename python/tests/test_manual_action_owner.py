"""Actual component lifetime tracking; inert SQL/providers, controlled threads."""
import unittest
from threading import Event
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch
from artex.agent.manual_action_owner import ManualActionOwner,ManualActionUnavailable
from artex.agent.owned_finalization import OwnedFinalization
from artex.db.final_settlement import FinalSettlementStore
import test_owned_finalization as fixture

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.owner=ManualActionOwner(self.f.f.host)
    def register(self):
        self.owner.register_observer(self.f.f.observer);self.owner.register_finalizer(self.f.controller)
    def test_constructor_and_registration_inert_then_seal_direct_methods(self):
        before=list(self.f.sql);self.register();self.assertEqual(self.f.sql,before);self.assertEqual(self.f.calls,[])
        self.assertTrue(self.owner.stop_new_actions())
        with self.assertRaises(ManualActionUnavailable):self.f.controller.run_once()
        with self.assertRaises(ManualActionUnavailable):self.f.f.observer.tick()
        self.assertTrue(self.owner.wait(0).drained)
    def test_untracked_finalizer_cannot_bypass_tracked_observer(self):
        self.owner.register_observer(self.f.f.observer)
        other=OwnedFinalization(self.f.f.observer,self.f.plan)
        with self.assertRaises(ManualActionUnavailable):other.run_once()
        self.assertEqual(self.f.calls,[])
    def test_adoption_rebind_and_capacity_failclosed(self):
        self.f.f.observer.tick()
        with self.assertRaises(ManualActionUnavailable):self.owner.register_observer(self.f.f.observer)
        f=fixture.Tests();f.setUp();self.addCleanup(f.doCleanups)
        owner=ManualActionOwner(f.f.host,max_components=1);owner.register_observer(f.f.observer)
        with self.assertRaises(ManualActionUnavailable):owner.register_finalizer(f.controller)
        with self.assertRaises(ManualActionUnavailable):owner.register_observer(f.f.observer)
        with self.assertRaises(ValueError):ManualActionOwner(f.f.host,max_components=True)
    def test_blocked_finalizer_remains_active_after_seal(self):
        self.register();entered=Event();release=Event()
        def hook():
            entered.set()
            if not release.wait(2):raise RuntimeError('fixture timeout')
        self.f.hook=hook
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(self.f.controller.run_once)
            try:
                self.assertTrue(entered.wait(1));self.owner.stop_new_actions()
                self.assertEqual(self.owner.wait(0).active,1)
                self.assertFalse(self.owner.wait(0).drained)
            finally:release.set()
            self.assertEqual(future.result(timeout=2).phase,'settled')
        self.assertTrue(self.owner.wait(0).drained)
    def test_post_manager_retirement_invocation_still_blocks_close(self):
        self.register();entered=Event();release=Event();original=FinalSettlementStore.settle
        def settle(store):
            receipt=original(store);entered.set()
            if not release.wait(2):raise RuntimeError('fixture timeout')
            return receipt
        with patch.object(FinalSettlementStore,'settle',settle),ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(self.f.controller.run_once)
            try:
                self.assertTrue(entered.wait(1));self.assertEqual(self.f.manager.deadline_owners(),())
                self.owner.stop_new_actions();self.assertFalse(self.owner.wait(0).drained)
                self.assertEqual(self.owner.snapshot().active,1)
            finally:release.set()
            self.assertEqual(future.result(timeout=2).phase,'settled')
        self.assertTrue(self.owner.wait(0).drained)
    def test_cancel_outside_registry_lock_retains_uncertainty(self):
        self.register();entered=Event();release=Event()
        self.f.hook=lambda:(entered.set(),release.wait(2))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(self.f.controller.run_once)
            try:
                self.assertTrue(entered.wait(1));self.owner.stop_new_actions();self.owner.cancel_active()
            finally:release.set()
            with self.assertRaises(Exception):future.result(timeout=2)
        result=self.owner.wait(0);self.assertEqual(result.active,0);self.assertEqual(result.unresolved,1);self.assertFalse(result.drained)
    def test_copied_completion_receipt_cannot_release_active(self):
        self.register();self.f.controller._manual_started=True
        receipt=self.owner._enter(self.f.controller,'run_once')
        with self.assertRaises(ManualActionUnavailable):self.owner._leave(replace(receipt))
        self.assertEqual(self.owner.snapshot().active,1);self.owner._leave(receipt)
        with self.assertRaises(ManualActionUnavailable):self.owner._leave(receipt)

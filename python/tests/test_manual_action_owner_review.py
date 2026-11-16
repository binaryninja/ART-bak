"""Independent tracked-public entry and cancellation lock boundaries."""
import unittest
from threading import Event
from concurrent.futures import ThreadPoolExecutor
from artex.agent.manual_action_owner import ManualActionUnavailable
import test_manual_action_owner as fixture

class ManualOwnerReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups);self.f.register()
    def test_finalizer_read_callback_seals_before_claim_no_model_or_lost_invocation(self):
        f=self.f;original=f.f.controller._validate;once=[]
        def validate():
            original()
            if not once:once.append(True);f.owner.stop_new_actions()
        f.f.controller._validate=validate
        with self.assertRaises(ManualActionUnavailable):f.f.controller.run_once()
        self.assertEqual(f.f.calls,[]);self.assertEqual(f.owner.snapshot().active,0)
        self.assertTrue(f.owner.wait(0).drained)
    def test_cancel_callback_can_read_registry_from_other_thread(self):
        f=self.f;entered=Event();release=Event();seen=[]
        f.f.hook=lambda:(entered.set(),release.wait(3))
        original=f.f.controller.cancel
        def cancel():
            with ThreadPoolExecutor(max_workers=1) as inspection:
                seen.append(inspection.submit(f.owner.snapshot).result(timeout=1).active)
            return original()
        f.f.controller.cancel=cancel
        with ThreadPoolExecutor(max_workers=1) as executor:
            future=executor.submit(f.f.controller.run_once)
            try:
                self.assertTrue(entered.wait(1));f.owner.stop_new_actions();f.owner.cancel_active()
            finally:release.set()
            with self.assertRaises(Exception):future.result(timeout=2)
        self.assertEqual(seen,[1]);self.assertFalse(f.owner.wait(0).drained)
    def test_public_claim_denied_after_seal_without_fabricated_coordinator_state(self):
        f=self.f;f.owner.stop_new_actions()
        with self.assertRaises(ManualActionUnavailable):
            f.f.f.observer.claim_finalization(f.f.controller,f.f.plan)
        self.assertEqual(f.f.calls,[]);self.assertEqual(f.owner.snapshot().active,0)
    def test_registration_is_closed_after_seal(self):
        from artex.agent.owned_finalization import OwnedFinalization
        f=self.f;f.owner.stop_new_actions()
        other=OwnedFinalization(f.f.f.observer,f.f.plan)
        with self.assertRaises(ManualActionUnavailable):f.owner.register_finalizer(other)
        self.assertIsNone(other._manual_owner);self.assertEqual(f.owner.snapshot().active,0)

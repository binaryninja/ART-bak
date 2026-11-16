"""Independent actual-owned intent control review; inert blocked provider."""
import unittest
from artex.agent.intent_control import IntentControlUnavailable
import test_intent_control as fixture

class IntentControlReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_pending_inspection_rechecks_current_control_grant(self):
        f=self.f;f.start()
        self.assertEqual(f.host.control_intent('5',3,'pause',timeout=0).phase,'pending')
        f.allowed=False
        with self.assertRaises(IntentControlUnavailable):f.host.inspect_intent_control('5',3,timeout=0)
        self.assertFalse(f.outer.token.cancelled);self.assertTrue(f.adapter.retained(f.outer))
    def test_repeated_pending_inspection_never_recancels_or_replays_model(self):
        f=self.f;worker=f.engine.worker;original=worker.cancel;cancelled=[]
        def cancel(*args):cancelled.append(args);return original(*args)
        worker.cancel=cancel
        f.start();self.assertEqual(f.host.control_intent('5',3,'pause',timeout=0).phase,'pending')
        for _ in range(2):self.assertEqual(f.host.inspect_intent_control('5',3,timeout=0).phase,'pending')
        self.assertEqual(len(cancelled),1);self.assertEqual(len(f.f.f.f.wp.calls),1)
        self.assertFalse(f.outer.token.cancelled)
    def pause_and_drain(self):
        f=self.f;f.start();f.host.control_intent('5',3,'pause',timeout=0)
        f.release.set();f.thread.join(2);self.assertFalse(f.errors)
        return f
    def test_resume_version_race_retains_unknown_without_model_or_task_cancel(self):
        from artex.agent.intent_control import IntentControlUncertain
        f=self.pause_and_drain();f.host.inspect_intent_control('5',3,timeout=0)
        original=f.db.execute
        def race(q):
            if q.sql.startswith("UPDATE exploration_nodes SET state='open'"):f.db.versions[3]+=1
            return original(q)
        f.db.execute=race
        with self.assertRaises(IntentControlUncertain):f.host.control_intent('5',3,'resume',timeout=0)
        self.assertTrue(f.adapter.retained(f.outer));self.assertTrue(f.outer.unresolved)
        self.assertFalse(f.outer.token.cancelled);self.assertEqual(len(f.f.f.f.wp.calls),1)
    def test_replaced_canonical_outcome_not_confirmed(self):
        from dataclasses import replace
        from artex.agent.intent_control import IntentControlUncertain
        f=self.pause_and_drain();outcome,done=f.engine._control_outcome
        f.engine._control_outcome=(replace(outcome,version=outcome.version+1),done)
        with self.assertRaises(IntentControlUncertain):f.host.inspect_intent_control('5',3,timeout=0)
        self.assertTrue(f.adapter.retained(f.outer));self.assertFalse(f.outer.token.cancelled)
    def test_replaced_coordinator_validation_does_not_authorize_pending_control(self):
        f=self.f;f.start();f.host.control_intent('5',3,'pause',timeout=0)
        f.outer.coordinator._check=lambda *a:None
        with self.assertRaises(IntentControlUnavailable):f.host.inspect_intent_control('5',3,timeout=0)
        self.assertFalse(f.outer.token.cancelled);self.assertTrue(f.adapter.retained(f.outer))
    def test_configuration_callback_revokes_control_before_cancellation(self):
        f=self.f;f.start();original=f.outer.coordinator.current;worker=f.engine.worker;cancel=worker.cancel;seen=[]
        def revoke(*args):f.allowed=False;return original(*args)
        def counted(*args):seen.append(args);return cancel(*args)
        f.outer.coordinator.current=revoke;worker.cancel=counted
        with self.assertRaises(IntentControlUnavailable):f.host.control_intent('5',3,'pause',timeout=0)
        self.assertFalse(seen);self.assertFalse(f.outer.token.cancelled)

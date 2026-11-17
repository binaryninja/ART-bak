"""Permanent admission fencing over real composition with inert SQL/engines."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import unittest
from unittest.mock import patch
from artex.agent.host import PollResult
from artex.agent.engine_runner import EngineRunner
from artex.manager import RuntimeUnavailable
from artex.scheduler import _snapshot
from artex.server.runtime_listener import RuntimeListener, RuntimeResourceOwner
import test_admission as fixtures
import test_runtime_listener as listener_fixtures


class FenceTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.controller=self.f.capacity

    def test_explicit_seal_denies_all_admission_before_callbacks(self):
        before=list(self.f.f.db.calls)
        self.assertTrue(self.controller.stop_admissions())
        self.assertTrue(self.controller.stop_admissions())
        with patch.object(self.f.host,'poll') as poll:
            for operation,task in (('create',None),('start','5'),('resume','5'),('queued','5')):
                with self.subTest(operation=operation),self.assertRaises(RuntimeUnavailable):
                    with self.controller.admission_guard(operation,task):self.fail('sealed guard yielded')
            poll.assert_not_called()
        self.assertEqual(self.f.f.db.calls,before);self.assertEqual(self.f.calls,[])
        self.assertEqual(self.controller.admit_queued(_snapshot(self.f.manager.lifecycle_snapshot('5'))),'deferred')

    def test_poll_reconcile_and_drain_do_not_reopen_admission(self):
        self.assertTrue(self.controller.stop_admissions())
        self.assertFalse(self.controller.poll().busy)
        self.assertFalse(self.controller.reconcile().busy)
        self.assertTrue(self.controller.drain(0).all_settled)
        with self.assertRaises(RuntimeUnavailable):self.f.facade.control_task('5','resume')
        self.assertTrue(self.controller.stop_admissions())

    def test_inflight_admission_retained_until_call_finishes_then_pause_drains(self):
        entered,release=Event(),Event();original=self.f.runner._factory
        def delayed(*args):
            entered.set()
            if not release.wait(3):raise RuntimeError('fixture timeout')
            return original(*args)
        self.f.runner._factory=delayed
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(self.f.facade.control_task,'5','resume')
            try:
                self.assertTrue(entered.wait(2))
                self.assertFalse(self.controller.stop_admissions())
                self.assertFalse(self.controller.stop_admissions())
                with self.assertRaises(RuntimeUnavailable):self.f.facade.start('5')
            finally:release.set()
            future.result(2)
        self.assertEqual(len(self.f.calls),1)
        self.assertTrue(self.controller.stop_admissions())
        self.assertEqual(self.controller.poll().pending,1)
        self.f.facade.control_task('5','pause')
        self.assertTrue(self.controller.drain(1).all_settled)
        with self.assertRaises(RuntimeUnavailable):self.f.facade.control_task('5','resume')
        self.assertEqual(len(self.f.calls),1)

    def test_reentrant_seal_during_inventory_prevents_yield_without_deadlock(self):
        original=self.f.host.poll;observed=[]
        def poll():
            observed.append(self.controller.stop_admissions())
            return original()
        with patch.object(self.f.host,'poll',side_effect=poll):
            with self.assertRaises(RuntimeUnavailable):
                with self.controller.admission_guard('start','5'):self.fail('sealed before yield')
        self.assertEqual(observed,[False]);self.assertTrue(self.controller.stop_admissions())
        self.assertEqual(self.f.calls,[])

    def test_seal_during_yield_does_not_revoke_original_resume_claim(self):
        with self.controller.admission_guard('resume','5'):
            claim=self.controller._continuation_claim('5')
            self.assertFalse(self.controller.stop_admissions())
            self.assertIs(self.controller._continuation_claim('5'),claim)
        self.assertTrue(self.controller.stop_admissions())
        with self.assertRaises(RuntimeUnavailable):
            with self.controller.admission_guard('resume','5'):self.fail('seal was removed')

    def test_failure_and_quarantine_reconciliation_do_not_clear_seal(self):
        original=self.f.host.poll;count=0
        def poll():
            nonlocal count
            count+=1
            if count==2:raise RuntimeError('fixture inventory failure')
            return original()
        with patch.object(self.f.host,'poll',side_effect=poll):
            with self.assertRaisesRegex(ValueError,'fixture operation failure'):
                with self.controller.admission_guard('start','5'):
                    self.assertFalse(self.controller.stop_admissions())
                    raise ValueError('fixture operation failure')
        self.assertTrue(self.controller._quarantined)
        self.assertTrue(self.controller.stop_admissions())
        self.assertFalse(self.controller.reconcile().busy)
        with self.assertRaises(RuntimeUnavailable):self.f.facade.start('5')

    def test_nonadmission_reservation_is_conservatively_unresolved(self):
        original=self.f.host.poll;observed=[]
        def poll():
            observed.append(self.controller.stop_admissions())
            return original()
        with patch.object(self.f.host,'poll',side_effect=poll):
            self.assertFalse(self.controller.reconcile().busy)
        self.assertEqual(observed,[False]);self.assertTrue(self.controller.stop_admissions())
        with self.assertRaises(RuntimeUnavailable):self.f.facade.start('5')

    def test_changed_host_identity_refuses_before_sealing(self):
        with patch.object(self.f.host,'manager',object()):
            with self.assertRaises(RuntimeUnavailable):self.controller.stop_admissions()
        self.assertFalse(self.controller._sealed)
        self.assertTrue(self.controller.stop_admissions())


class HostFenceTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.host=self.f.host

    def test_all_original_host_admission_paths_refuse_before_manager_calls(self):
        self.assertTrue(self.host.stop_admissions())
        self.assertTrue(self.host.stop_admissions())
        calls=(lambda:self.host.create_task('fixture','fixture'),
            lambda:self.host.start('5'),lambda:self.host.start_queued('5',{}),
            lambda:self.host.control_task('5','resume'),
            lambda:self.host.resume_suspended('5'),lambda:self.host.install_continuation(None),
            lambda:self.host.control_intent('5',3,'resume'))
        before=list(self.f.f.db.calls)
        for call in calls:
            with self.subTest(call=call),self.assertRaisesRegex(RuntimeUnavailable,'permanently sealed'):
                call()
        self.assertEqual(self.f.f.db.calls,before);self.assertEqual(self.f.calls,[])
        self.assertEqual(self.host._admissions,0)

    def test_controller_reserved_before_host_seal_cannot_enter_host(self):
        with self.f.capacity.admission_guard('start','5'):
            self.assertTrue(self.host.stop_admissions())
            with self.assertRaisesRegex(RuntimeUnavailable,'permanently sealed'):self.host.start('5')
        self.assertEqual(self.f.calls,[])
        self.assertTrue(self.f.capacity.stop_admissions())

    def test_host_seal_ignores_replaceable_claim_callback(self):
        with patch.object(self.f.runner,'claim_host',side_effect=AssertionError('must not call runner')):
            self.assertTrue(self.host.stop_admissions())
        self.assertTrue(self.f.capacity.stop_admissions())

    def test_controller_invokes_original_host_seal_not_instance_replacement(self):
        with patch.object(self.host,'stop_admissions',return_value=True) as replacement:
            self.assertTrue(self.f.capacity.stop_admissions())
        replacement.assert_not_called()
        with self.assertRaisesRegex(RuntimeUnavailable,'permanently sealed'):self.host.start('5')

    def test_inflight_host_admission_remains_counted_and_reentrant_seal_returns_false(self):
        observed=[]
        def admitted():
            observed.append(self.host.stop_admissions())
            self.assertEqual(self.host._admissions,1)
            with self.assertRaises(RuntimeUnavailable):self.host.start('5')
            return 'already admitted'
        self.assertEqual(self.host._invoke(admitted,admission=True),'already admitted')
        self.assertEqual(observed,[False]);self.assertEqual(self.host._admissions,0)
        self.assertTrue(self.host.stop_admissions())

    def test_inflight_intent_resume_is_counted_but_pause_and_inspect_remain_allowed(self):
        entered,release=Event(),Event();seen=[]
        def control(runner,task,intent,action,*,timeout):
            self.assertTrue(self.host.owns_intent_control(runner,task,intent,action))
            seen.append(action)
            if action=='resume':
                entered.set()
                if not release.wait(3):raise RuntimeError('fixture timeout')
            return action
        with patch.object(EngineRunner,'control_intent',control):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(self.host.control_intent,'5',3,'resume')
                try:
                    self.assertTrue(entered.wait(2));self.assertFalse(self.host.stop_admissions())
                    self.assertEqual(self.host._admissions,1)
                finally:release.set()
                self.assertEqual(future.result(2),'resume')
            self.assertTrue(self.host.stop_admissions())
            with self.assertRaises(RuntimeUnavailable):self.host.control_intent('5',3,'resume')
            self.assertEqual(self.host.control_intent('5',3,'pause'),'pause')
            self.assertEqual(self.host.inspect_intent_control('5',3),'inspect')
        self.assertEqual(seen,['resume','pause','inspect']);self.assertEqual(self.host._admissions,0)

    def test_failed_inflight_intent_resume_releases_count_without_unsealing(self):
        def control(runner,task,intent,action,*,timeout):
            self.assertFalse(self.host.stop_admissions())
            raise ValueError('fixture failure')
        with patch.object(EngineRunner,'control_intent',control):
            with self.assertRaisesRegex(ValueError,'fixture failure'):self.host.control_intent('5',3,'resume')
        self.assertEqual(self.host._admissions,0);self.assertIsNone(self.host._intent_control)
        self.assertTrue(self.host.stop_admissions())
        with self.assertRaises(RuntimeUnavailable):self.host.control_intent('5',3,'resume')

    def test_original_host_binding_required_before_seal(self):
        original=self.host.manager
        self.host.manager=object()
        try:
            with self.assertRaisesRegex(RuntimeUnavailable,'ownership changed'):self.host.stop_admissions()
        finally:self.host.manager=original
        self.assertFalse(self.host._admission_sealed)


class ListenerCompositionTests(unittest.TestCase):
    def setUp(self):
        self.f=listener_fixtures.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.bundle=self.f.bundle
        def cancel():
            for handle in self.bundle.runner.handles():
                self.bundle.host.pause(handle.task_id,timeout=1)
            # pause acknowledges cancellation; listener's following host.drain
            # owns runner retirement, so handles need not already be absent.
            return True
        self.owner=RuntimeResourceOwner(self.f.pool,self.bundle.admission.stop_admissions,
            cancel,self.f.owner.close_resources)
        self.listener=RuntimeListener(self.bundle,self.owner)
        self.addCleanup(self.listener.stop,1)

    def test_real_admission_owner_seals_then_cancels_and_closes(self):
        self.bundle.facade.start('5')
        self.assertEqual(len(self.bundle.runner.handles()),1)
        result=self.listener.stop(1);self.assertTrue(result.stopped,result)
        self.assertEqual(self.bundle.runner.handles(),())
        self.assertEqual(self.f.calls,['resources'])
        with self.assertRaises(RuntimeUnavailable):self.bundle.facade.start('5')
        self.assertEqual(self.f.f.p.calls,[])

    def test_busy_admission_retains_resources_until_explicit_shutdown_retry(self):
        with self.bundle.admission.admission_guard('start','5'):
            result=self.listener.stop(0)
            self.assertEqual(result.reason,'admission_unresolved');self.assertFalse(result.stopped)
            self.assertEqual(self.f.calls,[])
            with self.assertRaises(RuntimeUnavailable):self.bundle.facade.start('5')
        result=self.listener.stop(1)
        self.assertTrue(result.stopped,result);self.assertEqual(self.f.calls,['resources'])
        with self.assertRaises(RuntimeUnavailable):self.bundle.facade.start('5')


if __name__=='__main__':unittest.main()

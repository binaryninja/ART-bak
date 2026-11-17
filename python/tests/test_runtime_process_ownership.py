"""Real local lease + host/listener/session; inert SQL and no listening socket."""
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from artex.agent.host import DrainResult, PollResult
from artex.manager import RuntimeUnavailable
from artex.runtime_lease import RuntimeLease, LeaseUnavailable
from artex.server.runtime_listener import RuntimeListener, RuntimeResourceOwner, ListenerStop
from artex.server.runtime_session import RuntimeSession
import test_runtime_listener as fixture


class Tests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.Tests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / 'runtime.lock')
        self.lease = RuntimeLease(self.path); self.lease.acquire()
        self.addCleanup(self.lease.release)
        self.host = self.f.bundle.host
        self.listener = self.session = None

    def assemble(self, close=None):
        owner = self.f.owner
        if close is not None:
            owner = RuntimeResourceOwner(owner.pool, owner.stop_admissions, owner.cancel_owned, close)
        self.listener = RuntimeListener(self.f.bundle, owner)
        self.session = RuntimeSession(self.listener, self.lease)
        self.addCleanup(self.session.stop, 1)

    def process_can_lock(self):
        code = '''import fcntl,sys
with open(sys.argv[1],'r+') as stream:
 try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:sys.exit(3)
'''
        result = subprocess.run([sys.executable, '-c', code, self.path], capture_output=True, timeout=5)
        self.assertIn(result.returncode, (0, 3), result.stderr)
        return result.returncode == 0

    def test_actual_blocked_owned_callback_retains_process_lock_until_return(self):
        self.assemble()
        entered, release = threading.Event(), threading.Event()
        errors = []
        def callback():
            entered.set()
            if not release.wait(3):raise AssertionError('test callback timed out')
        def work():
            try:self.host.advance_owned(callback)
            except BaseException as error:errors.append(type(error).__name__)
        thread = threading.Thread(target=work); thread.start()
        try:
            self.assertTrue(entered.wait(2))
            stopped = self.session.stop(0)
            self.assertFalse(stopped.stopped)
            self.assertTrue(self.lease.validate())
            self.assertFalse(self.process_can_lock())
            self.assertNotIn('resources', self.f.calls)
            with self.assertRaises(LeaseUnavailable):self.lease.release(owner=self.session)
            with self.assertRaises(LeaseUnavailable):self.lease.transfer(self.session, object())
        finally:
            release.set(); thread.join(3)
        self.assertFalse(thread.is_alive()); self.assertFalse(errors)
        self.assertTrue(self.session.stop(1).stopped)
        self.assertTrue(self.process_can_lock())

    def test_callback_returning_baseexception_still_needs_new_actual_drain(self):
        self.assemble()
        with self.assertRaises(KeyboardInterrupt):
            self.host.advance_owned(lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
        self.assertTrue(self.lease.validate()); self.assertFalse(self.process_can_lock())
        self.assertTrue(self.session.stop(1).stopped)

    def test_structural_stop_and_drain_values_never_release_the_lease(self):
        self.assemble()
        with patch.object(self.listener, 'stop', return_value=ListenerStop(True, 'forged')):
            self.assertFalse(self.session.stop(0).stopped)
        with patch.object(self.host, 'drain', return_value=DrainResult(True, PollResult(False))):
            self.assertFalse(self.session.stop(0).stopped)
        self.assertTrue(self.lease.validate()); self.assertFalse(self.process_can_lock())
        self.assertTrue(self.session.stop(1).stopped)

    def test_new_call_after_successful_drain_invalidates_release_even_if_it_returns(self):
        self.assemble()
        original = self.host.drain
        def raced(timeout):
            result = original(timeout)
            self.assertTrue(result.all_settled)
            self.host.advance_owned(lambda: None)
            return result
        with patch.object(self.host, 'drain', raced):
            self.assertFalse(self.session.stop(0).stopped)
        self.assertTrue(self.lease.validate()); self.assertFalse(self.process_can_lock())
        self.assertTrue(self.session.stop(1).stopped)

    def test_resource_cleanup_cannot_restart_host_or_unlock_early(self):
        attempted = []
        def close():
            calls = [lambda: self.host.advance_owned(lambda: attempted.append('work')),
                     lambda: self.host._invoke(lambda: attempted.append('mutation'), admission=False),
                     self.host.poll, lambda: self.host.drain(0),
                     lambda: self.f.bundle.facade.update_metadata('5', name='not written')]
            for operation in calls:
                with self.assertRaises(RuntimeUnavailable):operation()
            with self.assertRaises(LeaseUnavailable):self.lease.release(owner=self.session)
            self.assertFalse(self.process_can_lock())
            return True
        self.assemble(close)
        self.assertTrue(self.session.stop(1).stopped)
        self.assertFalse(attempted); self.assertTrue(self.process_can_lock())
        self.assertTrue(self.listener.closed_lifetime())

    def test_close_failure_keeps_permanent_host_fence_and_retries_only_resources(self):
        calls = []
        def close():
            calls.append(True)
            return len(calls) > 1
        self.assemble(close)
        self.assertFalse(self.session.stop(0).stopped)
        self.assertTrue(self.lease.validate()); self.assertFalse(self.process_can_lock())
        with self.assertRaises(RuntimeUnavailable):self.host.advance_owned(lambda: None)
        self.assertEqual(self.f.calls, ['admissions', 'cancel'])
        self.assertTrue(self.session.stop(1).stopped)
        self.assertEqual(self.f.calls, ['admissions', 'cancel'])
        self.assertEqual(len(calls), 2); self.assertTrue(self.process_can_lock())

    def test_close_receipt_is_exact_sealed_and_single_lifetime(self):
        self.host.stop_admissions()
        result = self.host.drain(0)
        self.assertTrue(result.all_settled)
        self.assertFalse(self.host.close_lifetime(replace(result)))
        self.assertTrue(self.host.close_lifetime(result))
        self.assertTrue(self.host.lifetime_closed(result))
        for operation in (self.host.poll, lambda: self.host.pause('5'),
                          lambda: self.host.advance_owned(lambda: self.fail('closed callback')),
                          lambda: self.host.control_intent('5', 3, 'inspect')):
            with self.assertRaises(RuntimeUnavailable):operation()
        self.assertTrue(self.host.close_lifetime(result))
        self.assertTrue(self.lease.release())

    def test_closed_host_rejects_before_replaceable_claim_callback(self):
        self.host.stop_admissions(); proof = self.host.drain(0)
        self.assertTrue(self.host.close_lifetime(proof))
        with patch.object(self.host.runner, 'claim_host', side_effect=AssertionError('closed callback')) as callback:
            for call in (self.host.poll, lambda: self.host.pause('5'),
                         lambda: self.host.advance_owned(lambda: None), lambda: self.host.drain(0)):
                with self.assertRaises(RuntimeUnavailable):call()
            callback.assert_not_called()
        with patch.object(self.host, 'manager', object()):
            self.assertFalse(self.host.lifetime_closed(proof))
        self.lease.release()

    def test_admission_seal_preserves_authorized_pause_and_denied_pause_authority(self):
        import test_task_continuation as continuation
        f = continuation.Tests(); f.setUp(); self.addCleanup(f.doCleanups)
        f.app.host.install_continuation(f.owner)
        self.assertTrue(f.app.host.stop_admissions())
        f.allowed = False; before = len(f.db.calls)
        with self.assertRaises(Exception):f.app.host.pause('5', timeout=0)
        self.assertFalse(f.handle.token.cancelled)
        self.assertEqual(len(f.db.calls), before)
        f.allowed = True
        self.assertTrue(f.app.host.pause('5', timeout=0)['paused'])
        self.assertTrue(f.handle.token.cancelled)
        # Paused continuation ownership is not a clean lifetime drain.
        drain = f.app.host.drain(0)
        self.assertFalse(drain.all_settled)
        self.assertFalse(f.app.host.close_lifetime(drain))
        self.assertTrue(f.owner.retained(f.handle))
        self.lease.release()

    def test_unsealed_or_later_poll_receipt_cannot_close_lifetime(self):
        result = self.host.drain(0)
        self.assertFalse(self.host.close_lifetime(result))
        self.host.stop_admissions()
        self.host.poll()
        self.assertFalse(self.host.close_lifetime(result))
        current = self.host.drain(0)
        self.assertTrue(self.host.close_lifetime(current))
        self.lease.release()

    def test_concurrent_close_and_owned_entry_have_one_linearized_winner(self):
        # If entry wins, even a completed callback invalidates the drain. If
        # closure wins, callback admission fails before it can run.
        for _ in range(25):
            f = fixture.Tests(); f.setUp()
            try:
                host = f.bundle.host; host.stop_admissions(); proof = host.drain(0)
                barrier = threading.Barrier(2); calls = []; outcome = []
                def work():
                    barrier.wait(2)
                    try:host.advance_owned(lambda: calls.append(True))
                    except RuntimeUnavailable:outcome.append('refused')
                thread = threading.Thread(target=work); thread.start()
                barrier.wait(2); closed = host.close_lifetime(proof)
                thread.join(2); self.assertFalse(thread.is_alive())
                self.assertEqual(bool(calls), not closed)
                self.assertEqual(outcome, ['refused'] if closed else [])
            finally:f.doCleanups()
        self.lease.release()


if __name__ == '__main__':
    unittest.main()

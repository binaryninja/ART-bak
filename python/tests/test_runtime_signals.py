"""Explicit signal ownership and inert real-process foreground acceptance."""
import http.client
import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, Mock

from artex.runtime_lease import RuntimeLease, LeaseUnavailable
from artex.server.auth import sign_jwt
from artex.server.core import json_response
from artex.server.runtime_listener import RuntimeListener, RuntimeResourceOwner, DevListenerConfig
from artex.server.runtime_session import RuntimeSession, SessionResult
from artex.server.runtime_signals import RuntimeSignalScope, SignalScopeUnavailable
from artex.agent.host import DrainResult, PollResult
import test_runtime_listener as fixtures
import test_runtime_session as session_fixtures


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.scope = RuntimeSignalScope()
        self.original = {n: signal.getsignal(n) for n in (signal.SIGINT, signal.SIGTERM)}
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for number, handler in self.original.items():
            signal.signal(number, handler)
        self.scope.restore()

    def test_inert_install_coalescing_and_restore(self):
        self.assertEqual(self.original, {n: signal.getsignal(n) for n in self.original})
        self.scope.install()
        self.scope.validate()
        # The handler must be safe even while this thread owns a nonreentrant
        # Event condition. Direct invocation deterministically exercises that.
        event = threading.Event()
        with event._cond:
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
        self.assertEqual(self.scope.signal_number, signal.SIGTERM)
        self.assertFalse(event.is_set())
        self.assertTrue(self.scope.restore())
        self.assertTrue(self.scope.restore())
        self.assertEqual(self.original, {n: signal.getsignal(n) for n in self.original})
        with self.assertRaises(SignalScopeUnavailable): self.scope.install()

    def test_nonmain_and_nested_ownership_reject_without_changes(self):
        errors = []
        def worker():
            try: self.scope.install()
            except Exception as error: errors.append(error)
        thread = threading.Thread(target=worker); thread.start(); thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertIsInstance(errors[0], SignalScopeUnavailable)
        self.assertEqual(self.original, {n: signal.getsignal(n) for n in self.original})
        self.scope.install()
        with self.assertRaises(SignalScopeUnavailable): RuntimeSignalScope().install()
        self.scope.validate()

    def test_partial_install_failure_restores_both_attempted_slots(self):
        original_signal = signal.signal
        def fail(number, handler):
            result = original_signal(number, handler)
            if number == signal.SIGTERM and handler is self.scope._handler:
                raise OSError('private installation detail')
            return result
        with patch('artex.server.runtime_signals.signal.signal', side_effect=fail):
            with self.assertRaisesRegex(SignalScopeUnavailable, 'Signal installation failed') as caught:
                self.scope.install()
        self.assertNotIn('private', str(caught.exception))
        self.assertEqual(self.original, {n: signal.getsignal(n) for n in self.original})
        self.assertTrue(self.scope.restore())

    def test_unknown_original_refuses_before_install(self):
        with patch('artex.server.runtime_signals.signal.getsignal', return_value=None), patch('artex.server.runtime_signals.signal.signal') as setter:
            with self.assertRaises(SignalScopeUnavailable): self.scope.install()
        setter.assert_not_called()

    def test_replaced_handler_not_overwritten_and_scope_retained(self):
        self.scope.install()
        replacement = lambda *args: None
        signal.signal(signal.SIGTERM, replacement)
        with self.assertRaises(SignalScopeUnavailable): self.scope.validate()
        self.assertFalse(self.scope.restore())
        self.assertIs(signal.getsignal(signal.SIGTERM), replacement)
        with self.assertRaises(SignalScopeUnavailable): RuntimeSignalScope().install()
        signal.signal(signal.SIGTERM, self.original[signal.SIGTERM])
        self.assertTrue(self.scope.restore())

    def test_restore_failure_is_retained_and_explicitly_retryable(self):
        self.scope.install()
        with patch('artex.server.runtime_signals.signal.signal', side_effect=OSError('private')):
            self.assertFalse(self.scope.restore())
        with self.assertRaises(SignalScopeUnavailable): RuntimeSignalScope().install()
        self.assertTrue(self.scope.restore())

    def test_pid_change_refuses_ownership_and_restore(self):
        self.scope.install()
        with patch('artex.server.runtime_signals.os.getpid', return_value=-1):
            with self.assertRaises(SignalScopeUnavailable): self.scope.validate()
            with self.assertRaises(SignalScopeUnavailable): self.scope.restore()


class SessionSignalTests(unittest.TestCase):
    def setUp(self):
        self.fixture = session_fixtures.SessionTests()
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.session, self.lease = self.fixture.session, self.fixture.lease
        self.event = threading.Event()
        self.scope = RuntimeSignalScope(); self.addCleanup(self.scope.restore)

    def test_invalid_arguments_do_not_install(self):
        for event, interval, timeout in ((None, .1, 10), (self.event, 0, 10), (self.event, .1, -1)):
            with patch.object(self.scope, 'install') as install:
                with self.assertRaises(ValueError):
                    self.session.run_until_signalled(self.scope, event, poll_interval=interval, shutdown_timeout=timeout)
            install.assert_not_called()
        with self.assertRaises(ValueError): self.session.run_until_signalled(None, self.event)
        self.assertEqual(self.fixture.fixture.calls, [])

    def test_pre_requested_stop_restores_without_start(self):
        self.event.set()
        with patch.object(self.session, 'start') as start:
            result = self.session.run_until_signalled(self.scope, self.event)
        start.assert_not_called()
        self.assertEqual(result.foreground.reason, 'stop_requested')
        self.assertTrue(result.foreground.shutdown.stopped)
        self.assertTrue(result.handlers_restored)
        self.assertIsNone(result.signal_number)

    def test_signal_before_start_does_not_start_and_restores(self):
        original_install = self.scope.install
        def install():
            original_install(); self.scope._handler(signal.SIGTERM, None)
        with patch.object(self.scope, 'install', side_effect=install), patch.object(self.session, 'start') as start:
            result = self.session.run_until_signalled(self.scope, self.event)
        start.assert_not_called()
        self.assertEqual(result.foreground.reason, 'signal_received')
        self.assertEqual(result.signal_number, signal.SIGTERM)
        self.assertTrue(result.foreground.shutdown.stopped)
        self.assertTrue(result.handlers_restored)

    def test_startup_failure_keeps_original_cleanup_result(self):
        with patch.object(self.session, 'start', return_value=SessionResult(False, False, 'startup_failed_unresolved')), patch.object(self.session, 'stop') as stop:
            result = self.session.run_until_signalled(self.scope, self.event)
        stop.assert_not_called()
        self.assertFalse(result.foreground.shutdown.stopped)
        self.assertTrue(result.handlers_restored)
        self.assertTrue(self.lease.validate())

    def test_signal_shutdown_unresolved_retains_lease_no_automatic_retry(self):
        self.event.set()
        # Exercise the original listener stop path; replaced stop methods are
        # now refused before invocation and cannot serve as drain evidence.
        with patch.object(self.session.listener._bundle.host, 'drain', side_effect=RuntimeError('inert unresolved drain')) as drain:
            result = self.session.run_until_signalled(self.scope, self.event)
        drain.assert_called_once()
        self.assertFalse(result.foreground.shutdown.stopped)
        self.assertTrue(result.handlers_restored)
        self.assertTrue(self.lease.validate())

    def test_restore_failure_is_separate_from_verified_runtime_shutdown(self):
        self.event.set()
        with patch.object(self.scope, 'restore', return_value=False):
            result = self.session.run_until_signalled(self.scope, self.event)
        self.assertFalse(result.handlers_restored)
        self.assertTrue(result.foreground.shutdown.stopped)
        with self.assertRaises(LeaseUnavailable): self.lease.validate()

    def test_foreground_exception_restores_handlers(self):
        original = {n: signal.getsignal(n) for n in (signal.SIGINT, signal.SIGTERM)}
        with patch.object(self.session, '_run_foreground', side_effect=RuntimeError('test exception')):
            with self.assertRaisesRegex(RuntimeError, 'test exception'):
                self.session.run_until_signalled(self.scope, self.event)
        self.assertEqual(original, {n: signal.getsignal(n) for n in original})
        self.assertTrue(self.lease.validate())

    def test_plain_driver_preserves_wait_notification_after_event_clear(self):
        # Event.wait may return True for its notification even when a different
        # host thread clears the flag before this thread next calls is_set().
        # The existing non-signal driver must not discard that accepted stop.
        with patch.object(self.session, 'start', return_value=SessionResult(True, False, 'running')), \
             patch.object(self.session.listener, '_thread', Mock(is_alive=lambda: True)), \
             patch.object(self.session.listener._bundle.pump, '_thread', Mock(is_alive=lambda: True)), \
             patch.object(self.event, 'wait', side_effect=[True, AssertionError('Lost stop notification')]) as wait, \
             patch.object(self.session, 'stop', return_value=SessionResult(False, False, 'stopped_unresolved')) as stop:
            result = self.session.run_until_stopped(self.event)
        self.assertFalse(self.event.is_set())
        self.assertEqual(result.reason, 'stop_requested')
        wait.assert_called_once(); stop.assert_called_once()


def child_runtime(mode):
    """Real listener/pump/lease with fabricated stores and no provider calls."""
    fixture = fixtures.Tests(); fixture.setUp()
    with tempfile.TemporaryDirectory() as directory:
        lease = RuntimeLease(str(Path(directory) / 'runtime.lock')); lease.acquire()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        def close_resources():
            print('DRAINING', flush=True)
            time.sleep(.15)
            fixture.calls.append('resources')
            return True
        owner = RuntimeResourceOwner(fixture.pool, fixture.owner.stop_admissions,
                                     fixture.owner.cancel_owned, close_resources)
        fixture.bundle.bootstrap.app.router.add('GET', '/probe', lambda req, p: json_response(200, {'ok': True}))
        listener = RuntimeListener(fixture.bundle, owner, DevListenerConfig(port=port))
        session = RuntimeSession(listener, lease)
        original = {n: signal.getsignal(n) for n in (signal.SIGINT, signal.SIGTERM)}
        scope = RuntimeSignalScope()
        def ready():
            deadline = time.monotonic() + 5
            while not session.result.started and time.monotonic() < deadline:
                time.sleep(.005)
            if session.result.started: print('READY ' + str(port), flush=True)
        observer = threading.Thread(target=ready); observer.start()
        if mode == 'unresolved':
            with patch.object(fixture.bundle.host, 'drain', return_value=DrainResult(False, PollResult(True))):
                result = session.run_until_signalled(scope, threading.Event(), poll_interval=.01, shutdown_timeout=1)
            lease_held = lease.validate()
            contender = RuntimeLease(lease.path)
            try:
                contender.acquire(); contender.release(); raise AssertionError('Unresolved lease escaped')
            except LeaseUnavailable: pass
            assert fixture.calls == ['admissions', 'cancel']
            assert session.stop(1).stopped
        else:
            result = session.run_until_signalled(scope, threading.Event(), poll_interval=.01, shutdown_timeout=1)
            lease_held = False
        observer.join(1)
        assert not listener._thread.is_alive() and not fixture.bundle.pump._thread.is_alive()
        assert original == {n: signal.getsignal(n) for n in original}
        assert fixture.f.p.calls == []
        contender = RuntimeLease(lease.path); assert contender.acquire(); contender.release()
        print(json.dumps({'reason': result.foreground.reason, 'stopped': result.foreground.shutdown.stopped,
                          'signal': result.signal_number, 'restored': result.handlers_restored,
                          'lease_held': lease_held, 'calls': fixture.calls}), flush=True)
    fixture.doCleanups()


class ProcessSignalTests(unittest.TestCase):
    def run_child(self, first_signal, *, mode='clean', repeat=False):
        root = Path(__file__).resolve().parents[1]
        environment = {'PATH': os.environ.get('PATH', ''), 'PYTHONPATH': str(root) + os.pathsep + str(root / 'tests')}
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--signal-child', mode],
                                   cwd=root, env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertTrue(select.select([process.stdout], [], [], 8)[0], 'Child startup timed out')
            line = process.stdout.readline().strip()
            self.assertTrue(line.startswith('READY '), line)
            connection = http.client.HTTPConnection('127.0.0.1', int(line.split()[1]), timeout=2)
            try:
                connection.request('GET', '/probe', headers={'Authorization': 'Bearer ' + sign_jwt(b'x' * 32)})
                response = connection.getresponse()
                self.assertEqual(response.status, 200); self.assertEqual(json.loads(response.read()), {'ok': True})
            finally: connection.close()
            process.send_signal(first_signal)
            if repeat:
                self.assertTrue(select.select([process.stdout], [], [], 8)[0], 'Drain did not begin')
                self.assertEqual(process.stdout.readline().strip(), 'DRAINING')
                process.send_signal(signal.SIGINT)
                process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=8)
            self.assertEqual(process.returncode, 0, stderr)
            result = json.loads(stdout.splitlines()[-1])
            self.assertEqual(result['signal'], first_signal)
            self.assertEqual(result['reason'], 'signal_received')
            self.assertTrue(result['restored'])
            return result
        finally:
            if process.poll() is None: process.kill()
            process.communicate(timeout=5)

    def test_actual_sigterm_drains_once_despite_repeated_signals(self):
        result = self.run_child(signal.SIGTERM, repeat=True)
        self.assertTrue(result['stopped']); self.assertFalse(result['lease_held'])
        self.assertEqual(result['calls'], ['admissions', 'cancel', 'resources'])

    def test_actual_sigint_uses_clean_drain(self):
        self.assertTrue(self.run_child(signal.SIGINT)['stopped'])

    def test_actual_sigterm_retains_unresolved_lease_until_explicit_retry(self):
        result = self.run_child(signal.SIGTERM, mode='unresolved')
        self.assertFalse(result['stopped']); self.assertTrue(result['lease_held'])
        self.assertEqual(result['calls'], ['admissions', 'cancel', 'admissions', 'cancel', 'resources'])


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--signal-child':
        child_runtime(sys.argv[2])
    else:
        unittest.main()

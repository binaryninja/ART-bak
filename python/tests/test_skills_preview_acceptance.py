"""Dependency-free contracts for the explicit synthetic preview smoke runner."""
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / 'integration' / 'skills_preview_acceptance.py'
SPEC = importlib.util.spec_from_file_location('skills_preview_acceptance', SOURCE)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class Tests(unittest.TestCase):
    def test_ports_are_explicit_distinct_and_unprivileged(self):
        args = runner.parse_args(['--frontend-port', '3000', '--backend-port', '8787'])
        self.assertEqual((args.frontend_port, args.backend_port), (3000, 8787))
        for values in [[], ['3000'], ['--frontend-port', '3000', '--backend-port', '3000'],
                       ['--frontend-port', '80', '--backend-port', '8787'],
                       ['--frontend-port', '3000', '--backend-port', '65536']]:
            with self.subTest(values=values), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    runner.parse_args(values)

    def test_environment_does_not_forward_credentials_or_profile_overrides(self):
        with patch.dict(os.environ, {'API_KEY': 'secret', 'PGHOST': 'shared',
                                     'PYTHONPATH': '/private', 'NODE_OPTIONS': '--inspect',
                                     'HTTPS_PROXY': 'https://proxy', 'NEXT_EXPORT': '1',
                                     'NEXT_PUBLIC_MOCK': '1'}, clear=True):
            env = runner.child_environment(Path('/fixture'))
        self.assertEqual(env['PYTHONPATH'], str(runner.REPO / 'python'))
        self.assertEqual(env['HOME'], '/fixture')
        self.assertEqual(env['PYTHONNOUSERSITE'], '1')
        for key in ('API_KEY', 'PGHOST', 'NODE_OPTIONS', 'HTTPS_PROXY', 'NEXT_EXPORT', 'NEXT_PUBLIC_MOCK'):
            self.assertNotIn(key, env)

    def test_port_preflight_closes_all_descriptors_on_collision(self):
        first, second = Mock(), Mock()
        second.bind.side_effect = OSError('occupied')
        with patch.object(runner.socket, 'socket', side_effect=[first, second]):
            with self.assertRaises(OSError):
                runner.reserve_ports(3000, 8787)
        first.bind.assert_called_once_with(('127.0.0.1', 3000))
        first.setsockopt.assert_called_once_with(runner.socket.SOL_SOCKET, runner.socket.SO_REUSEADDR, 1)
        first.close.assert_called_once()
        second.close.assert_called_once()

    def test_readiness_cannot_accept_a_child_that_already_exited(self):
        child = Mock()
        child.poll.return_value = 0
        with patch.object(runner.http.client, 'HTTPConnection') as connection:
            with self.assertRaises(RuntimeError):
                runner.wait_ready(child, 3000, '/api/auth/status', 1)
            connection.assert_not_called()

    def test_natural_stop_requires_live_child_success_and_no_force(self):
        child = Mock()
        child.poll.return_value = None
        child.wait.return_value = 0
        runner.stop_naturally(child, 3)
        child.send_signal.assert_called_once_with(signal.SIGTERM)
        child.wait.assert_called_once_with(timeout=3)
        child.wait.side_effect = subprocess.TimeoutExpired('child', 3)
        with self.assertRaises(subprocess.TimeoutExpired):
            runner.stop_naturally(child, 3)
        child.kill.assert_not_called()
        child.poll.return_value = 0
        with self.assertRaises(RuntimeError):
            runner.stop_naturally(child, 3)

    def test_failed_fixture_containment_is_not_natural_exit(self):
        live, exited = Mock(pid=23456), Mock(pid=34567)
        live.poll.return_value, exited.poll.return_value = None, 0
        with patch.object(runner.os, 'killpg') as kill:
            self.assertEqual(runner.contain([live, exited]),
                             {'forced_fixture_cleanup': True, 'cleanup_errors': []})
            kill.assert_called_once_with(23456, signal.SIGKILL)
        self.assertEqual(runner.contain([exited]),
                         {'forced_fixture_cleanup': False, 'cleanup_errors': []})

    def test_containment_reaps_a_child_that_exits_during_signal(self):
        child = Mock(pid=23456)
        child.poll.return_value = None
        with patch.object(runner.os, 'killpg', side_effect=ProcessLookupError):
            self.assertEqual(runner.contain([child]),
                             {'forced_fixture_cleanup': True, 'cleanup_errors': []})
        child.wait.assert_called_once_with(timeout=5)

    def test_containment_attempts_all_children_after_signal_and_wait_errors(self):
        children = [Mock(pid=12340 + n) for n in range(3)]
        for child in children:
            child.poll.return_value = None
        children[2].wait.side_effect = subprocess.TimeoutExpired('private argv', 5)

        def kill(pid, _signal):
            if pid == children[1].pid:
                raise PermissionError('private details')

        with patch.object(runner.os, 'killpg', side_effect=kill) as signal_group:
            result = runner.contain(children)
        self.assertEqual([call.args[0] for call in signal_group.call_args_list], [12342, 12341, 12340])
        for child in children:
            child.wait.assert_called_once_with(timeout=5)
        self.assertEqual(result, {'forced_fixture_cleanup': True, 'cleanup_errors': [
            {'stage': 'wait', 'error_type': 'TimeoutExpired', 'index': 2},
            {'stage': 'signal', 'error_type': 'PermissionError', 'index': 1},
        ]})
        self.assertNotIn('private', json.dumps(result))

    def test_poll_error_still_attempts_that_owned_child(self):
        child = Mock(pid=12340)
        child.poll.side_effect = RuntimeError('private details')
        with patch.object(runner.os, 'killpg') as kill:
            result = runner.contain([child])
        kill.assert_called_once_with(12340, signal.SIGKILL)
        child.wait.assert_called_once_with(timeout=5)
        self.assertEqual(result['cleanup_errors'],
                         [{'stage': 'poll', 'error_type': 'RuntimeError', 'index': 0}])

    def test_phase_finalization_closes_all_logs_and_records_cleanup_failures(self):
        children = [Mock(pid=12340 + n) for n in range(2)]
        for child in children:
            child.poll.return_value = None
        children[1].wait.side_effect = subprocess.TimeoutExpired('private', 5)
        logs = [Mock(), Mock()]
        logs[0].close.side_effect = OSError('private details')
        with tempfile.TemporaryDirectory() as folder, patch.object(runner.os, 'killpg') as kill:
            result = runner.finalize_phase(Path(folder), children, logs, True)
            stored = json.loads((Path(folder) / 'result.json').read_text())
        self.assertEqual(kill.call_count, 2)
        self.assertEqual(stored, result)
        self.assertFalse(stored['passed'])
        self.assertTrue(stored['forced_fixture_cleanup'])
        self.assertEqual([row['stage'] for row in stored['cleanup_errors']], ['wait', 'log_close'])
        for log in logs:
            log.close.assert_called_once()

    def test_unexpected_containment_exception_still_finalizes_failed_phase(self):
        logs = [Mock(), Mock()]
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(runner, 'contain', side_effect=RuntimeError('private details')):
            result = runner.finalize_phase(Path(folder), [], logs, True)
            self.assertEqual(json.loads((Path(folder) / 'result.json').read_text()), result)
        self.assertFalse(result['passed'])
        self.assertEqual(result['cleanup_errors'],
                         [{'stage': 'containment', 'error_type': 'RuntimeError', 'index': None}])
        for log in logs:
            log.close.assert_called_once()

    def test_unavailable_evidence_storage_is_failure_after_log_closure(self):
        logs = [Mock(), Mock()]
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(Path, 'write_text', side_effect=OSError('no storage')):
            with self.assertRaises(OSError):
                runner.finalize_phase(Path(folder), [], logs, True)
        for log in logs:
            log.close.assert_called_once()

    def test_cancellation_is_latched_and_handlers_are_restored(self):
        previous = {signal.SIGINT: object(), signal.SIGTERM: object()}
        handlers = previous.copy()

        def install(signum, handler):
            old, handlers[signum] = handlers[signum], handler
            return old

        with patch.object(runner.signal, 'signal', side_effect=install):
            with runner.cancellation_scope():
                with self.assertRaises(runner.RunCancelled):
                    handlers[signal.SIGTERM](signal.SIGTERM, None)
                # Repeated cancellation cannot interrupt the pending cleanup.
                handlers[signal.SIGTERM](signal.SIGTERM, None)
                handlers[signal.SIGINT](signal.SIGINT, None)
        self.assertEqual(handlers, previous)

    def test_cli_cancellation_preserves_failed_summary_and_nonzero_result(self):
        handlers = {signal.SIGINT: object(), signal.SIGTERM: object()}

        def install(signum, handler):
            old, handlers[signum] = handlers[signum], handler
            return old

        def cancelled_phase(*_args):
            handlers[signal.SIGTERM](signal.SIGTERM, None)

        with tempfile.TemporaryDirectory() as folder, \
                patch.object(runner.tempfile, 'mkdtemp', return_value=folder), \
                patch.dict(sys.modules, {'bcrypt': Mock()}), \
                patch.object(runner.signal, 'signal', side_effect=install), \
                patch.object(runner, 'run_phase', side_effect=cancelled_phase), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = runner.cli(['--frontend-port', '3000', '--backend-port', '8787'])
            stored = json.loads((Path(folder) / 'result.json').read_text())
        self.assertEqual(code, 1)
        self.assertFalse(stored['passed'])
        self.assertEqual(stored['phases'], [])

    def test_cancelled_phase_unwinds_to_all_registered_children_and_result(self):
        args = runner.parse_args(['--frontend-port', '3000', '--backend-port', '8787'])
        cancellation = runner.Cancellation()
        children = [Mock(pid=12340 + n) for n in range(3)]
        for child in children:
            child.poll.return_value = None

        def wait_client(*, timeout):
            if timeout == 90:
                cancellation.cancel(signal.SIGTERM, None)
            return 0

        children[2].wait.side_effect = wait_client
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(runner, 'reserve_ports'), \
                patch.object(runner, 'wait_ready'), \
                patch.object(runner.subprocess, 'Popen', side_effect=children), \
                patch.object(runner.os, 'killpg') as kill:
            with self.assertRaises(runner.RunCancelled):
                runner.run_phase(args, Path(folder), {}, False, cancellation)
            stored = json.loads((Path(folder) / 'read-only' / 'result.json').read_text())
        self.assertEqual([call.args[0] for call in kill.call_args_list], [12342, 12341, 12340])
        self.assertFalse(stored['passed'])
        self.assertTrue(stored['forced_fixture_cleanup'])
        self.assertTrue(stored['cancelled'])
        self.assertEqual(stored['cleanup_errors'], [])

    def test_first_signal_during_finalization_does_not_skip_children_or_logs(self):
        cancellation = runner.Cancellation()
        children = [Mock(pid=12340 + n) for n in range(2)]
        for child in children:
            child.poll.return_value = None
        logs = [Mock(), Mock()]
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(runner.os, 'killpg',
                             side_effect=lambda *_: cancellation.cancel(signal.SIGTERM, None)) as kill:
            result = runner.finalize_phase(Path(folder), children, logs, True, cancellation)
            stored = json.loads((Path(folder) / 'result.json').read_text())
        self.assertEqual(kill.call_count, 2)
        for child in children:
            child.wait.assert_called_once_with(timeout=5)
        for log in logs:
            log.close.assert_called_once()
        self.assertEqual(result, stored)
        self.assertFalse(stored['passed'])
        self.assertTrue(stored['cancelled'])
        self.assertEqual(stored['cleanup_errors'], [])

    def test_first_signal_inside_phase_result_write_records_failed_cancellation(self):
        cancellation = runner.Cancellation()
        original_write = Path.write_text
        writes = []

        def write(path, value, **kwargs):
            writes.append(json.loads(value))
            cancellation.cancel(signal.SIGTERM, None)
            return original_write(path, value, **kwargs)

        with tempfile.TemporaryDirectory() as folder, patch.object(Path, 'write_text', new=write):
            result = runner.finalize_phase(Path(folder), [], [], True, cancellation)
            stored = json.loads((Path(folder) / 'result.json').read_text())
        self.assertEqual(len(writes), 2)
        self.assertTrue(writes[0]['passed'])
        self.assertEqual(result, stored)
        self.assertFalse(stored['passed'])
        self.assertTrue(stored['cancelled'])

    def test_first_sigterm_inside_top_level_summary_finishes_evidence_and_fails_cli(self):
        for phase_error in (None, RuntimeError('inert phase failure')):
            with self.subTest(phase_error=phase_error):
                previous = {signal.SIGINT: object(), signal.SIGTERM: object()}
                handlers = previous.copy()
                original_write = Path.write_text
                writes = []

                def install(signum, handler):
                    old, handlers[signum] = handlers[signum], handler
                    return old

                def write(path, value, **kwargs):
                    writes.append(json.loads(value))
                    handlers[signal.SIGTERM](signal.SIGTERM, None)
                    return original_write(path, value, **kwargs)

                with tempfile.TemporaryDirectory() as folder, \
                        patch.object(runner.tempfile, 'mkdtemp', return_value=folder), \
                        patch.dict(sys.modules, {'bcrypt': Mock()}), \
                        patch.object(runner.signal, 'signal', side_effect=install), \
                        patch.object(runner, 'run_phase', side_effect=phase_error), \
                        patch.object(Path, 'write_text', new=write), \
                        redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()):
                    code = runner.cli(['--frontend-port', '3000', '--backend-port', '8787'])
                    stored = json.loads((Path(folder) / 'result.json').read_text())
                self.assertEqual(code, 1)
                self.assertEqual(len(writes), 2)
                self.assertTrue(stored['cancelled'])
                self.assertFalse(stored['passed'])
                self.assertNotIn('PASS Skills preview smoke', stdout.getvalue())
                self.assertEqual(handlers, previous)

    def test_read_only_filesystem_oracle_rejects_unexpected_effects(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'skills'
            original = runner.seed(root)
            (root / '.artex-skills.lock').touch()
            runner.verify_files(root, False, original)
            (root / 'sample' / 'unexpected').touch()
            with self.assertRaises(RuntimeError):
                runner.verify_files(root, False, original)

    def test_write_enabled_oracle_requires_exact_unicode_content(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'skills'
            original = runner.seed(root)
            (root / '.artex-skills.lock').touch()
            target = root / '資料'
            (target / '参考').mkdir(parents=True)
            (target / 'SKILL.md').write_text('inert', encoding='utf8')
            (target / '参考' / '例.txt').write_text('Edited 世界\n', encoding='utf8')
            runner.verify_files(root, True, original)
            (target / '参考' / '例.txt').write_text('wrong', encoding='utf8')
            with self.assertRaises(RuntimeError):
                runner.verify_files(root, True, original)

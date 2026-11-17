"""Hosted fixture contracts only. No Docker daemon/database is contacted."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('artex_postgres_ci', Path(__file__).parents[1] / 'ci/postgres_acceptance.py')
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


class Tests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='artex-ci-pg-', dir='/tmp'))
        self.addCleanup(lambda: shutil.rmtree(self.root) if self.root.exists() else None)
        self.fixture = ci.Fixture(self.root, 'a' * 32, os.getuid(), os.getgid())
        self.fixture.prepare()

    def config(self):
        f = self.fixture
        return {'Config': {'User': f'{f.uid}:{f.gid}', 'Image': ci.IMAGE, 'Labels': {ci.OWNER_LABEL: f.owner}},
                'HostConfig': {'NetworkMode': 'none', 'ReadonlyRootfs': True, 'Privileged': False,
                               'PortBindings': {}, 'PublishAllPorts': False, 'CapDrop': ['ALL'],
                               'SecurityOpt': ['no-new-privileges:true']},
                'Mounts': [{'Type': 'bind', 'Source': str(f.root / 'data'), 'Destination': '/var/lib/postgresql/data'},
                           {'Type': 'bind', 'Source': str(f.socket), 'Destination': '/run/artex-ci'}]}

    def test_clean_environment_excludes_application_database_tokens_and_docker_defaults(self):
        with patch.dict(os.environ, {'PGHOST': 'remote', 'PGSERVICE': 'private', 'ARTEX_PG_DSN': 'private',
                                     'OPENAI_API_KEY': 'private', 'DOCKER_HOST': 'tcp://remote:2375',
                                     'GITHUB_TOKEN': 'private', 'HTTP_PROXY': 'http://remote'}):
            environment = ci.clean_environment(self.root / 'home', self.root / 'tmp')
        self.assertEqual(set(environment), {'PATH', 'HOME', 'TMPDIR', 'PYTHONPATH', 'PYTHONNOUSERSITE', 'PYTHONUNBUFFERED'})
        self.assertNotIn('private', str(environment))

    def test_existing_disposable_guard_accepts_socket_under_selected_tmpdir(self):
        program = ('from argparse import Namespace; import sys; '
                   'from integration.local_postgres_acceptance import validate_arguments; '
                   "validate_arguments(Namespace(host=sys.argv[1],port=5432,user='qa',database='artex_test_ci_baseline',confirm_disposable='artex_test_ci_baseline'))")
        result = subprocess.run([sys.executable, '-c', program, str(self.fixture.socket)],
                                env=self.fixture.environment, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        sibling = self.root / 'socket'; sibling.mkdir(mode=0o700)
        refused = subprocess.run([sys.executable, '-c', program, str(sibling)],
                                 env=self.fixture.environment, capture_output=True, timeout=5)
        self.assertNotEqual(refused.returncode, 0)

    def test_root_and_socket_are_private_no_preexisting_data_allowed(self):
        self.assertEqual(self.fixture.socket.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.root / 'empty.pgpass').stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ci.FixtureError):
            self.fixture.prepare()

    def test_create_has_no_network_port_privilege_or_hostwide_mount(self):
        args = self.fixture.create_arguments()
        self.assertEqual(args[args.index('--network') + 1], 'none')
        self.assertEqual(args[args.index('--user') + 1], f'{os.getuid()}:{os.getgid()}')
        self.assertIn('--read-only', args)
        self.assertIn('no-new-privileges:true', args)
        self.assertEqual(args[args.index('--cap-drop') + 1], 'ALL')
        self.assertFalse(set(args) & {'--privileged', '--publish', '-p', '--volume', '--pid', '--device'})
        self.assertNotIn('/var/run/docker.sock', ' '.join(args))
        mounts = [args[n + 1] for n, word in enumerate(args) if word == '--mount']
        self.assertEqual(mounts, [f'type=bind,src={self.root / "data"},dst=/var/lib/postgresql/data',
                                  f'type=bind,src={self.fixture.socket},dst=/run/artex-ci'])
        self.assertIn('listen_addresses=', args)
        self.assertIn('POSTGRES_HOST_AUTH_METHOD=reject', args)
        self.assertRegex(ci.IMAGE, r'^postgres@sha256:[0-9a-f]{64}$')

    def test_docker_client_uses_local_daemon_private_empty_config_and_no_shell(self):
        result = SimpleNamespace(returncode=0, stdout=' observed\n')
        with patch.object(ci.subprocess, 'run', return_value=result) as run:
            self.assertEqual(self.fixture.docker('ps'), 'observed')
        args, kwargs = run.call_args
        self.assertEqual(args[0][:5], ['/usr/bin/docker', '--host', 'unix:///var/run/docker.sock', '--config', str(self.root / 'docker-config')])
        self.assertNotIn('shell', kwargs)
        self.assertEqual(kwargs['env'], self.fixture.environment)
        self.assertEqual(kwargs['timeout'], 20)

    def test_docker_failure_and_timeout_are_sanitized(self):
        for value in [SimpleNamespace(returncode=1, stdout='private', stderr='private'),
                      subprocess.TimeoutExpired('private', 1)]:
            with self.subTest(value=type(value)), patch.object(ci.subprocess, 'run') as run:
                if isinstance(value, Exception): run.side_effect = value
                else: run.return_value = value
                with self.assertRaises(ci.FixtureError) as error: self.fixture.docker('ps')
                self.assertNotIn('private', str(error.exception))

    def test_inspection_checks_actual_container_security_selectors(self):
        self.fixture.docker = Mock(return_value=json.dumps(self.config()))
        self.fixture.verify_container()
        for section, key, value in [('HostConfig', 'NetworkMode', 'bridge'), ('HostConfig', 'Privileged', True),
                                    ('HostConfig', 'ReadonlyRootfs', False), ('HostConfig', 'CapDrop', []),
                                    ('HostConfig', 'SecurityOpt', []), ('HostConfig', 'PortBindings', {'5432/tcp': []}),
                                    ('Config', 'User', '0:0'), ('Config', 'Image', 'postgres:latest')]:
            altered = self.config(); altered[section][key] = value
            self.fixture.docker.return_value = json.dumps(altered)
            with self.subTest(key=key), self.assertRaises(ci.FixtureError): self.fixture.verify_container()
        altered = self.config(); altered['Mounts'][1]['Source'] = '/'
        self.fixture.docker.return_value = json.dumps(altered)
        with self.assertRaises(ci.FixtureError): self.fixture.verify_container()

    def test_start_checks_pinned_image_then_final_postgres_health(self):
        self.fixture.docker = Mock(side_effect=['', ci.IMAGE_ID, 'b' * 64, json.dumps(self.config()), '',
                                                 json.dumps({'Running': True, 'Health': {'Status': 'healthy'}})])
        self.fixture.start()
        self.assertTrue(self.fixture.create_attempted)
        self.assertEqual(self.fixture.docker.call_args_list[0].args, ('pull', '--platform', 'linux/amd64', ci.IMAGE))
        self.assertEqual(self.fixture.docker.call_args_list[0].kwargs, {'timeout': 180})

    def test_wrong_image_identity_refuses_before_create(self):
        self.fixture.docker = Mock(side_effect=['', 'wrong'])
        with self.assertRaises(ci.FixtureError): self.fixture.start()
        self.assertFalse(self.fixture.create_attempted)

    def test_lost_create_response_still_requires_owned_cleanup(self):
        self.fixture.docker = Mock(side_effect=['', ci.IMAGE_ID, ci.FixtureError('uncertain')])
        with self.assertRaises(ci.FixtureError): self.fixture.start()
        self.assertTrue(self.fixture.create_attempted)

    def test_unhealthy_or_exited_startup_fails(self):
        for state in ({'Running': False}, {'Running': True, 'Health': {'Status': 'unhealthy'}}):
            self.fixture.docker = Mock(side_effect=['', ci.IMAGE_ID, 'b' * 64, json.dumps(self.config()), '', json.dumps(state)])
            with self.subTest(state=state), self.assertRaises(ci.FixtureError): self.fixture.start()

    def test_startup_wait_is_bounded(self):
        self.fixture.docker = Mock(side_effect=['', ci.IMAGE_ID, 'b' * 64, json.dumps(self.config()), '', json.dumps({'Running': True, 'Health': {'Status': 'starting'}})])
        with patch.object(ci.time, 'monotonic', side_effect=[0, 1, 100]), patch.object(ci.time, 'sleep'):
            with self.assertRaises(ci.FixtureError): self.fixture.start()

    def connection(self, row):
        connection = Mock(); connection.execute.return_value.fetchone.return_value = row
        context = Mock(); context.__enter__ = Mock(return_value=connection); context.__exit__ = Mock(return_value=False)
        return Mock(return_value=context), connection

    def test_database_identity_transport_and_version_before_acceptance(self):
        good = (ci.DATABASE, 'qa', None, '170012', '')
        connect, connection = self.connection(good)
        self.fixture.verify_database(connect)
        self.assertEqual(connect.call_args.kwargs['host'], str(self.fixture.socket))
        self.assertEqual(connect.call_args.kwargs['passfile'], str(self.root / 'empty.pgpass'))
        self.assertEqual(connect.call_args.kwargs['password'], '')
        for row in [('production', *good[1:]), (good[0], 'other', *good[2:]),
                    (good[0], good[1], '127.0.0.1', *good[3:]), (*good[:3], '170011', ''), (*good[:4], '*')]:
            connect, _ = self.connection(row)
            with self.subTest(row=row), self.assertRaises(ci.FixtureError): self.fixture.verify_database(connect)

    def test_acceptance_is_one_fixed_script_and_exact_explicit_disposable_target(self):
        process = Mock(); process.wait.return_value = 0
        with patch.object(ci.subprocess, 'Popen', return_value=process) as popen:
            self.fixture.run_baseline()
        args, kwargs = popen.call_args
        self.assertEqual(args[0], [sys.executable, str(ci.ENTRYPOINT), '--host', str(self.fixture.socket),
                                  '--port', '5432', '--user', 'qa', '--database', ci.DATABASE,
                                  '--confirm-disposable', ci.DATABASE])
        self.assertTrue(kwargs['start_new_session'])
        self.assertEqual(kwargs['env'], self.fixture.environment)
        process.wait.assert_called_once_with(timeout=180)

    def test_acceptance_failure_and_timeout_terminate_owned_child(self):
        for result in [1, subprocess.TimeoutExpired('fixture', 180)]:
            process = Mock()
            if isinstance(result, Exception): process.wait.side_effect = result
            else: process.wait.return_value = result
            with patch.object(ci.subprocess, 'Popen', return_value=process), patch.object(self.fixture, 'terminate_child') as terminate:
                with self.assertRaises((ci.FixtureError, subprocess.TimeoutExpired)): self.fixture.run_baseline()
                terminate.assert_called_once_with(process)

    def test_interrupted_child_group_gets_bounded_term_then_kill(self):
        process = Mock(pid=456); process.wait.return_value = 0
        with patch.object(ci.os, 'killpg') as kill:
            self.fixture.terminate_child(process)
        self.assertEqual([c.args for c in kill.call_args_list], [(456, signal.SIGTERM), (456, signal.SIGKILL)])
        self.assertEqual([c.kwargs for c in process.wait.call_args_list], [{'timeout': 10}, {'timeout': 5}])

    def test_cleanup_only_uses_generated_owner_label_and_exact_returned_id(self):
        self.fixture.create_attempted = True
        self.fixture.docker = Mock(side_effect=['c' * 64, '', ''])
        self.fixture.cleanup()
        self.assertFalse(self.root.exists())
        query = self.fixture.docker.call_args_list[0].args
        self.assertEqual(query, ('ps', '-aq', '--no-trunc', '--filter', 'label=' + ci.OWNER_LABEL + '=' + 'a' * 32))
        self.assertEqual(self.fixture.docker.call_args_list[1].args, ('rm', '--force', '--volumes', 'c' * 64))

    def test_cleanup_proves_absence_before_deleting_data(self):
        for response in ['invalid', 'c' * 64 + '\n' + 'd' * 64]:
            self.fixture.create_attempted = True; self.fixture.docker = Mock(return_value=response)
            with self.assertRaises(ci.FixtureError): self.fixture.cleanup()
            self.assertTrue(self.root.exists())
            self.assertEqual(self.fixture.docker.call_count, 1)
        self.fixture.docker = Mock(side_effect=ci.FixtureError('daemon unavailable'))
        with self.assertRaises(ci.FixtureError): self.fixture.cleanup()
        self.assertTrue(self.root.exists())

    def test_cleanup_after_failed_pull_never_contacts_or_deletes_other_containers(self):
        self.fixture.docker = Mock()
        self.fixture.cleanup()
        self.fixture.docker.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_preflight_refuses_unconfirmed_nonhosted_root_ambient_or_missing_docker(self):
        good = {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted'}
        with patch.dict(os.environ, good, clear=True), patch.object(ci, 'DOCKER', str(ci.ENTRYPOINT)):
            ci.preflight(True)
            with self.assertRaises(ci.FixtureError): ci.preflight(False)
            with patch.dict(os.environ, {'RUNNER_ENVIRONMENT': 'self-hosted'}), self.assertRaises(ci.FixtureError): ci.preflight(True)
            with patch.dict(os.environ, {'PGHOST': ''}), self.assertRaises(ci.FixtureError): ci.preflight(True)
            with patch.object(ci.os, 'getuid', return_value=0), self.assertRaises(ci.FixtureError): ci.preflight(True)
            with patch.object(ci, 'DOCKER', str(self.root / 'absent')), self.assertRaises(ci.FixtureError): ci.preflight(True)

    def test_main_preflight_failure_has_no_temp_docker_or_driver_effect(self):
        with patch.object(ci, 'preflight', side_effect=ci.FixtureError('refused')), patch.object(ci.tempfile, 'mkdtemp') as temp, patch.object(ci, 'Fixture') as fixture, patch('sys.stderr', new=io.StringIO()):
            self.assertEqual(ci.main(['--github-hosted-disposable']), 1)
        temp.assert_not_called(); fixture.assert_not_called()

    def test_main_cleanup_failure_cannot_report_pass(self):
        fake = Mock(); fake.cleanup.side_effect = ci.FixtureError('uncertain')
        with patch.object(ci, 'preflight'), patch.object(ci.tempfile, 'mkdtemp', return_value=str(self.root)), patch.object(ci, 'Fixture', return_value=fake), patch.dict(sys.modules, {'psycopg': SimpleNamespace(connect=Mock())}), patch.object(ci.signal, 'signal', return_value=signal.SIG_DFL) as signals, patch('sys.stderr', new=io.StringIO()), patch('sys.stdout', new=io.StringIO()) as out:
            self.assertEqual(ci.main(['--github-hosted-disposable']), 1)
            self.assertNotIn('PASS:', out.getvalue())
        fake.cleanup.assert_called_once(); self.assertEqual(signals.call_args.args, (signal.SIGTERM, signal.SIG_DFL))

    def test_main_always_cleans_after_start_failure(self):
        fake = Mock(); fake.start.side_effect = ci.FixtureError('startup')
        with patch.object(ci, 'preflight'), patch.object(ci.tempfile, 'mkdtemp', return_value=str(self.root)), patch.object(ci, 'Fixture', return_value=fake), patch.dict(sys.modules, {'psycopg': SimpleNamespace(connect=Mock())}), patch.object(ci.signal, 'signal', return_value=signal.SIG_DFL), patch('sys.stderr', new=io.StringIO()):
            self.assertEqual(ci.main(['--github-hosted-disposable']), 1)
        fake.cleanup.assert_called_once(); fake.run_baseline.assert_not_called()


if __name__ == '__main__':
    unittest.main()

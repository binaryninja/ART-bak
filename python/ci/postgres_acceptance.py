#!/usr/bin/env python3
"""GitHub-hosted, disposable baseline PG fixture. Never targets an existing DB.

Only the database container has networking disabled. Host-side test code is not
sandboxed against the hosted runner or its network. See POSTGRES_CI_STATUS.md.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time

# Official postgres:17.12-bookworm linux/amd64 manifest, not a mutable tag.
IMAGE = 'postgres@sha256:99d1a998c76487d3abb48b141505bda5dcf8089c0c4cd7be40f75de314fa32f0'
IMAGE_ID = 'sha256:431b070f8300b219ab7e6597e7ebb158df31767739896f53a936cfc749d8437a'
DATABASE = 'artex_test_ci_baseline'
OWNER_LABEL = 'org.artex.disposable-ci-owner'
DOCKER = '/usr/bin/docker'
REPOSITORY = Path(__file__).resolve().parents[2]
ENTRYPOINT = REPOSITORY / 'python/integration/local_postgres_acceptance.py'


class FixtureError(RuntimeError):
    """Sanitized fixture-stage failure; never print connection/config objects."""


def preflight(confirmed: bool) -> None:
    if not confirmed or not __debug__:
        raise FixtureError('Explicit disposable-hosted confirmation and assertions required')
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise FixtureError('This entry point is only for an explicitly selected GitHub-hosted job')
    if os.name != 'posix' or platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'amd64'):
        raise FixtureError('Linux x86_64 required')
    if os.getuid() == 0 or os.getgid() == 0:
        raise FixtureError('An unprivileged runner user and group are required')
    if any(name.startswith('PG') for name in os.environ):
        raise FixtureError('Ambient PG connection settings are forbidden')
    if ENTRYPOINT.is_symlink() or not ENTRYPOINT.is_file() or ENTRYPOINT.resolve() != ENTRYPOINT:
        raise FixtureError('The exact checked-out baseline acceptance entry point is required')
    if not Path(DOCKER).is_file():
        raise FixtureError('The hosted runner local Docker client is required')


def clean_environment(home: Path, temp: Path) -> dict[str, str]:
    return {
        'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
        'HOME': str(home), 'TMPDIR': str(temp),
        'PYTHONPATH': str(REPOSITORY / 'python'),
        'PYTHONNOUSERSITE': '1', 'PYTHONUNBUFFERED': '1',
    }


class Fixture:
    def __init__(self, root: Path, owner: str, uid: int, gid: int):
        self.root, self.owner, self.uid, self.gid = root, owner, uid, gid
        self.socket = root / 'tmp' / 'socket'
        self.name = 'artex-ci-pg-' + owner
        self.create_attempted = False
        self.environment = clean_environment(root / 'home', root / 'tmp')

    def prepare(self) -> None:
        info = self.root.lstat()
        if self.root.is_symlink() or self.root.parent != Path('/tmp') or not self.root.name.startswith('artex-ci-pg-'):
            raise FixtureError('Dedicated temporary fixture root required')
        if info.st_uid != self.uid or info.st_mode & 0o777 != 0o700:
            raise FixtureError('Fixture root must be owned by the runner with mode0700')
        if not re.fullmatch(r'[a-f0-9]{32}', self.owner) or self.uid <= 0 or self.gid <= 0:
            raise FixtureError('Invalid private fixture ownership')
        if list(self.root.iterdir()):
            raise FixtureError('Fixture root must be fresh and empty')
        for part in ('data', 'home', 'tmp', 'docker-config'):
            (self.root / part).mkdir(mode=0o700)
        self.socket.mkdir(mode=0o700)
        (self.root / 'empty.pgpass').touch(mode=0o600)

    def docker(self, *arguments: str, timeout: float = 20) -> str:
        # A private empty config and explicit local socket ignore login/context/DOCKER_HOST defaults.
        command = [DOCKER, '--host', 'unix:///var/run/docker.sock', '--config', str(self.root / 'docker-config'), *arguments]
        try:
            result = subprocess.run(command, env=self.environment, cwd=self.root,
                                    capture_output=True, text=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise FixtureError('Local Docker operation unavailable or timed out') from error
        if result.returncode:
            raise FixtureError('Local Docker operation failed')
        return result.stdout.strip()

    def create_arguments(self) -> list[str]:
        # The image's bootstrap psql needs its default Unix socket as well as our private shared one.
        sockets = '/var/run/postgresql,/run/artex-ci'
        health = 'test "$(cat /proc/1/comm)" = postgres && pg_isready -q -U qa -d ' + DATABASE
        return [
            'create', '--name', self.name, '--label', OWNER_LABEL + '=' + self.owner,
            '--pull', 'never', '--platform', 'linux/amd64', '--user', f'{self.uid}:{self.gid}',
            '--network', 'none', '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges:true', '--pids-limit', '100',
            '--memory', '512m', '--cpus', '2', '--shm-size', '64m',
            '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=32m,mode=1777',
            '--tmpfs', f'/var/run/postgresql:rw,noexec,nosuid,nodev,size=1m,uid={self.uid},gid={self.gid},mode=0700',
            '--mount', f'type=bind,src={self.root / "data"},dst=/var/lib/postgresql/data',
            '--mount', f'type=bind,src={self.socket},dst=/run/artex-ci',
            '--env', 'PGDATA=/var/lib/postgresql/data',
            '--env', 'POSTGRES_USER=qa', '--env', 'POSTGRES_DB=' + DATABASE,
            # Synthetic fixture-only value; local sockets use trust, every TCP HBA rule rejects.
            '--env', 'POSTGRES_PASSWORD=synthetic-disposable-ci-only',
            '--env', 'POSTGRES_HOST_AUTH_METHOD=reject',
            '--env', 'POSTGRES_INITDB_ARGS=--auth-local=trust --auth-host=reject --no-locale --encoding=UTF8',
            '--health-cmd', health, '--health-interval', '1s', '--health-timeout', '2s', '--health-retries', '60',
            IMAGE, 'postgres', '-c', 'listen_addresses=', '-c', 'unix_socket_directories=' + sockets,
            '-c', 'unix_socket_permissions=0700', '-c', 'max_connections=20',
            '-c', 'shared_buffers=32MB', '-c', 'jit=off', '-c', 'log_statement=none', '-c', 'timezone=UTC',
        ]

    def start(self) -> None:
        self.docker('pull', '--platform', 'linux/amd64', IMAGE, timeout=180)
        if self.docker('image', 'inspect', '--format', '{{.Id}}', IMAGE) != IMAGE_ID:
            raise FixtureError('Official image identity differs from the pinned image')
        self.create_attempted = True  # Cleanup also covers a lost/timeout create response.
        identifier = self.docker(*self.create_arguments())
        if not re.fullmatch(r'[0-9a-f]{64}', identifier):
            raise FixtureError('Unexpected disposable container identity')
        self.verify_container()
        self.docker('start', self.name)
        deadline = time.monotonic() + 75
        while time.monotonic() < deadline:
            try:
                state = json.loads(self.docker('inspect', '--format', '{{json .State}}', self.name))
            except (ValueError, TypeError) as error:
                raise FixtureError('Malformed disposable container state') from error
            if not state.get('Running') or state.get('Health', {}).get('Status') == 'unhealthy':
                raise FixtureError('Disposable database failed its bounded startup check')
            if state.get('Health', {}).get('Status') == 'healthy':
                return
            time.sleep(0.5)
        raise FixtureError('Disposable database startup timed out')

    def verify_container(self) -> None:
        try:
            info = json.loads(self.docker('inspect', '--format', '{{json .}}', self.name))
            host, config = info['HostConfig'], info['Config']
            expected_mounts = {(str(self.root / 'data'), '/var/lib/postgresql/data'),
                               (str(self.socket), '/run/artex-ci')}
            mounts = {(item['Source'], item['Destination']) for item in info['Mounts'] if item['Type'] == 'bind'}
            valid = (host['NetworkMode'] == 'none' and host['ReadonlyRootfs'] is True
                     and host['Privileged'] is False and not host.get('PortBindings')
                     and not host.get('PublishAllPorts') and host['CapDrop'] == ['ALL']
                     and any(value in host['SecurityOpt'] for value in ('no-new-privileges', 'no-new-privileges:true'))
                     and config['User'] == f'{self.uid}:{self.gid}' and config['Image'] == IMAGE
                     and config['Labels'].get(OWNER_LABEL) == self.owner and mounts == expected_mounts)
        except (ValueError, TypeError, KeyError) as error:
            raise FixtureError('Malformed disposable container configuration') from error
        if not valid:
            raise FixtureError('Disposable container isolation or ownership mismatch')

    def verify_database(self, connect) -> None:
        # No libpq environment/password/service fallback, even for this private socket.
        with connect(host=str(self.socket), port=5432, dbname=DATABASE, user='qa', password='',
                     passfile=str(self.root / 'empty.pgpass'), sslmode='disable', connect_timeout=3,
                     options='-c statement_timeout=4000', autocommit=True) as connection:
            row = connection.execute("SELECT current_database(),current_user,inet_server_addr(),"
                                     "current_setting('server_version_num'),current_setting('listen_addresses')").fetchone()
            if row != (DATABASE, 'qa', None, '170012', ''):
                raise FixtureError('Disposable database identity, transport or version mismatch')

    def run_baseline(self) -> None:
        arguments = [sys.executable, str(ENTRYPOINT), '--host', str(self.socket), '--port', '5432',
                     '--user', 'qa', '--database', DATABASE, '--confirm-disposable', DATABASE]
        process = subprocess.Popen(arguments, cwd=REPOSITORY, env=self.environment, start_new_session=True)
        try:
            if process.wait(timeout=180) != 0:
                raise FixtureError('Baseline acceptance failed')
        except BaseException:
            self.terminate_child(process)
            raise

    @staticmethod
    def terminate_child(process) -> None:
        # Bound interrupted/timeout fixture processes and their child process group.
        for signum, wait in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
            try:
                os.killpg(process.pid, signum)
            except ProcessLookupError:
                return
            try:
                process.wait(timeout=wait)
                if signum == signal.SIGKILL:
                    return
            except subprocess.TimeoutExpired:
                continue
        raise FixtureError('Acceptance process group cleanup unresolved')

    def cleanup(self) -> None:
        if self.create_attempted:
            # Never delete by an arbitrary name or perform Docker/system-wide prune.
            query = ('ps', '-aq', '--no-trunc', '--filter', 'label=' + OWNER_LABEL + '=' + self.owner)
            found = self.docker(*query).splitlines()
            if len(found) > 1 or any(not re.fullmatch(r'[0-9a-f]{64}', item) for item in found):
                raise FixtureError('Disposable container ownership is unresolved')
            if found:
                self.docker('rm', '--force', '--volumes', found[0])
            if self.docker(*query):
                raise FixtureError('Disposable container cleanup unresolved')
        # Remove only our just-created private directory, after absence is confirmed.
        shutil.rmtree(self.root)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--github-hosted-disposable', action='store_true', required=True)
    args = parser.parse_args(argv)
    fixture = None
    previous = None
    result = 1
    try:
        preflight(args.github_hosted_disposable)
        import psycopg
        fixture = Fixture(Path(tempfile.mkdtemp(prefix='artex-ci-pg-', dir='/tmp')),
                          secrets.token_hex(16), os.getuid(), os.getgid())
        def interrupted(signum, frame):
            raise KeyboardInterrupt
        previous = signal.signal(signal.SIGTERM, interrupted)
        fixture.prepare()
        fixture.start()
        fixture.verify_database(psycopg.connect)
        fixture.run_baseline()
        result = 0
    except (Exception, KeyboardInterrupt) as error:
        detail = str(error) if isinstance(error, FixtureError) else type(error).__name__
        print('FAIL: disposable PostgreSQL acceptance: ' + detail, file=sys.stderr)
    finally:
        if fixture is not None:
            try:
                fixture.cleanup()
            except Exception:
                print('FAIL: disposable fixture cleanup unresolved; job must fail', file=sys.stderr)
                result = 1
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)
    if result == 0:
        print('PASS: baseline PostgreSQL acceptance and disposable fixture cleanup')
    return result


if __name__ == '__main__':
    raise SystemExit(main())

"""Opt-in synthetic API/model -> prebuilt Next -> Python Skills smoke check.

Requires a matching preview build and installed dependencies. This runner never
installs, builds, contacts a provider, or accepts an existing Skills root.
"""
import argparse
from contextlib import contextmanager, nullcontext
import http.client
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parents[2]


class RunCancelled(KeyboardInterrupt):
    """Supported runner cancellation, not a successful smoke result."""


class Cancellation:
    def __init__(self):
        self.requested = False
        self.deferred = False

    def cancel(self, _signum, _frame):
        if not self.requested:
            self.requested = True
            if not self.deferred:
                raise RunCancelled()

    @contextmanager
    def defer(self):
        previous = self.deferred
        self.deferred = True
        try:
            yield
        finally:
            self.deferred = previous


@contextmanager
def cancellation_scope():
    # Standalone runner ownership only. The first ordinary cancellation unwinds
    # into fixture cleanup; repeated signals cannot interrupt that cleanup.
    cancellation = Cancellation()
    previous = {}
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, cancellation.cancel)
        yield cancellation
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend-port', type=int, required=True)
    parser.add_argument('--backend-port', type=int, required=True)
    args = parser.parse_args(argv)
    if not all(1024 <= port <= 65535 for port in (args.frontend_port, args.backend_port)):
        parser.error('ports must be unprivileged TCP ports')
    if args.frontend_port == args.backend_port:
        parser.error('frontend and backend ports must differ')
    return args


def child_environment(home):
    # No provider/database keys, proxy settings, NODE_OPTIONS, inherited Python
    # startup options or ambient Next/mock/export profile overrides.
    return {
        'PATH': os.environ.get('PATH', os.defpath),
        'HOME': str(home), 'TMPDIR': str(home), 'LANG': 'C.UTF-8',
        'PYTHONPATH': str(REPO / 'python'),
        'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1',
        'PYTHONUNBUFFERED': '1', 'PYTHONUTF8': '1',
        'NEXT_TELEMETRY_DISABLED': '1', 'HUSKY': '0',
    }


def reserve_ports(*ports):
    # Preflight only: not a reservation across launch. Readiness and random-secret
    # login subsequently check that the actual children are usable.
    sockets = []
    try:
        for port in ports:
            sock = socket.socket()
            sockets.append(sock)
            # Match the listeners' ordinary address reuse after the prior phase
            # closes. This does not share an actively listening TCP port.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('127.0.0.1', port))
    finally:
        for sock in sockets:
            sock.close()


def wait_ready(child, port, path, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeError('child exited before readiness')
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=0.5)
        try:
            conn.request('GET', path, headers={'Connection': 'close'})
            response = conn.getresponse()
            value = json.loads(response.read(4096))
            if response.status == 200 and value == {'initialized': True}:
                if child.poll() is not None:
                    raise RuntimeError('child exited during readiness')
                return
        except (OSError, http.client.HTTPException, ValueError):
            pass
        finally:
            conn.close()
        time.sleep(0.1)
    raise RuntimeError('bounded readiness timeout')


def stop_naturally(child, timeout):
    if child.poll() is not None:
        raise RuntimeError('child exited before normal shutdown')
    child.send_signal(signal.SIGTERM)
    if child.wait(timeout=timeout) != 0:
        raise RuntimeError('child failed normal shutdown')


def contain(children):
    """Failed synthetic fixture teardown, never passing shutdown evidence."""
    result = {'forced_fixture_cleanup': False, 'cleanup_errors': []}
    for index in reversed(range(len(children))):
        child = children[index]
        alive = True
        try:
            alive = child.poll() is None
        except BaseException as error:
            result['cleanup_errors'].append(cleanup_error('poll', error, index))
        if alive:
            result['forced_fixture_cleanup'] = True
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                # A child can exit between poll and signal; still reap it.
                pass
            except BaseException as error:
                result['cleanup_errors'].append(cleanup_error('signal', error, index))
            try:
                child.wait(timeout=5)
            except BaseException as error:
                result['cleanup_errors'].append(cleanup_error('wait', error, index))
    return result


def cleanup_error(stage, error, index=None):
    # Never copy exception messages/argv: they may contain synthetic credentials.
    return {'stage': stage, 'error_type': type(error).__name__, 'index': index}


def write_result(path, result, cancellation=None):
    # Both phase evidence and the top-level summary need this protection. The
    # latch is monotonic: at most one corrective write is needed if cancellation
    # first arrives while serializing/writing an otherwise successful result.
    with cancellation.defer() if cancellation is not None else nullcontext():
        result['cancelled'] = bool(cancellation and cancellation.requested)
        if result['cancelled']:
            result['passed'] = False
        path.write_text(json.dumps(result, indent=2) + '\n', encoding='utf8')
        if cancellation and cancellation.requested and not result['cancelled']:
            result['cancelled'] = True
            result['passed'] = False
            path.write_text(json.dumps(result, indent=2) + '\n', encoding='utf8')


def finalize_phase(phase, children, logs, completed, cancellation=None):
    result = {'passed': False, 'forced_fixture_cleanup': False,
              'cleanup_errors': [], 'browser_tested': False}
    # Even the *first* cancellation arriving during cleanup is only latched;
    # it must not escape between per-child guards and skip remaining children.
    with cancellation.defer() if cancellation is not None else nullcontext():
        try:
            result.update(contain(children))
        except BaseException as error:
            # Unexpected harness failures must not skip log closure/result writing.
            result['cleanup_errors'].append(cleanup_error('containment', error))
        finally:
            try:
                for index, log in enumerate(logs):
                    try:
                        log.close()
                    except BaseException as error:
                        result['cleanup_errors'].append(cleanup_error('log_close', error, index))
            finally:
                result['cancelled'] = bool(cancellation and cancellation.requested)
                result['passed'] = bool(completed and not result['forced_fixture_cleanup']
                                        and not result['cleanup_errors'] and not result['cancelled'])
                # If evidence storage itself fails, propagate failure. No successful
                # finalization or complete evidence is claimed for that case.
                write_result(phase / 'result.json', result, cancellation)
    return result


def seed(root):
    root.mkdir(mode=0o700)
    sample = root / 'sample'
    sample.mkdir(mode=0o700)
    manifest = '---\nname: sample\ndescription: Synthetic preview fixture\n---\n# Inert data\n'
    (sample / 'SKILL.md').write_text(manifest, encoding='utf8')
    (sample / 'SKILL.md').chmod(0o600)
    return manifest


def verify_files(root, writable, original_manifest):
    if (root / 'sample' / 'SKILL.md').read_text(encoding='utf8') != original_manifest:
        raise RuntimeError('fixture manifest changed')
    expected = {'.artex-skills.lock', 'sample'} | ({'資料'} if writable else set())
    if {p.name for p in root.iterdir()} != expected:
        raise RuntimeError('unexpected root effect')
    if {p.name for p in (root / 'sample').iterdir()} != {'SKILL.md'}:
        raise RuntimeError('unexpected sample effect')
    if writable:
        if (root / '資料' / '参考' / '例.txt').read_text(encoding='utf8') != 'Edited 世界\n':
            raise RuntimeError('Unicode roundtrip did not persist')
        if not (root / '資料' / 'SKILL.md').is_file():
            raise RuntimeError('new skill manifest missing')


def run_phase(args, evidence, env, writable, cancellation=None):
    from artex.runtime_lease import RuntimeLease

    mode = 'write-enabled' if writable else 'read-only'
    phase = evidence / mode
    phase.mkdir(mode=0o700)
    root = phase / 'skills'
    original = seed(root)
    password_file = phase / 'password'
    password_file.write_text(secrets.token_urlsafe(24) + '\n', encoding='utf8')
    password_file.chmod(0o600)
    origin = f'http://127.0.0.1:{args.frontend_port}'
    children, logs = [], []
    completed = False
    try:
        reserve_ports(args.frontend_port, args.backend_port)
        command = [sys.executable, '-m', 'artex.skills_workspace', '--root', str(root),
                   '--password-file', str(password_file), '--exclusive-owner',
                   '--port', str(args.backend_port), '--frontend-origin', origin]
        if writable:
            command.append('--enable-writes')
        for name, command in [
            ('backend', command),
            ('frontend', ['node', 'web/dev/python-preview-server.mjs',
                          '--frontend-port', str(args.frontend_port),
                          '--backend-port', str(args.backend_port)]),
        ]:
            log = (phase / f'{name}.log').open('wb')
            logs.append(log)
            child = subprocess.Popen(command, cwd=REPO, env=env, stdout=log,
                                     stderr=subprocess.STDOUT, start_new_session=True)
            children.append(child)
            wait_ready(child, args.backend_port if name == 'backend' else args.frontend_port,
                       '/api/auth/status', 15 if name == 'backend' else 90)
        with (phase / 'client.log').open('wb') as log:
            client = subprocess.Popen(
                ['node', 'web/integration/skills-preview-client.mjs',
                 str(args.frontend_port), mode, str(password_file)],
                cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True)
            children.append(client)
            if client.wait(timeout=90) != 0:
                raise RuntimeError('API/model smoke failed; inspect private client.log')
        verify_files(root, writable, original)
        stop_naturally(children[1], 20)
        stop_naturally(children[0], 15)
        lease = RuntimeLease(str(root / '.artex-skills.lock'))
        lease.acquire()
        lease.release()
        completed = True
    finally:
        final = finalize_phase(phase, children, logs, completed, cancellation)
    if cancellation and cancellation.requested:
        raise RunCancelled()
    if not final['passed']:
        raise RuntimeError('synthetic fixture finalization failed; inspect private result.json')
    print(f'PASS {mode}: API/model, proxy, filesystem, natural exits and lease reuse.', flush=True)


def main(argv=None, cancellation=None):
    if not __debug__ or os.environ.get('PYTHONOPTIMIZE'):
        raise RuntimeError('assertions must be enabled')
    args = parse_args(argv)
    if os.name != 'posix':
        raise RuntimeError('POSIX is required')
    # Dependencies must already be installed in this interpreter. Inherited
    # PYTHONPATH is deliberately not passed to children.
    import bcrypt  # noqa: F401
    from artex.runtime_lease import RuntimeLease  # noqa: F401

    evidence = Path(tempfile.mkdtemp(prefix='artex-skills-preview-')).resolve()
    print(f'Private synthetic evidence: {evidence}', flush=True)
    env = child_environment(evidence)
    result = {'passed': False, 'browser_tested': False, 'phases': []}
    try:
        for writable in (False, True):
            run_phase(args, evidence, env, writable, cancellation)
            result['phases'].append('write-enabled' if writable else 'read-only')
        result['passed'] = True
    finally:
        write_result(evidence / 'result.json', result, cancellation)
    if cancellation and cancellation.requested:
        raise RunCancelled()
    print('PASS Skills preview smoke. No browser, task, database, provider or import coverage.', flush=True)


def cli(argv=None):
    try:
        with cancellation_scope() as cancellation:
            main(argv, cancellation)
    except (Exception, KeyboardInterrupt):
        # Private logs may contain synthetic secrets; never echo them here.
        print('FAIL Skills preview smoke; inspect the private evidence and installed prerequisites.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(cli())

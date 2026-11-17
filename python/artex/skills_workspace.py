"""Opt-in, local Skills workspace only. No task engine, database or provider.

An operator must exclusively control the root, including all out-of-process
writers/renames/mounts. The advisory lease is not an arbitrary-writer fence.
Unknown mutations retain process ownership; stopping is not rollback or recovery.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import math
import os
import secrets
import stat
import sys
import threading
import time
from wsgiref.simple_server import make_server

from .runtime_lease import RuntimeLease
from .tools.filesystem import ScopedFilesystem
from .server.auth import AuthService, PASSWORD_KEY, validate_password
from .server.passwords import BcryptCodec
from .server.runtime_listener import DevListenerConfig, QuietRuntimeHandler, _BoundedServer
from .server.runtime_signals import RuntimeSignalScope
from .server import runtime_signals as _signals_module
from .server.wsgi import WSGIApplication
from .skills.file_listing import SkillDirectoryScope, SkillFileListingReader
from .skills.file_read import SkillFileReader
from .skills.catalog_read import SkillCatalogReader
from .server.skill_content_read import SkillContentReadService
from .server.skill_files_read import SkillFilesReadService
from .server.skill_catalog_read import SkillCatalogReadService
from .server.skill_content_write import SkillContentWriteService, MAX_JSON_BYTES
from .server.skill_directory_create import SkillDirectoryCreateService
from .server.skills_workspace_setup import bind_skills_workspace


class WorkspaceUnavailable(RuntimeError):
    """Sanitized operator error, not a description of secret or file contents."""


def _canonical(value):
    if type(value) is not str or not value.startswith('/') or value == '/' or os.path.normpath(value) != value or '\0' in value:
        raise ValueError('An explicit canonical absolute path is required')
    return value


def _root_identity(root):
    with ScopedFilesystem(_canonical(root)) as fs:
        info = os.fstat(fs._fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('Existing owner-only mode0700 Skills root required')
        return info.st_dev, info.st_ino


@dataclass(frozen=True)
class WorkspaceConfig:
    root: str
    password_file: str
    exclusive_owner: bool
    enable_writes: bool = False
    port: int = 8787
    frontend_origin: str | None = None

    def validate(self):
        if self.exclusive_owner is not True or type(self.enable_writes) is not bool:
            raise ValueError('Explicit exclusive root ownership required')
        _root_identity(self.root)
        _canonical(self.password_file)
        if os.path.commonpath((self.root, self.password_file)) == self.root:
            raise ValueError('Password input must be outside the served root')
        DevListenerConfig(port=self.port, frontend_origin=self.frontend_origin).validate()


def _read_password(config):
    """Read one bounded private regular file without symlink ancestors or FIFOs."""
    config.validate()
    directory, name = os.path.split(config.password_file)
    with ScopedFilesystem(directory) as fs:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fs._fd)
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                    or stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1 or before.st_size > 73):
                raise ValueError('Private mode0600 single-link password file required')
            data = os.read(fd, 74)
            after = os.fstat(fd)
            current = os.stat(name, dir_fd=fs._fd, follow_symlinks=False)
            key = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            if key(before) != key(after) or key(after) != key(current) or len(data) != before.st_size:
                raise ValueError('Password input changed')
        finally:
            os.close(fd)
    if data.endswith(b'\n'): data = data[:-1]
    password = data.decode('utf-8')
    if '\n' in password or '\r' in password or '\0' in password or validate_password(password):
        raise ValueError('Invalid bounded password input')
    return password


class _MemoryPassword:
    def __init__(self, encoded):
        self._encoded = encoded
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            return self._encoded if key == PASSWORD_KEY else None

    def insert_if_absent(self, key, value):
        return False  # Always initialized before a socket can accept requests.

    def set(self, key, value):
        if key != PASSWORD_KEY: raise ValueError('Unsupported password setting')
        with self._lock: self._encoded = value


@dataclass(frozen=True)
class WorkspaceStop:
    stopped: bool
    reason: str


class SkillsWorkspaceHost:
    """Explicitly prepared original host; operations require its request token."""
    @classmethod
    def prepare(cls, config):
        if type(config) is not WorkspaceConfig: raise ValueError('Exact Skills workspace configuration required')
        config.validate()
        # Hash before acquiring ownership. Never persist or print password/key.
        encoded = BcryptCodec().hash(_read_password(config))
        self = cls()
        self._config = config
        self._identity = _root_identity(config.root)
        self._state = threading.Condition(threading.RLock())
        self._tree = threading.RLock()
        self._local = threading.local()
        self._active = set()
        self._sealed = self._unknown = self._closed = self._stopping = False
        self._server = self._thread = None
        self._launch_uncertain = False
        self._lease = RuntimeLease(os.path.join(config.root, '.artex-skills.lock'))
        self._lease.acquire()
        self._permit = None
        try:
            self._permit = self._lease.claim(self, guarded=True)
            self._scope = SkillDirectoryScope(config.root, *self._identity, secrets.token_hex(16))
            guard = self._guard  # Exact same callable object across all five services.
            current = self._allowed
            source = SkillFileReader(self._scope, authorize=current, current=current, lifecycle_guard=guard, max_bytes=262144)
            listing = SkillFileListingReader(self._scope, authorize=current, current=current, lifecycle_guard=guard)
            catalog = SkillCatalogReader(self._scope, source, authorize_global=current, current=current, lifecycle_guard=guard)
            self._auth = AuthService(_MemoryPassword(encoded), BcryptCodec(), secrets.token_bytes(32))
            base = WSGIApplication(self._auth.router(version='skills-workspace-dev'), max_body_bytes=MAX_JSON_BYTES)
            self._bundle = bind_skills_workspace(base,
                catalog_service=SkillCatalogReadService(catalog, authorize=current),
                files_service=SkillFilesReadService(listing, authorize=current),
                content_service=SkillContentReadService(source, authorize=current),
                write_service=SkillContentWriteService(self._scope, authorize=self._write_allowed,
                    mutation_authorize=self._write_allowed, current=current, writer_guard=guard),
                directory_service=SkillDirectoryCreateService(self._scope, authorize=self._write_allowed,
                    mutation_authorize=self._write_allowed, current=current, writer_guard=guard),
                authorize_catalog=current, authorize_files=current, authorize_content=current,
                authorize_write=self._write_allowed, authorize_directory=self._write_allowed)
            self._application = self._bundle.application
            self._check()
            return self
        except BaseException:
            # No socket, request or mutation has been admitted during preparation.
            self._lease.release(owner=self, permit=self._permit)
            raise WorkspaceUnavailable('Skills workspace preparation failed') from None

    def _check(self):
        self._lease.validate()
        if _root_identity(self._config.root) != self._identity:
            raise WorkspaceUnavailable('Skills root identity changed')

    def _allowed(self, *args):
        with self._state:
            token = getattr(self._local, 'token', None)
            if self._closed or self._unknown or token not in self._active: return False
        self._check()
        return True

    def _write_allowed(self, *args):
        return self._config.enable_writes and self._allowed()

    @contextmanager
    def _guard(self, *args):
        with self._tree:
            if not self._allowed(): raise PermissionError('Workspace admission unavailable')
            yield
            if not self._allowed(): raise PermissionError('Workspace admission unavailable')

    def _quarantine(self):
        with self._state:
            self._unknown = self._sealed = True

    def application(self, environ, start_response):
        # The active token lives through iterator completion/close, not just dispatch.
        def output():
            token = object()
            method = environ.get('REQUEST_METHOD', 'GET')
            method = method.upper() if isinstance(method, str) else ''
            with self._state:
                rejected = self._sealed or self._closed
                if not rejected:
                    if getattr(self._local, 'token', None) is not None:
                        raise WorkspaceUnavailable('Nested workspace requests are unsupported')
                    self._active.add(token)
                    self._local.token = token
            if rejected:
                body = b'{"error":"Skills workspace unavailable"}\n'
                start_response('503 Service Unavailable', [('Content-Type','application/json'),('Content-Length',str(len(body)))])
                if method != 'HEAD': yield body
                return
            path = environ.get('PATH_INFO', '')
            mutation = method in ('PUT', 'POST') and type(path) is str and path.startswith('/api/skills/')
            result = None
            status = None
            started = False
            def actual(code, headers, exc_info=None):
                nonlocal status, started
                status = int(code.split(' ', 1)[0]); started = True
                if mutation and status >= 500: self._quarantine()
                return start_response(code, headers, exc_info) if exc_info is not None else start_response(code, headers)
            try:
                self._check()
                result = self._application(environ, actual)
                for chunk in result: yield chunk
                if mutation and (status is None or status >= 500): self._quarantine()
            except Exception:
                if mutation: self._quarantine()
                if not started:
                    body = b'{"error":"Skills workspace unavailable"}\n'
                    start_response('503 Service Unavailable', [('Content-Type','application/json'),('Content-Length',str(len(body)))])
                    if method != 'HEAD': yield body
            except BaseException:
                if mutation: self._quarantine()
                raise
            finally:
                try:
                    if result is not None and callable(getattr(result, 'close', None)): result.close()
                except BaseException:
                    if mutation: self._quarantine()
                finally:
                    with self._state:
                        self._active.discard(token)
                        self._local.token = None
                        self._state.notify_all()
        return output()

    def start(self):
        with self._state:
            if self._sealed or self._closed: raise WorkspaceUnavailable('Workspace shutdown already requested')
            if self._server is not None: return False
            self._check()
            config = DevListenerConfig(port=self._config.port, frontend_origin=self._config.frontend_origin)
            server = make_server('127.0.0.1', config.port, self.application,
                                 server_class=_BoundedServer, handler_class=QuietRuntimeHandler)
            try: server.configure(config, MAX_JSON_BYTES)
            except BaseException:
                server.server_close(); raise
            thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .05}, name='artex-skills-loopback', daemon=False)
            self._server, self._thread = server, thread
            self._launch_uncertain = True
            try:
                thread.start()
                self._launch_uncertain = False
            except BaseException as error:
                self._sealed = True
                if type(error) is RuntimeError and thread.ident is None:
                    server.server_close(); self._server = self._thread = None
                    self._launch_uncertain = False
                raise
            return True

    def stop(self, timeout=5.0):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError('Finite nonnegative shutdown timeout required')
        deadline = time.monotonic() + timeout
        remaining = lambda: max(0, deadline-time.monotonic())
        with self._state:
            if self._closed: return WorkspaceStop(True, 'already_closed')
            if self._stopping: return WorkspaceStop(False, 'shutdown_busy')
            self._sealed = self._stopping = True
        try:
            if self._server is not None:
                if self._thread.is_alive(): self._server.shutdown()
                self._server.server_close()
                if not self._server.quiesce(remaining()): return WorkspaceStop(False, 'http_unresolved')
                if self._thread.ident is not None: self._thread.join(remaining())
                if self._thread.is_alive(): return WorkspaceStop(False, 'listener_unresolved')
            with self._state:
                if getattr(self._local, 'token', None) in self._active:
                    return WorkspaceStop(False, 'request_unresolved')
                if not self._state.wait_for(lambda: not self._active, remaining()):
                    return WorkspaceStop(False, 'request_unresolved')
                if self._launch_uncertain: return WorkspaceStop(False, 'launch_unresolved')
                if self._unknown: return WorkspaceStop(False, 'mutation_unknown')
                self._check()
                self._lease.release(owner=self, permit=self._permit)
                self._closed = True
                return WorkspaceStop(True, 'closed')
        except Exception:
            return WorkspaceStop(False, 'ownership_unresolved')
        finally:
            with self._state: self._stopping = False


def _retain_foreground(message):
    try: print(message, file=sys.stderr, flush=True)
    except BaseException: pass  # A disconnected log consumer cannot end ownership.
    while True:
        try: time.sleep(1)
        except KeyboardInterrupt: pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--root', required=True)
    parser.add_argument('--password-file', required=True)
    parser.add_argument('--exclusive-owner', required=True, action='store_true')
    parser.add_argument('--enable-writes', action='store_true')
    parser.add_argument('--port', type=int, default=8787)
    parser.add_argument('--frontend-origin')
    args = parser.parse_args(argv)
    host = None
    signals = RuntimeSignalScope()
    installed = False
    failed = False
    try:
        config = WorkspaceConfig(**vars(args)); config.validate()
        signals.install(); installed = True
        host = SkillsWorkspaceHost.prepare(config)
        if signals.signal_number is None:
            host.start()
            print('Skills workspace ready on loopback; credentials remain private.', flush=True)
        while signals.signal_number is None:
            signals.validate()
            if host._thread is not None and not host._thread.is_alive():
                raise WorkspaceUnavailable('Listener stopped unexpectedly')
            time.sleep(.05)
    except (Exception, KeyboardInterrupt):
        failed = True
        print('Skills workspace failed; no automatic restart or mutation retry.', file=sys.stderr)
    finally:
        if host is not None:
            try:
                stopped = host.stop()
                drained = type(stopped) is WorkspaceStop and stopped.stopped is True
            except BaseException:
                drained = False
            if not drained:
                # A shutdown interruption is not proof that owned work ended.
                # Retain this exact host and signal scope; do not retry stop.
                _retain_foreground('Shutdown unresolved; retaining process ownership. Do not restart or retry mutations.')
        if installed:
            try: restored = signals.restore()
            except BaseException: restored = False
            if not restored:
                _retain_foreground('Host drained, but signal restoration is unresolved; retaining the exact signal scope.')
        elif _signals_module._active_scope is signals:
            # install() already attempted its own rollback. Do not retry it
            # implicitly or lose the exact unresolved process-wide scope.
            _retain_foreground('Signal installation rollback unresolved; retaining the exact signal scope.')
    return 0 if not failed and host is not None and host._closed else 1


if __name__ == '__main__':
    raise SystemExit(main())

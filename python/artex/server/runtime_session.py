"""Explicit development start/drain coordinator, not a configurable CLI launcher.

The caller acquires RuntimeLease BEFORE assembling this listener and owns all
assembly-failure cleanup. Supply only a fresh, unstarted exact RuntimeListener.
No context-manager cleanup, automatic restart, signals, force release, default
policies, schema changes or credential/provider discovery are provided.
"""
from dataclasses import dataclass
import math
from threading import RLock
from ..runtime_lease import RuntimeLease
from .runtime_listener import RuntimeListener, ListenerStop


@dataclass(frozen=True)
class SessionResult:
    started: bool
    stopped: bool
    reason: str


class RuntimeSession:
    def __init__(self, listener, lease):
        if type(listener) is not RuntimeListener or type(lease) is not RuntimeLease:
            raise ValueError('Exact development listener and acquired process lease required')
        lease.validate()
        listener._check()
        pump = listener._bundle.pump
        if listener._server is not None or listener._closed or listener._stopping or pump._thread is not None or pump._active:
            raise ValueError('A fresh unstarted listener and pump are required')
        self.listener, self.lease = listener, lease
        self._pins = (listener, lease)
        self._lock = RLock()
        self._busy = False
        self._state = 'new'
        self.result = SessionResult(False, False, 'not_started')
        lease.claim(self)

    @staticmethod
    def _timeout(timeout):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 300:
            raise ValueError('Shutdown timeout must be between zero and 300 seconds')

    def _bindings(self):
        if self.listener is not self._pins[0] or self.lease is not self._pins[1]:
            raise RuntimeError('Session ownership changed')

    def start(self, *, shutdown_timeout=10):
        self._timeout(shutdown_timeout)
        with self._lock:
            self._bindings()
            if self._busy: return SessionResult(False, False, 'session_busy')
            if self._state != 'new': return self.result
            self._busy = True
            self._state = 'starting'
        try:
            self._pins[1].validate()
            if self._pins[0].start_listener() is not True: raise RuntimeError()
            self._pins[1].validate()
            if self._pins[0].start_pump() is not True: raise RuntimeError()
            self._pins[1].validate()
            self._state = 'running'
            self.result = SessionResult(True, False, 'running')
        except BaseException:
            # Even a failed bind/pump start may own resources. Retain the lease
            # unless the existing listener explicitly verifies complete drain.
            self._state = 'stopping'
            self.result = self._drain(shutdown_timeout, 'startup_failed')
        finally:
            with self._lock: self._busy = False
        return self.result

    def _drain(self, timeout, reason):
        try:
            result = self._pins[0].stop(timeout)
            if type(result) is not ListenerStop or result.stopped is not True:
                return SessionResult(False, False, reason + '_unresolved')
            self._pins[1].release(owner=self)
            self._state = 'closed'
            return SessionResult(False, True, reason)
        except BaseException:
            return SessionResult(False, False, reason + '_unresolved')

    def stop(self, timeout=10):
        self._timeout(timeout)
        with self._lock:
            self._bindings()
            if self._busy: return SessionResult(False, False, 'session_busy')
            if self._state == 'closed': return self.result
            self._busy = True
            self._state = 'stopping'
        try:
            self.result = self._drain(timeout, 'stopped')
            return self.result
        finally:
            with self._lock: self._busy = False

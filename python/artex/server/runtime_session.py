"""Explicit development start/drain coordinator, not a configurable CLI launcher.

The caller acquires RuntimeLease BEFORE assembling this listener and owns all
assembly-failure cleanup. Supply only a fresh, unstarted exact RuntimeListener.
No context-manager cleanup, automatic restart, force release, default
policies, schema changes or credential/provider discovery are provided.
Signals are installed only by the separate explicit run_until_signalled entry.
"""
from dataclasses import dataclass
import math
from threading import RLock, Event
from ..runtime_lease import RuntimeLease
from .runtime_listener import RuntimeListener, ListenerStop


@dataclass(frozen=True)
class SessionResult:
    started: bool
    stopped: bool
    reason: str


@dataclass(frozen=True)
class ForegroundResult:
    reason: str
    shutdown: SessionResult


@dataclass(frozen=True)
class SignalForegroundResult:
    foreground: ForegroundResult
    signal_number: int | None
    handlers_restored: bool


class RuntimeSession:
    def __init__(self, listener, lease, *, lease_owner=None):
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
        self._driving = False
        self.result = SessionResult(False, False, 'not_started')
        if lease_owner is None:
            lease.claim(self)
        else:
            lease.transfer(lease_owner, self)

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

    def run_until_stopped(self, stop_requested, *, poll_interval=0.1, shutdown_timeout=10):
        """Explicit blocking development run, with no signal/process handlers.

        The host supplies an Event and remains responsible for unresolved drain.
        This method never exits the process or releases an unresolved lease.
        Health is a finite observation, not proof of readiness or atomic liveness.
        Do not independently start/tick/stop the session while this driver owns it.
        """
        return self._run_foreground(stop_requested, poll_interval=poll_interval,
                                    shutdown_timeout=shutdown_timeout)

    def _validate_foreground(self, stop_requested, poll_interval, shutdown_timeout):
        if type(stop_requested) is not Event:
            raise ValueError('An explicit stop Event is required')
        if type(poll_interval) not in (int, float) or not math.isfinite(poll_interval) or not .01 <= poll_interval <= 1:
            raise ValueError('Poll interval must be between 0.01 and 1 second')
        self._timeout(shutdown_timeout)
        with self._lock:
            self._bindings()
            if self._driving or self._busy or self._state != 'new':
                raise RuntimeError('Foreground run requires an unused session')

    def run_until_signalled(self, signals, stop_requested, *, poll_interval=0.1, shutdown_timeout=10):
        """Explicit main-thread SIGINT/SIGTERM -> existing foreground drain.

        Supply a fresh RuntimeSignalScope and retain it if restoration fails.
        Signals coalesce; they never force exit or interrupt cleanup. No event,
        runtime callback or lock is touched by the handler. Existing callbacks
        remain cooperative, so poll_interval is not a bound on overall latency.
        An unresolved drain retains the session/lease. Returning from this call
        does not authorize process exit or restart; the host owns reconciliation.
        """
        from .runtime_signals import RuntimeSignalScope
        if type(signals) is not RuntimeSignalScope:
            raise ValueError('An explicit fresh runtime signal scope is required')
        self._validate_foreground(stop_requested, poll_interval, shutdown_timeout)
        signals.install()
        try:
            result = self._run_foreground(stop_requested, poll_interval=poll_interval,
                                         shutdown_timeout=shutdown_timeout, signals=signals)
        finally:
            restored = signals.restore()
        return SignalForegroundResult(result, signals.signal_number, restored)

    def _run_foreground(self, stop_requested, *, poll_interval, shutdown_timeout, signals=None):
        with self._lock:
            self._validate_foreground(stop_requested, poll_interval, shutdown_timeout)
            self._driving = True
        def requested():
            if signals is not None:
                signals.validate()
                if signals.signal_number is not None:
                    return 'signal_received'
            return 'stop_requested' if stop_requested.is_set() else None
        reason = 'runtime_unavailable'
        try:
            pending = requested()
            if pending is not None:
                reason = pending
            else:
                result = self.start(shutdown_timeout=shutdown_timeout)
                if not result.started:
                    # start already attempted its one cleanup sequence. Do not
                    # automatically retry possibly effectful unresolved cleanup.
                    with self._lock: self._driving = False
                    return ForegroundResult('startup_failed', result)
                while True:
                    pending = requested()
                    if pending is not None:
                        reason = pending
                        break
                    self._bindings()
                    listener, lease = self._pins
                    lease.validate()
                    listener._check()
                    pump = listener._bundle.pump
                    if pump.failure:
                        reason = 'pump_failed'
                        break
                    if listener._thread is None or not listener._thread.is_alive():
                        reason = 'listener_stopped'
                        break
                    if pump._thread is None or not pump._thread.is_alive():
                        reason = 'pump_stopped'
                        break
                    # Signal handlers deliberately do not set this Event. The
                    # next bounded poll observes their lock-free latch instead.
                    # Preserve an accepted Event notification even if another
                    # host thread clears the Event before the next is_set().
                    if stop_requested.wait(poll_interval):
                        reason = ('signal_received' if signals is not None and signals.signal_number is not None
                                  else 'stop_requested')
                        break
        except (KeyboardInterrupt, SystemExit):
            reason = 'interrupted'
        except BaseException:
            reason = 'runtime_unavailable'
        try:
            shutdown = self.stop(shutdown_timeout)
        except BaseException:
            shutdown = SessionResult(False, False, 'stopped_unresolved')
        finally:
            with self._lock: self._driving = False
        return ForegroundResult(reason, shutdown)

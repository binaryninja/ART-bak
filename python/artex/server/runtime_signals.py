"""Opt-in, main-thread SIGINT/SIGTERM ownership for a development foreground run.

Construction is inert. The host must exclusively own these process-wide handlers
while installed, must not fork, and must retain this object if restore() fails.
The handler only records a signal number: no locks, Event.set(), callbacks, I/O or
cleanup may run in it. The existing foreground loop observes the latch by polling.
"""
import os
import signal
from threading import current_thread, main_thread


class SignalScopeUnavailable(RuntimeError):
    pass


_active_scope = None


class RuntimeSignalScope:
    def __init__(self):
        self.signal_number = None
        self._previous = {}
        self._attempted = []
        self._pid = None
        self._used = False

        def latch(number, frame):
            if self.signal_number is None:
                self.signal_number = number

        self._handler = latch

    @staticmethod
    def _main_thread():
        if current_thread() is not main_thread():
            raise SignalScopeUnavailable('Signal ownership requires the main thread')

    def install(self):
        global _active_scope
        self._main_thread()
        if self._used or _active_scope is not None:
            raise SignalScopeUnavailable('Signal scope already used or owned')
        numbers = (signal.SIGINT, signal.SIGTERM)
        previous = {number: signal.getsignal(number) for number in numbers}
        if any(value is None for value in previous.values()):
            raise SignalScopeUnavailable('Original signal handlers cannot be restored')
        self._previous = previous
        self._pid = os.getpid()
        self._used = True
        _active_scope = self
        try:
            for number in numbers:
                # Retain the attempted slot even if installation raises after
                # taking effect. restore observes ownership before changing it.
                self._attempted.append(number)
                signal.signal(number, self._handler)
            self.validate()
        except BaseException:
            self.restore()
            raise SignalScopeUnavailable('Signal installation failed; inspect and restore the retained scope') from None

    def validate(self):
        self._main_thread()
        if (_active_scope is not self or self._pid != os.getpid()
                or len(self._attempted) != 2
                or any(signal.getsignal(number) is not self._handler for number in self._attempted)):
            raise SignalScopeUnavailable('Foreground signal ownership changed')

    def restore(self):
        """Restore only handlers still owned here; never overwrite replacements.

        False retains the scope's process ownership for explicit reconciliation.
        This does not stop a runtime or release its lease. Once all original
        handlers are observed restored, another fresh scope may be installed.
        """
        global _active_scope
        self._main_thread()
        if self._pid is not None and self._pid != os.getpid():
            raise SignalScopeUnavailable('Signal scope belongs to another process')
        restored = True
        for number in reversed(self._attempted):
            try:
                current = signal.getsignal(number)
                previous = self._previous[number]
                if current is self._handler:
                    signal.signal(number, previous)
                elif current is not previous:
                    restored = False
                if signal.getsignal(number) is not previous:
                    restored = False
            except BaseException:
                restored = False
        if restored and _active_scope is self:
            _active_scope = None
        return restored

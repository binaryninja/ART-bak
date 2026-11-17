"""Explicit synchronous stepping of one existing task on an assembled runtime.

Requires an exclusive manual controller, not a concurrently served application.
No creation, initialized adoption, listener, thread, lease, or shutdown wiring.
"""
from dataclasses import dataclass
from threading import Lock
from weakref import WeakKeyDictionary

from .pump import EnginePump
from ..manager import RuntimeUnavailable
from ..server.task_runtime_setup import TaskRuntimeApplication


@dataclass(frozen=True)
class TaskDriveResult:
    task_id: str
    state: str
    ticks: int = 0
    advanced: int = 0
    status: str = ''
    started: bool = False

    @property
    def completed(self):
        return self.state == 'completed'


_locks_guard = Lock()
_locks = WeakKeyDictionary()


def drive_existing_task(application, task_id, *, max_ticks=16):
    """Admit an existing fresh task if needed, then perform at most max_ticks.

    The caller must exclusively control this already-assembled application.
    Other admission/manual ticks may race the read checks; this helper is not a
    serving-loop ownership fence. A prior/current background pump is forbidden.
    A tick budget is not a time limit or provider preemption. Non-completion and
    exceptions retain the original application and work for caller reconciliation;
    this function never pauses, cancels, closes, releases, retries or sleeps.
    """
    if (type(task_id) is not str or not 1 <= len(task_id) <= 19
            or not task_id.isascii() or not task_id.isdecimal()
            or task_id.startswith('0') or int(task_id) >= 1 << 63):
        raise ValueError('Canonical positive int64 task ID required')
    if type(max_ticks) is not int or not 1 <= max_ticks <= 1000:
        raise ValueError('Tick budget must be an integer from 1 to 1000')
    if type(application) is not TaskRuntimeApplication or type(application.pump) is not EnginePump:
        raise ValueError('Exact assembled task runtime application required')
    pump, host, manager, runner = (application.pump, application.host,
                                   application.manager, application.runner)
    pins = (application.runtime, application.bootstrap, pump, host, manager, runner,
            application.facade)
    with _locks_guard:
        lock = _locks.setdefault(pump, Lock())
    if not lock.acquire(False):
        return TaskDriveResult(task_id, 'busy')
    ticks = advanced = 0
    started = False

    def result(state, status=''):
        return TaskDriveResult(task_id, state, ticks, advanced, status, started)

    def inspect():
        current = (application.runtime, application.bootstrap, application.pump,
                   application.host, application.manager, application.runner,
                   application.facade)
        if any(a is not b for a, b in zip(current, pins)):
            raise RuntimeUnavailable('Synchronous task runtime ownership changed')
        application._check()
        with pump._condition:
            if pump._thread is not None:
                raise RuntimeUnavailable('Synchronous driver requires a never-threaded pump')
            if pump._active or pump._stoppers:
                return 'busy', None, ''
            if pump._failure:
                return 'unresolved', None, ''
            if pump._stop.is_set():
                return 'stopped', None, ''
        handles = runner.handles()
        if len(handles) > 1 or any(h.task_id != task_id for h in handles):
            raise RuntimeUnavailable('Synchronous driver cannot own unrelated task handles')
        if manager.deadline_owners() or manager.suspension_owners():
            raise RuntimeUnavailable('Synchronous driver does not support retained timed or continuation owners')
        row = manager.lifecycle_snapshot(task_id)
        if type(row.get('timeout_seconds')) is not int or row['timeout_seconds'] != 0:
            raise RuntimeUnavailable('Synchronous driver supports zero-timeout tasks only')
        status = manager.status(task_id)
        handle = handles[0] if handles else None
        if handle is not None and handle.unresolved:
            return 'unresolved', handle, status
        if handle is not None and handle.token.cancelled:
            return 'cancelled', handle, status
        return '', handle, status

    try:
        state, handle, status = inspect()
        if state:
            return result(state, status)
        if handle is None:
            terminal, _, active, retained = manager.retirement_snapshot(task_id)
            if active or retained:
                raise RuntimeUnavailable('Existing task ownership requires reconciliation')
            if status != 'created':
                return result('already_terminal' if terminal in ('done', 'failed', 'timeout') else 'inactive', status)
            row = manager.lifecycle_snapshot(task_id)
            if (row.get('status') != 'created' or row.get('paused') is not False
                    or row.get('queued') is not False or row.get('deleted_at') is not None
                    or row.get('archived_at') is not None):
                raise RuntimeUnavailable('Existing task is not a fresh created task')
            # There is no initialized flag in the Task DTO. The original
            # bootstrap journal checks receipt/graph eligibility on first tick;
            # a rejection after admission must retain its unresolved owner.
            accepted = application.facade.start(task_id)
            if accepted is not True:
                raise RuntimeUnavailable('Task admission did not confirm a new started generation')
            started = True
            state, handle, status = inspect()
            if state:
                return result(state, status)
            if handle is None:
                raise RuntimeUnavailable('Task admission did not retain an owned handle')
        original_handle, original_token = handle, handle.token
        for _ in range(max_ticks):
            state, handle, status = inspect()
            if state:
                return result(state, status)
            if handle is not original_handle or handle.token is not original_token:
                raise RuntimeUnavailable('Original task generation is no longer retained')
            tick = application.tick()
            ticks += 1
            if (any(identifier != task_id for identifier in tick.advanced)
                    or tick.settlement is not None and any(identifier != task_id for identifier in tick.settlement.settled)):
                raise RuntimeUnavailable('Tick observed unrelated task work; reconcile the original application')
            advanced += int(task_id in tick.advanced)
            state, handle, status = inspect()
            if state:
                return result(state, status)
            if handle is not None and (handle is not original_handle or handle.token is not original_token):
                raise RuntimeUnavailable('Original task generation changed during tick')
            if original_token.cancelled:
                return result('cancelled', status)
            settlement = tick.settlement
            if settlement is not None and task_id in settlement.settled:
                terminal, _, active, retained = manager.retirement_snapshot(task_id)
                if (terminal != 'done' or status != 'done' or active or retained
                        or handle is not None or not settlement.all_settled):
                    raise RuntimeUnavailable('Task settlement did not retire original ownership')
                return result('completed', status)
            if tick.busy or settlement is not None and settlement.busy:
                return result('busy', status)
            if tick.stopped:
                return result('stopped', status)
            if handle is None or status != 'running':
                return result('inactive', status)
        return result('tick_limit', status)
    finally:
        lock.release()

"""Explicit cooperative TaskManager Runner adapter for BoundedAgent.

No default provider, gate, tools, bootstrap, sink, scheduler or CLI wiring. The
host must supply pure preflight/bootstrap functions, a configured BoundedAgent,
and a synchronous result sink. Tool/provider/sink callbacks must not leave
untracked background work: all work they own MUST drain before returning.
wait(True) means the worker thread, including the sink, actually terminated.

A completed worker DOES NOT call TaskManager.complete itself: it could finish
before TaskManager.start installs its handle. The host must call
settle_completed(manager.complete) only after the enclosing manager.start (or
create/resume) returned. Settlement verifies thread drain and forwards the exact
CancellationToken; manager.complete is the final generation/cancellation guard.
There is no automatic poller. Persisted task status may remain running until the
host explicitly settles completion; that limitation is preferable to a false
completion/paused claim. Pause cancellation remains owned by TaskManager.

This runs ONE bounded conversation. Main/planner/workers, graph bootstrap,
intent seeding, FIFO queueing, automatic restart/recovery and background execution
remain absent. seed_first_intent is explicitly rejected. Host preflight must
reject all other unsupported profile/scope/feature choices before persistence.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
import json
from threading import Lock, Thread, current_thread

from ..manager import CancellationToken, RuntimeUnavailable, TaskConflict
from .runtime import BoundedAgent, RunResult


@dataclass(frozen=True)
class RunPlan:
    request: Mapping
    metadata: Mapping = field(default_factory=dict)


def _copy_json(value):
    try:
        raw = json.dumps(value, allow_nan=False, ensure_ascii=False)
        raw.encode('utf-8')
        return json.loads(raw)
    except (TypeError, ValueError, UnicodeError):
        raise RuntimeUnavailable('Bootstrap returned non-JSON or malformed data') from None


def _task_id(task):
    value = str(task.get('id', ''))
    if not value.isascii() or not value.isdecimal() or not 0 < int(value) < 2**63:
        raise RuntimeUnavailable('Runner requires a valid task ID')
    return str(int(value))


class Handle:
    """One generation; wait joins the real thread, not just a cancellation flag."""
    def __init__(self, task, token, agent, plan, sink):
        self.task_id, self.token = _task_id(task), token
        self._task, self._agent, self._plan, self._sink = deepcopy(task), agent, plan, sink
        self._result = None
        self._failure = ''
        self._settlement = Lock()
        self._settled = False
        self._thread = Thread(target=self._work, name=f'artex-task-{self.task_id}', daemon=False)

    def _work(self):
        try:
            result = self._agent.run(self._plan.request, token=self.token,
                                     metadata=self._plan.metadata)
            self._result = deepcopy(result)
            try:
                # Includes cancellation/failure outcomes; no lifecycle mutation here.
                self._sink(deepcopy(self._task), self.token, deepcopy(result))
            except Exception:
                self._failure = 'result_persistence_failed'
        except BaseException:
            # The thread never falsely reports successful completion after a crash.
            self._failure = 'runtime_failed'

    def cancel(self, reason='paused_by_user'):
        self.token.cancel(reason)

    def wait(self, timeout=None):
        if timeout is not None and timeout < 0:
            raise ValueError('timeout must be nonnegative')
        if current_thread() is self._thread:
            raise RuntimeUnavailable('Worker cannot wait for its own completion')
        self._thread.join(timeout)
        return not self._thread.is_alive()

    @property
    def result(self):
        return deepcopy(self._result) if not self._thread.is_alive() else None

    @property
    def failure(self):
        return self._failure if not self._thread.is_alive() else ''

    @property
    def terminal_status(self):
        if self._thread.is_alive() or self.token.cancelled:
            return None
        if self._failure or self._result is None:
            return 'failed'
        if self._result.reason == 'cancelled':
            return None
        if self._result.reason == 'completed':
            return 'done'
        return 'timeout' if self._result.reason == 'timeout' else 'failed'


class AgentRunner:
    """Explicit adapter; supplied callbacks are trusted host code, not model tools.

    validate_create(desc, goal, options, seed) -> literal True verifies feature,
    scope and provider prerequisites without writing. readiness(task) -> literal
    True also supports provisional pre-create snapshots. bootstrap(task) -> RunPlan
    is pure and must work on those provisional snapshots. result_sink(task,
    token, RunResult) persists results synchronously, not task lifecycle. It MUST
    NOT call manager.complete or spawn unowned work. Exceptions mark failure.
    """
    def __init__(self, agent=None, *, validate_create=None, readiness=None,
                 bootstrap=None, result_sink=None, max_pending=64):
        if type(max_pending) is not int or max_pending <= 0:
            raise ValueError("max_pending must be a positive integer")
        self.max_pending = max_pending
        self.agent, self._validate_create, self._readiness = agent, validate_create, readiness
        self._bootstrap, self._sink = bootstrap, result_sink
        self._lock = Lock()
        self._handles = {}

    def _configured(self):
        if not isinstance(self.agent, BoundedAgent) or not callable(self.agent.gate):
            raise RuntimeUnavailable('Configured bounded agent and explicit permission gate are required')
        if not all(callable(fn) for fn in (self._validate_create, self._readiness, self._bootstrap, self._sink)):
            raise RuntimeUnavailable('Pure preflight/bootstrap and synchronous result sink are required')

    def _prepare(self, task):
        self._configured()
        if task.get('seed_first_intent'):
            raise RuntimeUnavailable('Graph intent seeding is not implemented')
        snapshot = deepcopy(dict(task))
        if self._readiness(deepcopy(snapshot)) is not True:
            raise RuntimeUnavailable('Runtime prerequisites were not verified')
        plan = self._bootstrap(snapshot)
        if not isinstance(plan, RunPlan) or not isinstance(plan.request, Mapping) or not isinstance(plan.metadata, Mapping):
            raise RuntimeUnavailable('Bootstrap must return a RunPlan')
        request, metadata = _copy_json(dict(plan.request)), _copy_json(dict(plan.metadata))
        from ..providers import Config, serialize
        try:
            # Validate without sending a request or running callbacks.
            serialize(Config('openai', 'preflight-only'), request)
        except Exception:
            raise RuntimeUnavailable('Bootstrap request is invalid') from None
        if len(json.dumps(request).encode('utf-8')) > self.agent.limits.max_request_bytes:
            raise RuntimeUnavailable('Bootstrap request exceeds runtime budget')
        return RunPlan(request, metadata)

    def validate_create(self, description, goal, options, seed_first_intent=False):
        self._configured()
        if seed_first_intent:
            raise RuntimeUnavailable('Graph intent seeding is not implemented')
        if self._validate_create(description, goal, deepcopy(options), seed_first_intent) is not True:
            raise RuntimeUnavailable('Task preflight did not explicitly approve all prerequisites')
        self._prepare({'provisional': True, 'description': description, 'goal': goal,
                       'options': deepcopy(options), 'seed_first_intent': seed_first_intent})

    def ready(self, task):
        self._prepare(task)
        with self._lock:
            if len(self._handles) >= self.max_pending and str(task.get('id', '')) not in self._handles:
                raise RuntimeUnavailable('Runner capacity is full')
        return True

    def start(self, task, token):
        if not isinstance(token, CancellationToken):
            raise RuntimeUnavailable('Runner requires the manager generation CancellationToken')
        token.check()
        key = _task_id(task)
        plan = self._prepare(task)
        token.check()
        with self._lock:
            old = self._handles.get(key)
            if old is not None:
                if old.token is token:
                    return old  # Idempotent duplicate; never launch twice.
                if old._thread.is_alive() or not old.token.cancelled:
                    raise TaskConflict('Prior runner generation has not drained and settled')
                del self._handles[key]  # A drained canceled generation cannot complete.
            if len(self._handles) >= self.max_pending:
                raise RuntimeUnavailable('Runner capacity is full; drain and settle pending generations')
            token.check()
            metadata = dict(plan.metadata)
            # Host bootstrap cannot redirect identity to another task/session.
            metadata['task_id'] = key
            metadata.setdefault('session_id', f'task-{key}')
            handle = Handle(dict(task), token, self.agent, RunPlan(plan.request, metadata), self._sink)
            self._handles[key] = handle
            try:
                handle._thread.start()
            except BaseException:
                # Standard Thread.start failures leave no worker. If an unusual
                # implementation launched before raising, return its owned handle
                # canceled rather than claiming a clean no-live-work failure.
                if handle._thread.ident is not None:
                    token.cancel('runner_start_failed')
                    return handle
                if self._handles.get(key) is handle:
                    del self._handles[key]
                raise
            return handle

    def handles(self):
        """Read-only generation inventory for explicit host draining/diagnostics."""
        with self._lock:
            return tuple(self._handles.values())

    def settle_completed(self, complete):
        """Call ONLY after manager admission returns; never from a worker/sink.

        complete is manager.complete(task_id, exact_token, status). A False return
        leaves the record pending for explicit reconciliation/retry; cancellation
        and generation checks prevent a stale worker completing a newer run.
        """
        if not callable(complete):
            raise ValueError('A generation-aware completion callback is required')
        with self._lock:
            handles = tuple(self._handles.values())
        settled = []
        for handle in handles:
            if current_thread() is handle._thread:
                raise RuntimeUnavailable('Completion cannot be settled from its own worker')
            if handle._thread.is_alive():
                continue
            with handle._settlement:
                if handle._settled:
                    continue
                if not handle.wait(0):
                    continue
                if handle.token.cancelled:
                    # The manager owns pause persistence; do not call complete.
                    accepted = True
                else:
                    status = handle.terminal_status
                    accepted = status is not None and complete(handle.task_id, handle.token, status) is True
                if accepted:
                    handle._settled = True
                    with self._lock:
                        if self._handles.get(handle.task_id) is handle:
                            del self._handles[handle.task_id]
                    settled.append(handle.task_id)
        return settled

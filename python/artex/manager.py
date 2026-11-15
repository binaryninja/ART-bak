"""Synchronized task registry/lifecycle bridge, not an autonomous execution engine.

Grounded in server/manager.go, resolvedTaskStatus, dto.go and task_control.go.
Persistence/category repositories and a cooperative runner are injected. No tools,
providers, threads, listeners or agents are constructed here. HTTP-compatible DTOs
require an explicit metrics/archive enricher; absent metrics are never fabricated.

An explicit optional FIFO coordinator lives in scheduler.py; no scheduler is
started or wired by this registry. Not ported: bootstrap/goal decomposition, task timeout
monitor, automatic recovery/restart, delete barriers/filesystem cleanup, main-chat
cancellation, inherited graph stores and provider selection. A runner must enforce
scope/approvals and cancellation before EVERY side effect and must drain all owned
work before wait() returns True. Cancellation signalling alone is not completion.
A failed runtime launch after commit may leave a persisted paused task; it is not
a transactional rollback of task creation. Inspect the registry before retrying
creation. The manager serializes one-process mutations; database conditional updates remain
necessary for cross-process races. Use one shared manager per repository/runtime.
"""
from __future__ import annotations
from copy import deepcopy
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from threading import Event, RLock
from time import monotonic
from typing import Mapping, Protocol

from .db.task_categories import CategoryTaskNotFound


class RuntimeUnavailable(RuntimeError):
    pass


class TaskConflict(RuntimeError):
    pass


class TaskNotFound(LookupError):
    pass


class CancellationToken:
    """One run generation. The first cancellation cause wins."""
    def __init__(self):
        self._event, self._lock = Event(), RLock()
        self._reason = ''

    def cancel(self, reason='paused_by_user'):
        with self._lock:
            if not self._event.is_set():
                self._reason = reason
                self._event.set()

    @property
    def cancelled(self):
        return self._event.is_set()

    @property
    def reason(self):
        with self._lock:
            return self._reason

    def wait(self, timeout=None):
        return self._event.wait(timeout)

    def check(self):
        if self.cancelled:
            raise TaskConflict('Run cancelled: ' + self.reason)


class RunHandle(Protocol):
    def cancel(self, reason: str) -> None:
        """Idempotently signal all owned work, including current main-agent turn."""
        ...
    def wait(self, timeout: float | None) -> bool:
        """True only after every owned operation has stopped writing/acting."""
        ...


class Runner(Protocol):
    def validate_create(self, description, goal, options, seed_first_intent: bool) -> None:
        """Validate availability, selected profiles/credentials, scope and features.

        Must not execute a task or change persistent state. Raise if unsupported.
        """
        ...
    def ready(self, task: Mapping) -> bool:
        """Accept actual snapshots or pre-create {provisional: True, options: ...}.

        Must not execute anything. Creation preflight must verify provider, graph
        bootstrap and execution prerequisites before any persistent task is made.
        """
        ...
    def start(self, task: Mapping, token: CancellationToken) -> RunHandle:
        """Return an acknowledged running handle or raise with NO live work left.

        Obey token even if cancelled while starting; never silently ignore it.
        """
        ...


@dataclass
class _Run:
    token: CancellationToken = field(default_factory=CancellationToken)
    ready: Event = field(default_factory=Event)
    handle: RunHandle | None = None
    started: bool = False
    uncertain: bool = False


_TERMINAL = {'done', 'failed', 'timeout'}
_METRICS = {'last_activity_unix', 'tokens', 'goals_total', 'goals_met', 'in_flight', 'findings'}
_PUBLIC = {'id','exploration_id','name','category_id','category_name','pinned','pinned_at',
           'description','goal','status','created_at','created_unix','completed_at','completed_unix',
           'paused','queued','llm_profile_id','llm_profile_ids','active_llm_profile_id',
           'llm_failover_state','llm_failover_reason','source_task_ids','company_ids',
           'coverage_enabled','archive_blocked_by_task_id'} | _METRICS
_LIFECYCLE = {'status','paused','queued','queued_at','queue_mode','completed_at','first_run_at','deadline_at'}


def _id(value):
    text = str(value)
    if not text.isascii() or not text.isdecimal():
        raise TaskNotFound('task not found')
    digits = text.lstrip('0') or '0'
    if len(digits) > 19 or not 0 < int(digits) < 2**63:
        raise TaskNotFound('task not found')
    return str(int(digits))


def _record(value):
    if is_dataclass(value):
        value = asdict(value)
    elif hasattr(value, 'to_dict'):
        value = value.to_dict()
    if not isinstance(value, Mapping):
        raise RuntimeUnavailable('Task repository returned an invalid record')
    result = deepcopy(dict(value))
    result['id'] = _id(result['id'])
    return result


def _date(value):
    if value is None or value == '' or value == 0:
        return '', 0
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    elif isinstance(value, (int, float)):
        value = datetime.fromtimestamp(value, timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise RuntimeUnavailable('Task timestamp must include a timezone')
    return value.isoformat(), int(value.timestamp())


class TaskManager:
    def __init__(self, repository, categories=None, runner: Runner | None = None,
                 *, dto_enricher=None, profile_available=None):
        self.repository, self.categories, self.runner = repository, categories, runner
        self.dto_enricher, self.profile_available = dto_enricher, profile_available
        self._lock = RLock()
        self._tasks: dict[str, dict] = {}
        self._runs: dict[str, _Run] = {}
        self._active = ''

    def load_existing(self):
        """Register persisted tasks without launching/resuming any execution."""
        with self._lock:
            rows = [_record(row) for row in self.repository.list()]
            for row in rows:
                self._tasks.setdefault(row['id'], row)
            if not self._active and self._tasks:
                self._active = max(self._tasks, key=lambda k: (_date(self._tasks[k]['created_at'])[1], int(k)))
            return len(rows)

    def _require(self, task_id):
        key = _id(task_id)
        if key not in self._tasks:
            raise TaskNotFound('task not found')
        return key, self._tasks[key]

    def _status(self, key, row):
        if row.get('status') in _TERMINAL:
            return row['status']
        if row.get('queued'):
            return 'queued'
        run = self._runs.get(key)
        if row.get('paused') or (run is not None and run.token.cancelled):
            return 'paused'
        return 'running' if run and run.started else 'created'

    def status(self, task_id):
        with self._lock:
            key, row = self._require(task_id)
            return self._status(key, row)

    def lifecycle_snapshot(self, task_id):
        with self._lock:
            _, row = self._require(task_id)
            return deepcopy(row)

    def scheduler_snapshot_rows(self, limit=10000):
        """Bounded local registry snapshot, not a cross-process DB lease."""
        if type(limit) is not int or limit <= 0:
            raise ValueError('positive snapshot limit required')
        with self._lock:
            return deepcopy(list(self._tasks.values())[:limit]), len(self._tasks) <= limit

    def _dto(self, key, row):
        if self.dto_enricher is None:
            raise RuntimeUnavailable('Task metrics/archive DTO adapter is not configured')
        created, created_unix = _date(row['created_at'])
        completed, completed_unix = _date(row.get('completed_at'))
        pinned, pinned_unix = _date(row.get('pinned_at'))
        dto = {field: deepcopy(row[field]) for field in _PUBLIC - _METRICS if field in row}
        dto.update(id=key, status=self._status(key, row), created_at=created,
                   created_unix=created_unix, completed_at=completed, completed_unix=completed_unix,
                   pinned=bool(pinned_unix), source_task_ids=[str(x) for x in row.get('source_task_ids', [])])
        if pinned:
            dto['pinned_at'] = pinned
        else:
            dto.pop('pinned_at', None)
        dto.setdefault('llm_profile_ids', [])
        dto.setdefault('company_ids', [])
        dto.setdefault('llm_failover_state', '')
        for name in ('category_id', 'category_name', 'llm_profile_id', 'active_llm_profile_id', 'llm_failover_reason'):
            if dto.get(name) is None or dto.get(name) == '':
                dto.pop(name, None)
        enriched = self.dto_enricher(deepcopy(dto))
        if not isinstance(enriched, Mapping) or not _METRICS <= enriched.keys():
            raise RuntimeUnavailable('Task DTO adapter did not supply required metrics')
        # Enrichers cannot override identity, lifecycle, scope or other persisted fields.
        for field in _METRICS | {'archive_blocked_by_task_id'}:
            if field in enriched:
                dto[field] = deepcopy(enriched[field])
        return dto

    def task(self, task_id):
        with self._lock:
            try:
                key, row = self._require(task_id)
            except TaskNotFound:
                return None
            return self._dto(key, row)

    def list_tasks(self):
        with self._lock:
            rows = sorted(self._tasks.items(), key=lambda item: (_date(item[1].get('pinned_at'))[1], int(item[0])), reverse=True)
            return [self._dto(key, row) for key, row in rows]

    def active_task_id(self):
        with self._lock:
            return self._active

    def _runtime_required(self):
        if self.runner is None:
            raise RuntimeUnavailable('Task runner is not configured')
        if not callable(getattr(self.repository, 'apply_admission', None)):
            raise RuntimeUnavailable('Atomic task admission persistence is not configured')

    def create_task(self, description, goal, options=None, *, start=True, seed_first_intent=False):
        if self.dto_enricher is None:
            raise RuntimeUnavailable('Task metrics/archive DTO adapter is not configured')
        if seed_first_intent and not start:
            raise RuntimeUnavailable('Intent seeding requires a configured runner')
        if start:
            self._runtime_required()
            profiles = options.get('llm_profile_ids', []) if isinstance(options, Mapping) else getattr(options, 'llm_profile_ids', [])
            if profiles and self.profile_available is None:
                raise RuntimeUnavailable('LLM profile availability adapter is not configured')
            if any(not self.profile_available(profile) for profile in profiles):
                raise TaskConflict('Selected LLM profile is unavailable')
            self.runner.validate_create(description, goal, options, seed_first_intent)
            if not self.runner.ready({'provisional': True, 'description': description, 'goal': goal,
                                      'options': deepcopy(options), 'seed_first_intent': seed_first_intent}):
                raise RuntimeUnavailable('Task runner is not ready for creation')
        with self._lock:
            row = _record(self.repository.create(description, goal, options))
            self._tasks[row['id']] = row
            self._active = row['id']
        if start:
            self.start(row['id'], seed_first_intent=seed_first_intent)
        return self.task(row['id'])

    def start(self, task_id, *, seed_first_intent=False):
        return self._start(task_id, seed_first_intent=seed_first_intent)

    def start_queued(self, task_id, expected_lifecycle):
        """Local generation-safe queued admission; invoke via AgentHost fence.

        expected_lifecycle is an exact lifecycle_snapshot, not a partial patch.
        DB admission remains status-only CAS: other processes must not mutate this
        registry's tasks concurrently. This does not supply a distributed lease.
        """
        if not isinstance(expected_lifecycle, Mapping):
            raise ValueError('expected lifecycle snapshot required')
        return self._start(task_id, expected_queue=deepcopy(dict(expected_lifecycle)))

    def _start(self, task_id, *, seed_first_intent=False, expected_queue=None):
        self._runtime_required()
        with self._lock:
            key, row = self._require(task_id)
            if key in self._runs:
                if self._runs[key].token.cancelled:
                    raise TaskConflict('Task cancellation is still in progress')
                return False
            if row.get('status') in _TERMINAL or row.get('deleted_at'):
                raise TaskConflict('Terminal/deleted task cannot be admitted')
            if expected_queue is not None:
                if (row != expected_queue or not row.get('queued') or row.get('paused')
                        or row.get('queue_mode') not in ('bootstrap','resume')):
                    raise TaskConflict('Queued lifecycle changed before admission')
            elif row.get('queued'):
                raise TaskConflict('Queued task requires scheduler admission')
            run = _Run()
            self._runs[key] = run
            snapshot = deepcopy(row)
        admitted = False
        try:
            if not self.runner.ready(snapshot):
                raise RuntimeUnavailable('Task runner is not ready')
            with self._lock:
                run.token.check()
                updated = self.repository.apply_admission(int(key), snapshot['status'], 'running', False, '', False)
                if updated is None:
                    raise TaskConflict('Task lifecycle changed before admission')
                self._merge_lifecycle(key, updated)
                admitted = True
                snapshot = deepcopy(self._tasks[key])
            if expected_queue is not None:
                snapshot['admission_mode'] = expected_queue['queue_mode']
            snapshot['seed_first_intent'] = seed_first_intent
            handle = self.runner.start(snapshot, run.token)
            if not callable(getattr(handle, 'cancel', None)) or not callable(getattr(handle, 'wait', None)):
                run.uncertain = True
                raise RuntimeUnavailable('Runner returned an invalid cancellation handle; runtime quarantined')
            with self._lock:
                if self._runs.get(key) is not run:
                    return False
                run.handle = handle
                run.started = not run.token.cancelled
            if run.token.cancelled:
                handle.cancel(run.token.reason)
            return run.started
        except Exception:
            with self._lock:
                if run.uncertain:
                    run.token.cancel('invalid_runner_handle')
                elif run.token.cancelled:
                    # A concurrent pause owns draining/persistence and must retain its gate.
                    pass
                elif admitted:
                    run.token.cancel('start_failed')
                    try:
                        paused = self.repository.apply_pause(int(key))
                        if paused is not None:
                            self._merge_lifecycle(key, paused)
                            self._runs.pop(key, None)
                    except Exception:
                        # Preserve the cancellation gate if compensating persistence fails.
                        pass
                else:
                    self._runs.pop(key, None)
            raise
        finally:
            run.ready.set()

    def _merge_lifecycle(self, key, updated):
        updated = _record(updated)
        if updated['id'] != key:
            raise RuntimeUnavailable('Lifecycle update returned the wrong task')
        for name in _LIFECYCLE:
            if name in updated:
                self._tasks[key][name] = deepcopy(updated[name])

    def pause(self, task_id, *, timeout=5.0):
        if timeout < 0:
            raise ValueError('timeout must be nonnegative')
        with self._lock:
            key, row = self._require(task_id)
            if row.get('status') in _TERMINAL or row.get('paused'):
                raise TaskConflict('Task is terminal or already paused')
            run = self._runs.get(key)
            if run is None:
                run = _Run()
                run.ready.set()
                self._runs[key] = run
            if run.uncertain:
                raise RuntimeUnavailable('Runner state is unknown; manual reconciliation required')
            run.token.cancel('paused_by_user')
        deadline = monotonic() + timeout
        if run:
            if not run.ready.wait(max(0, deadline-monotonic())):
                raise TaskConflict('Runner has not acknowledged startup cancellation')
            if run.handle:
                run.handle.cancel(run.token.reason)
                if not run.handle.wait(max(0, deadline-monotonic())):
                    raise TaskConflict('Runner has not stopped; task remains cancellation-gated')
        with self._lock:
            updated = self.repository.apply_pause(int(key))
            if updated is None:
                raise TaskConflict('Task lifecycle changed before pause')
            self._merge_lifecycle(key, updated)
            if self._runs.get(key) is run:
                self._runs.pop(key, None)
            return {'id': key, 'paused': True, 'queued': False, 'status': 'paused'}

    def control_task(self, task_id, action):
        if action == 'pause':
            return self.pause(task_id)
        if action != 'resume':
            raise ValueError('action must be pause|resume')
        with self._lock:
            key, row = self._require(task_id)
            if not row.get('paused'):
                raise TaskConflict('Task is not paused')
        if not self.start(key):
            raise TaskConflict('Task start was cancelled or already in progress')
        return {'id': key, 'paused': False, 'queued': False, 'status': 'running'}

    def complete(self, task_id, token, status='done'):
        if status not in _TERMINAL:
            raise ValueError('Completion status must be done, failed or timeout')
        with self._lock:
            key, row = self._require(task_id)
            run = self._runs.get(key)
            if run is None or run.token is not token or token.cancelled:
                return False
            if not self.repository.set_terminal_status_guarded(int(key), status):
                return False
            updated = self.repository.get(int(key))
            if updated is None:
                raise TaskConflict('Completed task disappeared')
            self._merge_lifecycle(key, updated)
            self._runs.pop(key, None)
            return True

    def update_metadata(self, task_id, **patch):
        with self._lock:
            key, row = self._require(task_id)
            updated = self.repository.update(int(key), **patch)
            if updated is None:
                raise TaskNotFound('task not found')
            value = _record(updated)
            for name in ('name', 'pinned_at'):
                row[name] = deepcopy(value[name])
            return self._dto(key, row)

    def _categories_required(self):
        if self.categories is None:
            raise RuntimeUnavailable('Category repository is not configured')

    def rename(self, category_id, name):
        self._categories_required()
        with self._lock:
            category = self.categories.rename(category_id, name)
            for row in self._tasks.values():
                if row.get('category_id') == category_id:
                    row['category_name'] = category.name
            return category

    def delete(self, category_id):
        """Delete a CATEGORY, not a task; required TaskCategoryManager protocol."""
        self._categories_required()
        with self._lock:
            removed = self.categories.delete(category_id)
            if removed:
                for row in self._tasks.values():
                    if row.get('category_id') == category_id:
                        row.update(category_id=None, category_name='')
            return removed

    def set_task(self, task_id, category_id):
        self._categories_required()
        with self._lock:
            try:
                key = _id(task_id)
            except TaskNotFound as error:
                raise CategoryTaskNotFound('task not found') from error
            category = self.categories.set_task(int(key), category_id)
            if key in self._tasks:
                self._tasks[key].update(category_id=category_id, category_name=category.name if category else '')
            return category

    def set_tasks(self, task_ids, category_id):
        self._categories_required()
        with self._lock:
            try:
                numeric = [int(_id(value)) for value in task_ids]
            except TaskNotFound as error:
                raise CategoryTaskNotFound('task not found') from error
            updated, category = self.categories.set_tasks(numeric, category_id)
            keys = [str(value) for value in updated]
            for key in keys:
                if key in self._tasks:
                    self._tasks[key].update(category_id=category_id, category_name=category.name if category else '')
            return keys, category

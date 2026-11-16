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


@dataclass(frozen=True)
class DeadlineOwnerSnapshot:
    task_id: str
    state: str


@dataclass(frozen=True, eq=False)
class DeadlineReservation:
    task_id: str


@dataclass(frozen=True, eq=False)
class DeadlineLease:
    task_id: str
    receipt: object = field(repr=False)
    clock: object = field(repr=False)


@dataclass(frozen=True, eq=False)
class DeadlineFreeze:
    """Exact process-local freeze receipt. Never a terminal-write capability."""
    task_id: str


@dataclass(frozen=True, eq=False)
class DeadlineAssessment:
    """Exact journal reservation only; no model/tool or terminal permission."""
    task_id: str
    config_digest: str


@dataclass
class _DeadlineSlot:
    reservation: DeadlineReservation
    token: CancellationToken
    handle: object
    store: object
    repository: object
    executor: object
    guard: object
    receipt: object
    timeout: int
    coordinator: object
    configuration: tuple
    state: str = 'reserved'
    attempted: bool = False
    lease: DeadlineLease | None = None
    freeze: DeadlineFreeze | None = None
    assessment: DeadlineAssessment | None = None


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
        self._deadline_slots: dict[str, _DeadlineSlot] = {}

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

    def owns_generation(self, task_id, token, handle, *, allow_cancelled=False):
        """Read-only process-local installed-generation capability.

        Execution requires an installed, started, uncancelled run and the exact
        running/unpaused/unqueued registry row. Explicit allow_cancelled=True
        only establishes ownership for cleanup/receipt settlement: it permits
        cancelled, paused or terminal lifecycle states, NEVER new execution.
        Both modes reject absent/retired/foreign handles, uncertain runs and
        deleted/archived rows. The row is the local registry, not a DB lease;
        callers still need shared lifetime guards and authoritative SQL checks.
        No repository, provider, handle or host callbacks run under this lock.
        """
        if type(task_id) not in (str,int) or type(allow_cancelled) is not bool:
            return False
        if type(token) is not CancellationToken or handle is None:
            return False
        try:key=_id(task_id)
        except TaskNotFound:return False
        with self._lock:
            row=self._tasks.get(key);run=self._runs.get(key)
            if type(row) is not dict or type(run) is not _Run:
                return False
            if (row.get('id')!=key or row.get('status') not in {'created','running','done','failed','timeout'}
                    or type(row.get('paused')) is not bool or type(row.get('queued')) is not bool
                    or row.get('deleted_at') is not None or row.get('archived_at') is not None
                    or run.token is not token or run.handle is not handle or run.uncertain is not False):
                return False
            if allow_cancelled:
                return True
            deadline=self._deadline_slots.get(key)
            if deadline is not None and deadline.state in ('freezing','drained','assessing','quarantined'):return False
            return (run.started is True and row['status']=='running'
                    and row['paused'] is False and row['queued'] is False
                    and not token._event.is_set())

    def deadline_owners(self):
        """Bounded process-local ownership inventory, not execution permission."""
        with self._lock:
            return tuple(DeadlineOwnerSnapshot(key,slot.state)
                         for key,slot in sorted(self._deadline_slots.items()))

    def _deadline_slot(self,reservation):
        if type(reservation) is not DeadlineReservation:
            raise TaskConflict('Exact deadline reservation required')
        slot=self._deadline_slots.get(reservation.task_id)
        if slot is None or slot.reservation is not reservation:
            raise TaskConflict('Deadline reservation is not owned')
        return slot

    def _deadline_binding(self,slot):
        # Only identity/data checks under manager lock. No handle, host, guard,
        # repository or provider callbacks here (those may acquire other locks).
        store=slot.store;handle=slot.handle
        if (self.repository is not slot.repository or self.repository.executor is not slot.executor
            or store.executor is not slot.executor or store.manager is not self
            or store.handle is not handle or store.receipt is not slot.receipt
            or store.guard is not slot.guard or store._guard is not slot.guard
            or store._executor is not slot.executor or store._manager is not self
            or store._handle is not handle or store._receipt is not slot.receipt
            or handle.token is not slot.token or handle.coordinator is not slot.coordinator
            or store._coordinator is not slot.coordinator or slot.coordinator.guard is not slot.guard
            or slot.coordinator.journal.executor is not slot.executor
            or (handle.model,handle.system,handle.policy_revision,handle.request_id,handle.task_id,handle.exploration_id,handle._configuration[-1])!=slot.configuration
            or handle._task.get('timeout_seconds',0)!=slot.timeout):
            raise TaskConflict('Deadline ownership binding changed')

    def reserve_deadline_owner(self,task_id,token,handle,*,store,timeout_seconds):
        """Reserve retirement before stamp. No clock write or execution grant.

        Positive runtime support remains disabled elsewhere. Only the actual
        prepared-owner store can validate this reservation; SQL authority remains
        its responsibility. No caller-created receipt is accepted as evidence.
        """
        from .db.task_deadline import TaskDeadlineStore
        from .agent.bootstrap_handle import BootstrapHandle
        if type(store) is not TaskDeadlineStore or not callable(getattr(store,'validate_reservation_owner',None)):
            raise RuntimeUnavailable('Owned deadline persistence unavailable')
        if type(timeout_seconds) is not int or not 0<timeout_seconds<=2**31-1:
            raise TaskConflict('Positive bounded deadline required')
        if type(handle) is not BootstrapHandle:raise TaskConflict('Actual bootstrap deadline owner required')
        prepared_timeout=handle.prepared_timeout_seconds  # Outside the registry lock.
        if type(prepared_timeout) is not int or prepared_timeout!=timeout_seconds:
            raise TaskConflict('Deadline differs from original prepared configuration')
        key=_id(task_id)
        with self._lock:
            if key in self._deadline_slots:raise TaskConflict('Deadline ownership already retained')
            if not self.owns_generation(key,token,handle):raise TaskConflict('Installed deadline generation required')
            row=self._tasks[key]
            if row.get('timeout_seconds')!=timeout_seconds:raise TaskConflict('Task deadline configuration changed')
            if (store.manager is not self or store.handle is not handle or store.receipt.task_id!=int(key)
                or store.receipt.exploration_id!=row.get('exploration_id')):
                raise TaskConflict('Deadline store belongs to another generation')
            reservation=DeadlineReservation(key)
            slot=_DeadlineSlot(reservation,token,handle,store,self.repository,
                self.repository.executor,store.guard,store.receipt,timeout_seconds,store._coordinator,
                (handle.model,handle.system,handle.policy_revision,handle.request_id,handle.task_id,handle.exploration_id,prepared_timeout))
            self._deadline_binding(slot)
            if len(self._deadline_slots)>=10000:raise RuntimeUnavailable('Deadline ownership inventory full')
            self._deadline_slots[key]=slot
        try:
            # Handle checks can call back into owns_generation; do not hold _lock.
            if store.validate_reservation_owner() is not True:
                raise TaskConflict('Prepared deadline owner unavailable')
            with self._lock:
                if self._deadline_slot(reservation) is not slot:raise TaskConflict('Deadline reservation changed')
                self._deadline_binding(slot)
                if not self.owns_generation(key,token,handle):raise TaskConflict('Deadline owner cancelled')
                return reservation
        except BaseException:
            self.abort_deadline_reservation(reservation)
            raise

    def mark_deadline_attempted(self,reservation):
        """Call immediately before the first possible SQL stamp effect."""
        with self._lock:
            slot=self._deadline_slot(reservation);self._deadline_binding(slot)
            if slot.state!='reserved' or slot.attempted:raise TaskConflict('Deadline stamp already attempted')
            if not self.owns_generation(reservation.task_id,slot.token,slot.handle):raise TaskConflict('Deadline owner unavailable')
            slot.attempted=True

    def abort_deadline_reservation(self,reservation):
        """Release only proven pre-attempt work. Attempted stamps are retained."""
        with self._lock:
            slot=self._deadline_slot(reservation)
            if slot.state!='reserved' or slot.attempted:raise TaskConflict('Attempted deadline requires reconciliation')
            del self._deadline_slots[reservation.task_id]

    def quarantine_deadline_owner(self,reservation):
        with self._lock:
            slot=self._deadline_slot(reservation)
            slot.state='quarantined'

    def install_deadline_owner(self,reservation,evidence):
        """Consume exact store-owned committed evidence outside manager lock."""
        from .db.task_deadline import DeadlineReceipt
        with self._lock:
            slot=self._deadline_slot(reservation);self._deadline_binding(slot)
            if slot.state!='reserved' or not slot.attempted:raise TaskConflict('No attempted deadline stamp')
            store=slot.store
        try:
            clock=store.consume_stamp_evidence(reservation,evidence)
            r=slot.receipt
            if (type(clock) is not DeadlineReceipt
                or (clock.task_id,clock.exploration_id,clock.run_id,clock.journal_revision,clock.timeout_seconds)
                   !=(r.task_id,r.exploration_id,r.run_id,r.revision,slot.timeout)
                or any(type(v) is not datetime or v.tzinfo is None or v.utcoffset() is None
                       for v in (clock.first_run_at,clock.deadline_at))
                or clock.deadline_at<clock.first_run_at):
                raise TaskConflict('Committed deadline evidence mismatch')
            with self._lock:
                if self._deadline_slot(reservation) is not slot:raise TaskConflict('Deadline reservation changed')
                self._deadline_binding(slot)
                if slot.state!='reserved' or not self.owns_generation(reservation.task_id,slot.token,slot.handle):
                    raise TaskConflict('Deadline installation lost generation')
                lease=DeadlineLease(reservation.task_id,r,clock)
                slot.lease=lease;slot.state='attached'
                return lease
        except BaseException:
            self.quarantine_deadline_owner(reservation)
            raise

    def reserve_deadline_freeze(self,task_id,token,handle,lease):
        """Host-only admission fence, not proof of expiry or terminal authority.

        Requires the full original installed owner, not merely a readable lease.
        No SQL, callback, waiting or cancellation occurs under the registry lock.
        Frozen execution cannot resume until a separately reviewed protocol exists.
        """
        key=_id(task_id)
        with self._lock:
            slot=self._deadline_slots.get(key)
            if slot is None or slot.token is not token or slot.handle is not handle or slot.lease is not lease:
                raise TaskConflict('Exact deadline execution owner required')
            self._deadline_binding(slot)
            if slot.freeze is not None:
                if slot.state not in ('freezing','drained'):raise TaskConflict('Deadline freeze requires reconciliation')
                return slot.freeze
            if not self.validate_deadline_owner(lease):raise TaskConflict('Deadline owner is not freezable')
            slot.freeze=DeadlineFreeze(key);slot.state='freezing'
            return slot.freeze

    def quarantine_deadline_freeze(self,freeze):
        with self._lock:
            slot=self._deadline_slots.get(getattr(freeze,'task_id',None))
            if type(freeze) is not DeadlineFreeze or slot is None or slot.freeze is not freeze:
                raise TaskConflict('Exact freeze receipt required')
            slot.state='quarantined'

    def confirm_deadline_drain(self,freeze,token,handle):
        """Check actual handle drain outside registry lock; retain all ownership.

        A clean drained receipt still cannot commit done/timeout or run a final
        model. Unknown effects remain quarantined; timeout does not release it.
        """
        with self._lock:
            slot=self._deadline_slots.get(getattr(freeze,'task_id',None))
            if type(freeze) is not DeadlineFreeze or slot is None or slot.freeze is not freeze or slot.token is not token or slot.handle is not handle or slot.state not in ('freezing','drained'):
                raise TaskConflict('Exact freeze receipt required')
            self._deadline_binding(slot)
        try:
            drained=handle.wait(0)
            unresolved=getattr(handle,'unresolved',True)
            if type(drained) is not bool or type(unresolved) is not bool:raise TaskConflict('Invalid drain evidence')
            with self._lock:
                if self._deadline_slots.get(freeze.task_id) is not slot or slot.freeze is not freeze:raise TaskConflict('Freeze ownership changed')
                self._deadline_binding(slot)
                if slot.state not in ('freezing','drained'):raise TaskConflict('Freeze state changed')
                if unresolved:
                    slot.state='quarantined';raise TaskConflict('Frozen work requires reconciliation')
                if not drained:return False
                slot.state='drained';return True
        except BaseException:
            with self._lock:
                if self._deadline_slots.get(freeze.task_id) is slot:slot.state='quarantined'
            raise

    def reserve_deadline_assessment(self,freeze,token,handle,lease,config_digest):
        """Reserve one final journal against a clean frozen generation.

        This does not authorize inference, restart, final tools or terminal SQL.
        An actual restricted runner and evidence-bound settlement remain separate.
        """
        if type(config_digest) is not str or len(config_digest)!=64 or any(c not in '0123456789abcdef' for c in config_digest):
            raise ValueError('Canonical final configuration digest required')
        with self._lock:
            slot=self._deadline_slots.get(getattr(freeze,'task_id',None))
            if type(freeze) is not DeadlineFreeze or slot is None or slot.freeze is not freeze or slot.token is not token or slot.handle is not handle or slot.lease is not lease:
                raise TaskConflict('Exact drained deadline owner required')
            self._deadline_binding(slot)
            if slot.assessment is not None:
                if slot.state=='assessing' and slot.assessment.config_digest==config_digest:return slot.assessment
                raise TaskConflict('Existing assessment requires reconciliation')
            if slot.state!='drained':raise TaskConflict('Clean frozen drain required')
            slot.assessment=DeadlineAssessment(freeze.task_id,config_digest);slot.state='assessing'
            return slot.assessment

    def validate_deadline_assessment(self,assessment,freeze,token,handle,lease,config_digest,*,inspection=False):
        """Validate exact retained journal identity, never execution authority."""
        if type(inspection) is not bool or type(assessment) is not DeadlineAssessment:return False
        with self._lock:
            slot=self._deadline_slots.get(assessment.task_id)
            if slot is None or slot.assessment is not assessment or slot.freeze is not freeze or slot.token is not token or slot.handle is not handle or slot.lease is not lease or assessment.config_digest!=config_digest:
                return False
            if slot.state not in (('assessing','quarantined') if inspection else ('assessing',)):return False
            try:self._deadline_binding(slot)
            except (TaskConflict,AttributeError):return False
            row=self._tasks.get(assessment.task_id)
            return row is not None and row.get('deleted_at') is None and row.get('archived_at') is None and row.get('status') not in _TERMINAL

    def validate_deadline_owner(self,lease,operation='read'):
        """Lease-only read capability; never permits providers/tools/completion.

        Durable task/journal/clock checks under the original guard remain mandatory.
        No final-assessment or generic initialized-task operation exists yet.
        """
        if type(lease) is not DeadlineLease or operation!='read':return False
        with self._lock:
            slot=self._deadline_slots.get(lease.task_id)
            if slot is None or slot.lease is not lease or slot.state not in ('attached','suspended'):return False
            try:self._deadline_binding(slot)
            except (TaskConflict,AttributeError):return False
            row=self._tasks.get(lease.task_id)
            if row is None or row.get('deleted_at') is not None or row.get('archived_at') is not None or row.get('status') in _TERMINAL:return False
            if slot.state=='attached':return self.owns_generation(lease.task_id,slot.token,slot.handle)
            return (row.get('paused') is True and lease.task_id not in self._runs
                    and slot.token._event.is_set())

    def deadline_owner_phase(self,lease):
        """Read-only state for the exact lease; no execution or DB authority."""
        with self._lock:
            if not self.validate_deadline_owner(lease):
                raise TaskConflict('Deadline lease is not currently readable')
            return self._deadline_slots[lease.task_id].state

    def acknowledge_deadline_retirement(self,task_id,token,handle):
        """Pure retirement fence; cancelled owners cannot create new reservations."""
        try:key=_id(task_id)
        except TaskNotFound:return False
        with self._lock:
            slot=self._deadline_slots.get(key)
            if slot is None:return True
            if slot.token is not token or slot.handle is not handle:return False
            try:self._deadline_binding(slot)
            except (TaskConflict,AttributeError):return False
            return slot.state=='suspended' and key not in self._runs

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
        if type(seed_first_intent) is not bool:
            raise ValueError('seed_first_intent must be boolean')
        self._runtime_required()
        with self._lock:
            key, row = self._require(task_id)
            if key in self._deadline_slots:
                raise TaskConflict('Retained deadline requires explicit resume reconciliation')
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
        # Preflight sees the same requested run features as start, before any
        # durable admission. Do not let a callback mutate the captured CAS row.
        snapshot['seed_first_intent'] = seed_first_intent
        if expected_queue is not None:
            snapshot['admission_mode'] = expected_queue['queue_mode']
        admitted = False
        try:
            if not self.runner.ready(deepcopy(snapshot)):
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
            deadline_slot=self._deadline_slots.get(key)
            if deadline_slot is not None and deadline_slot.state!='attached':
                raise TaskConflict('Deadline reservation or uncertainty prevents pause retirement')
            run = self._runs.get(key)
            if run is None:
                run = _Run()
                run.ready.set()
                self._runs[key] = run
            if run.uncertain:
                raise RuntimeUnavailable('Runner state is unknown; manual reconciliation required')
            if deadline_slot is not None:
                self._deadline_binding(deadline_slot)
                if deadline_slot.token is not run.token or deadline_slot.handle is not run.handle:
                    raise TaskConflict('Deadline attachment differs from paused generation')
            run.token.cancel('paused_by_user')
        deadline = monotonic() + timeout
        if run:
            if not run.ready.wait(max(0, deadline-monotonic())):
                raise TaskConflict('Runner has not acknowledged startup cancellation')
            if run.handle:
                run.handle.cancel(run.token.reason)
                if not run.handle.wait(max(0, deadline-monotonic())):
                    raise TaskConflict('Runner has not stopped; task remains cancellation-gated')
        # The cancelled run remains installed as a retirement/admission gate.
        # Do not hold the registry lock while SQL waits for a task row: a lease
        # reader may hold that row and need the registry lock to revalidate.
        with self._lock:
            if self._runs.get(key) is not run:
                raise TaskConflict('Paused generation changed before persistence')
            if deadline_slot is not None:
                if self._deadline_slots.get(key) is not deadline_slot or deadline_slot.state!='attached':
                    raise TaskConflict('Deadline owner changed before pause persistence')
                self._deadline_binding(deadline_slot)
        try:
            updated = self.repository.apply_pause(int(key))
            if updated is None:
                raise TaskConflict('Task lifecycle changed before pause')
            with self._lock:
                if self._runs.get(key) is not run:
                    raise TaskConflict('Paused generation changed after persistence')
                self._merge_lifecycle(key, updated)
                if deadline_slot is not None:
                    if self._deadline_slots.get(key) is not deadline_slot:
                        raise TaskConflict('Deadline owner changed during pause')
                    self._deadline_binding(deadline_slot)
                    if deadline_slot.state!='attached':raise TaskConflict('Deadline owner no longer attachable')
                    deadline_slot.state='suspended'
                self._runs.pop(key, None)
                return {'id': key, 'paused': True, 'queued': False, 'status': 'paused'}
        except BaseException:
            with self._lock:
                if deadline_slot is not None:deadline_slot.state='quarantined'
            raise

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
            if key in self._deadline_slots:
                return False  # No deadline terminal-CAS authority is implemented yet.
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

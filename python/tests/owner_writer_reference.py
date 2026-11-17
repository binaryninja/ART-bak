"""TEST-ONLY participating-writer model, never ART runtime authority.

This owner governs only its own memory and explicitly routed calls. Original
stores and direct executors can bypass it. It must not be installed in a runtime,
application, provider, manager or operator policy. No credential/profile setup,
real SQL, production lock or lifetime protocol is supplied here.

Order: reference barrier -> revision invalidation -> original writer -> original
transaction exit (if any) -> reference exit. write() itself adds no transaction.
Admitted failures permanently poison this owner, even validation/known rollback;
this deliberately conservative model has no reset/reconciliation. Snapshot
revision never rolls back. Agent metadata invalidation is a chosen fixture policy,
not a claim that enabled/profile bindings or runtime configuration changed.

Cancellation signals independently of the barrier. Checks are cooperative:
waiting acquisition is bounded but not promptly interrupted, and an in-flight
Query is not preempted. Late cancellation refuses success without claiming rows
were undone. Guard and write body exceptions propagate without post-check masking.
Signals can race cooperative checks/admission; there is no atomic guarantee that
no Query can occur after a cancellation signal.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
import math
from threading import Event, Lock, RLock, get_ident

from artex.db.agents import AgentStore
from artex.db.core import Query
from artex.db.profiles import ProfileStore


class ReferenceStale(RuntimeError): pass
class ReferenceCancelled(RuntimeError): pass
class ReferenceUncertain(RuntimeError): pass
class ReferenceTimeout(TimeoutError): pass


@dataclass(frozen=True, repr=False)
class ReferenceSnapshot:
    owner_identity: object
    revision: int


@dataclass
class AcquisitionProbe:
    attempted: Event = field(default_factory=Event)
    contended: Event = field(default_factory=Event)
    acquired: Event = field(default_factory=Event)


@dataclass(frozen=True)
class QueryEvent:
    kind: str
    query: Query | None
    thread_id: int
    depth: int


class RecordingExecutor:
    """Only the three allowed original mutation SQL forms, with synthetic rows.

    profiles maps int -> default bool; agents maps key -> (name, description,
    builtin bool), with None representing SQL NULL description. No credentials
    exist. before_mutation(query) and
    after_mutation(query) are optional explicit test hooks. events contains
    QueryEvent entries for execute/mutation/begin/commit/rollback/commit_uncertain.
    Mutation is logged after the actual in-memory row operation, even if a later
    synthetic transaction rollback restores it. Read rows only after synchronization.

    A body failure restores that transaction depth's row snapshot. commit_error
    at commit_error_depth (default 1) leaves possibly changed rows visible and
    propagates the configured exception: it deliberately models uncertainty,
    not real PostgreSQL commit/rollback/savepoint semantics.
    """
    CLEAR_DEFAULT = 'UPDATE llm_profiles SET is_default=false WHERE is_default'
    SET_DEFAULT = 'UPDATE llm_profiles SET is_default=true WHERE id=$1'
    AGENT_METADATA = "UPDATE agents SET name=$2, description=NULLIF($3,'') WHERE key=$1 AND builtin=false"

    def __init__(self, *, profiles, agents):
        self.profiles, self.agents = deepcopy(profiles), deepcopy(agents)
        self.events = []
        self.before_mutation = self.after_mutation = None
        self.commit_error = None
        self.commit_error_depth = 1
        self._lock = RLock()
        self._depth = 0

    def _record(self, kind, query=None):
        self.events.append(QueryEvent(kind, query, get_ident(), self._depth))

    @contextmanager
    def transaction(self):
        with self._lock:
            saved = deepcopy((self.profiles, self.agents))
            self._depth += 1
            self._record('begin')
            try:
                try:
                    yield self
                except BaseException:
                    self.profiles, self.agents = saved
                    self._record('rollback')
                    raise
                else:
                    if self.commit_error is not None and self._depth == self.commit_error_depth:
                        self._record('commit_uncertain')
                        raise self.commit_error
                    self._record('commit')
            finally:
                self._depth -= 1

    def execute(self, query):
        if type(query) is not Query:
            raise TypeError('Original Query required by test executor')
        with self._lock:
            if query.sql not in (self.CLEAR_DEFAULT, self.SET_DEFAULT, self.AGENT_METADATA):
                raise AssertionError('Unrecognized SQL outside test-only writer scope')
            self._record('execute', query)
            if self.before_mutation is not None:
                self.before_mutation(query)
            if query.sql == self.CLEAR_DEFAULT:
                assert query.params == ()
                count = sum(value is True for value in self.profiles.values())
                self.profiles = {key: False for key in self.profiles}
            elif query.sql == self.SET_DEFAULT:
                assert len(query.params) == 1
                identifier, = query.params
                count = int(identifier in self.profiles)
                if count:
                    self.profiles[identifier] = True
            else:
                assert len(query.params) == 3
                identifier, name, description = query.params
                row = self.agents.get(identifier)
                count = int(row is not None and row[2] is False)
                if count:
                    self.agents[identifier] = (name, None if description == '' else description, False)
            self._record('mutation', query)
            if self.after_mutation is not None:
                self.after_mutation(query)
            return count

    def fetch_one(self, query):
        raise AssertionError('Reads outside test-only mutation fixture')

    def fetch_all(self, query):
        raise AssertionError('Reads outside test-only mutation fixture')


class ReferenceOwner:
    """A new test owner of memory only, with no production issuance/provenance.

    Foreign/stale/cancelled/uncertain admission and acquisition timeout do not
    change revision. Once admitted, each writer increments revision before SQL.
    Nested writers require a newly captured/yielded snapshot. current() is false
    throughout writing; a read guard cannot enter from within a writer. Any
    admitted failure is sticky, including an inner failure caught by outer code.
    """
    def __init__(self, executor):
        if type(executor) is not RecordingExecutor:
            raise TypeError('Exact inert RecordingExecutor required')
        self.executor = executor
        self.profiles = ProfileStore(executor)
        self.agents = AgentStore(executor)
        self._identity = object()
        self._barrier = RLock()
        self._state = Lock()
        self._cancelled = Event()
        self._revision = 0
        self._uncertain = False
        self._write_depth = 0
        self._writer_thread = None

    @property
    def revision(self):
        with self._state:
            return self._revision

    @property
    def uncertain(self):
        with self._state:
            return self._uncertain

    @property
    def cancelled(self):
        return self._cancelled.is_set()

    def cancel(self):
        self._cancelled.set()

    def snapshot(self):
        with self._state:
            return ReferenceSnapshot(self._identity, self._revision)

    def _owner(self, snapshot):
        if (type(snapshot) is not ReferenceSnapshot or snapshot.owner_identity is not self._identity
                or type(snapshot.revision) is not int):
            raise ReferenceStale('Foreign reference-owner snapshot')

    def _status_locked(self):
        if self._cancelled.is_set():
            raise ReferenceCancelled('Reference cancellation is sticky')
        if self._uncertain:
            raise ReferenceUncertain('Reference failure requires a different fixture owner')

    def _check(self, snapshot, *, writer=False):
        self._owner(snapshot)
        with self._state:
            self._status_locked()
            if snapshot.revision != self._revision:
                raise ReferenceStale('Reference revision changed')
            if self._write_depth and (not writer or self._writer_thread != get_ident()):
                raise ReferenceStale('Reference write is still active')

    def current(self, snapshot):
        try:
            self._check(snapshot)
        except (ReferenceStale, ReferenceCancelled, ReferenceUncertain):
            return False
        return True

    def _acquire(self, timeout, probe):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 5:
            raise ValueError('Explicit wait bound must be between zero and five seconds')
        if probe is not None and type(probe) is not AcquisitionProbe:
            raise TypeError('Exact AcquisitionProbe required')
        if probe is not None:
            probe.attempted.set()
        acquired = self._barrier.acquire(blocking=False)
        if not acquired:
            if probe is not None:
                probe.contended.set()
            acquired = self._barrier.acquire(timeout=timeout)
        if not acquired:
            if self._cancelled.is_set():
                raise ReferenceCancelled('Cancelled while waiting for reference barrier')
            raise ReferenceTimeout('Reference barrier wait expired')
        if probe is not None:
            probe.acquired.set()

    @contextmanager
    def guard(self, snapshot, *, timeout=1.0, probe=None):
        self._owner(snapshot)
        self._acquire(timeout, probe)
        try:
            self._check(snapshot)
            yield snapshot
            self._check(snapshot)
        finally:
            self._barrier.release()

    @contextmanager
    def write(self, snapshot, *, timeout=1.0, probe=None):
        self._owner(snapshot)
        self._acquire(timeout, probe)
        admitted = False
        try:
            self._check(snapshot, writer=True)
            with self._state:
                self._revision += 1
                self._write_depth += 1
                self._writer_thread = get_ident()
                admitted = True
            try:
                yield self.snapshot()
                with self._state:
                    self._status_locked()
            except BaseException:
                with self._state:
                    self._uncertain = True
                raise
        finally:
            if admitted:
                with self._state:
                    self._write_depth -= 1
                    if not self._write_depth:
                        self._writer_thread = None
            self._barrier.release()

    def set_active(self, snapshot, identifier, *, timeout=1.0, probe=None):
        with self.write(snapshot, timeout=timeout, probe=probe):
            return self.profiles.set_active(identifier)

    def update_metadata(self, snapshot, identifier, name, description='', *, timeout=1.0, probe=None):
        with self.write(snapshot, timeout=timeout, probe=probe):
            return self.agents.update_metadata(identifier, name, description)

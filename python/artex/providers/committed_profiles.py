"""Opt-in profile mutation with an owned commit before runtime publication.

This is a storage/publication mechanism for an already authorized host, not a
task owner, durable receipt or barrier enrolling other writers. The original
ProfileCoordinator and application defaults are unchanged. Existing provider
references and the factory's legacy cache are neither cleared nor revoked.

One nonblocking operation gate covers preflight, mutation and publication.
Busy/reentrant calls fail immediately. The separate publication lock never
spans SQL or provider construction. Preparation runs outside executor scopes
(the factory may hold its own lock); transport factories must remain inert and
must not acquire unrelated resources in a conflicting order. Executor/driver
waits retain the host's timeout policy; no universal cancellation bound is added.

Save and selection first use an owned transaction containing only reads, then
prepare the candidate, then recheck it in a separate owned mutation transaction.
The recheck is not a SQL compare-and-swap or an atomic snapshot across external
writers, other coordinators or isolation levels. The host must keep the supplied
store/executor/factory bindings stable and coordinate nonparticipating writers.
The observed active/pool rows are captured before commit, not reread afterward.

Publication is unavailable throughout an operation and after any failed one.
An explicit reload can reconcile later; a mutation is never replayed here.
After confirmed mutation commit, preparation failure leaves committed_pending;
ordinary exceptions become pending_error, while BaseException is propagated.
For ProfileService compatibility the host can explicitly supply its existing
server.profiles.ProfileApplyPending class. No server or application is imported.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, fields
from threading import Lock

from .factory import ApplyPending, ProfileFactory, public_snapshot
from ..db.configuration_ownership import assert_bound
from ..db.core import int64
from ..db.pool import ExecutorPool
from ..db.postgres import PostgresExecutor
from ..db.transaction_coordination import TransactionCoordinationBinding, coordinated_transaction
from ..db.profiles import (
    LLMProfile, ProfileInvalid, ProfileNotFound, ProfileStorageError,
    ProfileStore, RetryOverride, _validate,
)


class ProfileMutationBusy(RuntimeError):
    """This coordinator already has an operation in progress; no retry occurs."""


class CommittedProfileApplyPending(ApplyPending):
    """Mutation commit was acknowledged; this coordinator has no applied pool."""

    def __init__(self, message, *, operation=None, profile_id=None):
        super().__init__(message)
        self.operation = operation
        self.profile_id = profile_id
        self.committed = True
        self.runtime_applied = False


@dataclass(frozen=True)
class ProfilePublicationStatus:
    """Safe process-local observation; committed describes mutation, not reads.

    None means a mutation was attempted without an acknowledged outer commit;
    it does not assert rollback. No error text or private profile is retained.
    """

    operation: str | None
    phase: str
    profile_id: int | None
    committed: bool | None
    runtime_applied: bool


@dataclass(frozen=True)
class _Publication:
    pool: object
    snapshot: dict
    status: ProfilePublicationStatus


class CommittedProfileCoordinator:
    def __init__(self, store, factory, *, pending_error=CommittedProfileApplyPending,
                 transaction_coordination=None):
        if type(store) is not ProfileStore or type(factory) is not ProfileFactory:
            raise ProfileInvalid('original profile store and factory required')
        executor = store.executor
        if type(executor) not in (PostgresExecutor, ExecutorPool):
            raise ProfileInvalid('original committed-transaction executor required')
        if not all(callable(getattr(value, name, None)) for value, name in (
            (executor, 'committed_transaction'), (factory, 'prepare'),
            (factory, 'prepare_pool'),
        )):
            raise ProfileInvalid('committed profile prerequisites required')
        if not isinstance(pending_error, type) or not issubclass(pending_error, RuntimeError):
            raise ProfileInvalid('pending error must be a RuntimeError class')
        self._store, self._factory, self._executor = store, factory, executor
        self._transaction_coordination = transaction_coordination
        self._original_transaction_coordination = transaction_coordination
        self._bindings()
        self._pending_error = pending_error
        self._operation_gate, self._publication_lock = Lock(), Lock()
        self._publication = _Publication(None, public_snapshot(),
            ProfilePublicationStatus(None, 'unconfigured', None, False, False))

    def runtime(self):
        """Return this publication and a safe copy; old external refs survive."""
        with self._publication_lock:
            publication = self._publication
            return publication.pool, deepcopy(publication.snapshot)

    def snapshot(self):
        return self.runtime()[1]

    def status(self):
        with self._publication_lock:
            return self._publication.status

    def _bindings(self):
        if self._store.executor is not self._executor:
            raise ProfileInvalid('profile executor binding changed')
        binding = self._coordination_binding()
        if binding is not None:
            if type(binding) is not TransactionCoordinationBinding or binding.executor is not self._executor:
                raise ProfileInvalid('coordination binding must match the original profile executor')
            assert_bound(self._executor, binding.owner)
            assert_bound(self._factory, binding.owner)

    def _coordination_binding(self):
        binding = self._original_transaction_coordination
        if self._transaction_coordination is not binding:
            raise ProfileInvalid('profile transaction coordination binding changed')
        return binding

    def _transaction(self, operation, mode):
        binding = self._coordination_binding()
        if binding is None:
            return self._executor.committed_transaction()
        return coordinated_transaction(binding, operation=operation, mode=mode)

    def _phase(self, phase, *, profile_id=None, committed=False):
        publication = _Publication(None, public_snapshot(), ProfilePublicationStatus(
            self.status().operation, phase, profile_id, committed, False))
        with self._publication_lock:
            self._publication = publication

    @contextmanager
    def _operation(self, name):
        if not self._operation_gate.acquire(blocking=False):
            raise ProfileMutationBusy('profile operation already in progress')
        try:
            publication = _Publication(None, public_snapshot(),
                ProfilePublicationStatus(name, 'in_progress', None, False, False))
            with self._publication_lock:
                self._publication = publication
            try:
                self._bindings()
                yield
            except BaseException:
                if self.status().phase == 'in_progress':
                    self._phase('not_committed')
                raise
        finally:
            self._operation_gate.release()

    def _capture(self):
        self._bindings()
        active, profiles = self._store.active(), self._store.pool()
        self._bindings()
        if active is not None:
            matches = [p for p in profiles if p.id == active.id and p.is_default]
            if len(matches) != 1 or matches[0] != active:
                raise ProfileInvalid('inconsistent active profile snapshot')
        elif any(p.is_default for p in profiles):
            raise ProfileInvalid('inconsistent active profile snapshot')
        return deepcopy(active), deepcopy(profiles)

    def _publish(self, active, profiles, *, committed, profile_id=None):
        try:
            self._bindings()
            pool = self._factory.prepare_pool(profiles)
            snapshot = public_snapshot(active)
            self._bindings()
        except Exception:
            if not committed:
                raise
            message = 'profile persisted but runtime application is incomplete'
            if self._pending_error is CommittedProfileApplyPending:
                raise CommittedProfileApplyPending(
                    message, operation=self.status().operation, profile_id=profile_id) from None
            raise self._pending_error(message) from None
        publication = _Publication(pool, snapshot, ProfilePublicationStatus(
            self.status().operation, 'applied', profile_id, committed, True))
        # One record swap; no SQL, callbacks or construction under this lock.
        with self._publication_lock:
            self._publication = publication

    def reload(self):
        """Explicit reconciliation; no profile mutation or automatic replay."""
        with self._operation('reload'):
            with self._transaction('profile.reconcile', 'read'):
                active, profiles = self._capture()
            self._publish(active, profiles, committed=False)

    @staticmethod
    def _identifier(value):
        if type(value) is not int or not 0 < value < 2**63:
            raise ProfileStorageError('invalid persisted profile identifier')
        return value

    @staticmethod
    def _expected_saved(candidate, previous, identifier):
        # The store accepts LLMProfile subclasses but its private projection
        # returns the base dataclass. Compare only fields it actually persists.
        expected = LLMProfile(**{f.name: deepcopy(getattr(candidate, f.name))
                                 for f in fields(LLMProfile)})
        expected.id = identifier
        # Original ProfileStore.save does not modify active selection and its
        # private projection omits hints. Match its documented normalizations.
        expected.is_default = previous.is_default if previous is not None else False
        expected.api_key_hint = ''
        expected.retry = RetryOverride(*(getattr(candidate.retry, name).clamped()
                                         for name in ('connect', 'empty', 'stream')))
        if expected.format != 'openai':
            expected.max_tokens_field = ''
        return expected

    def save(self, profile):
        with self._operation('save'):
            requested = deepcopy(profile)
            _validate(requested)
            candidate = deepcopy(requested)
            # Owned read preflight also refuses caller-owned transactions before
            # transport construction, including an otherwise read-free create.
            with self._transaction('profile.save', 'write'):
                previous = self._store.get(requested.id) if requested.id else None
                self._bindings()
                if requested.id and previous is None:
                    raise ProfileNotFound('profile not found')
                if previous is not None and not candidate.api_key:
                    candidate.api_key = previous.api_key
            self._factory.prepare(candidate)
            self._bindings()
            with self._transaction('profile.save', 'write'):
                self._bindings()
                if requested.id and self._store.get(requested.id) != previous:
                    raise ProfileInvalid('profile changed during preparation')
                self._phase('outcome_unknown', profile_id=requested.id or None, committed=None)
                identifier = self._identifier(self._store.save(requested))
                if requested.id and identifier != requested.id:
                    raise ProfileStorageError('persisted profile identifier changed')
                if self._store.get(identifier) != self._expected_saved(candidate, previous, identifier):
                    raise ProfileStorageError('saved profile read-back did not match')
                active, profiles = self._capture()
            self._phase('committed_pending', profile_id=identifier, committed=True)
            self._publish(active, profiles, committed=True, profile_id=identifier)
            return identifier

    def set_active(self, identifier):
        with self._operation('set_active'):
            identifier = int64(identifier, 'profile id')
            if identifier <= 0:
                raise ProfileInvalid('profile id must be positive')
            with self._transaction('profile.set_active', 'write'):
                previous = self._store.get(identifier)
                self._bindings()
                if previous is None:
                    raise ProfileNotFound('profile not found')
            self._factory.prepare(previous)
            self._bindings()
            with self._transaction('profile.set_active', 'write'):
                self._bindings()
                if self._store.get(identifier) != previous:
                    raise ProfileInvalid('profile changed during preparation')
                self._phase('outcome_unknown', profile_id=identifier, committed=None)
                self._store.set_active(identifier)
                expected = deepcopy(previous)
                expected.is_default = True
                if self._store.get(identifier) != expected:
                    raise ProfileStorageError('active profile read-back did not match')
                active, profiles = self._capture()
                if active != expected:
                    raise ProfileStorageError('active selection read-back did not match')
            self._phase('committed_pending', profile_id=identifier, committed=True)
            self._publish(active, profiles, committed=True, profile_id=identifier)

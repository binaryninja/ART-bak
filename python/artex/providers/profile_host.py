"""Explicit host policy around one exclusively owned committed coordinator.

The caller supplies substantive decisions and the shared-writer guard. This
adapter neither creates task authority nor enrolls direct store/coordinator,
SQL or cross-process callers. The coordinator and its resources must remain
exclusively owned through this boundary; an observed status is not a durable
operation receipt. Existing external provider references are not revoked.

The nonblocking host gate precedes callbacks. The owner guard spans committed
coordinator work and publication, but MUST NOT open an outer DB transaction:
the coordinator owns its transaction boundaries. Currentness/authorization
callbacks run before and within the guard; currentness also runs after exit.
They must respect the owner's declared lock/reentry order. This does not add
bounded cancellation or interrupt an already-running DB/provider operation.

Host publication is separate from coordinator publication. Admission clears it;
only successful guard exit and final checks publish a safe observation. Failed
operations never expose an underlying applied snapshot as host success. A
confirmed mutation can remain committed_pending; an uncertain one must not be
replayed. Passive observations do not grant authority or perform reconciliation.
Ordinary failures after a confirmed mutation use the coordinator's pinned
pending_error contract with the original cause. A body BaseException control
outcome retains identity even if guard exit raises an ordinary Exception; that
exit error is retained as its explicit cause. Other distinct exit errors keep
their usual precedence. Reconciliation never claims a persisted mutation. Binding pins
detect replacement, not hostile Python memory access or mutable callback state.
Non-Exception status-observation interruptions also propagate, including after
a delegate error (retained as context). Until status is safely captured, a
mutation attempt is unknown; interruption does not prove it failed to commit.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from threading import Lock
from types import MappingProxyType

from ..db.core import int64
from ..db.pool import ExecutorPool
from ..db.postgres import PostgresExecutor
from ..db.profiles import LLMProfile, ProfileInvalid, ProfileStore
from .committed_profiles import (
    CommittedProfileApplyPending, CommittedProfileCoordinator,
    ProfileMutationBusy, ProfilePublicationStatus,
)
from .factory import ProfileFactory, public_snapshot


# Canonical functions, captured from the original classes rather than accepting
# whatever delegate an instance happens to expose during host composition.
_METHODS = {
    kind: {name: getattr(kind, name) for name in names}
    for kind, names in (
        (CommittedProfileCoordinator, ('save', 'set_active', 'reload', 'status', 'snapshot')),
        (ProfileFactory, ('prepare', 'prepare_pool')),
        (ProfileStore, ('get', 'save', 'set_active', 'active', 'pool')),
    )
}


class ProfileHostDenied(RuntimeError):
    """The supplied owner did not authorize this operation."""


class ProfileHostStale(RuntimeError):
    """The supplied owner no longer considers this policy current."""


class ProfileHostUnavailable(RuntimeError):
    """Pinned host resources or the explicit guard contract are unavailable."""


class ProfileHostBusy(ProfileMutationBusy):
    """Another operation owns this host; no waiting or retry is attempted."""


@dataclass(frozen=True, repr=False)
class ProfileHostResources:
    """Exact resource association, without callbacks or authority issuance."""

    executor: object
    factory: ProfileFactory

    def __post_init__(self):
        if type(self.executor) not in (PostgresExecutor, ExecutorPool) or type(self.factory) is not ProfileFactory:
            raise TypeError('Exact original executor and profile factory required')


@dataclass(frozen=True, kw_only=True, repr=False)
class ProfileHostPolicy:
    """Required host decisions, not authority manufactured by a frozen wrapper.

    current(revision) and authorize(operation, value, revision) require literal
    True. value is a private copy of the save profile, an activation ID, or None
    for reconcile. guard(operation, revision) returns the original owner's
    non-suppressing context manager. Callback identity does not freeze captured
    policy state; the owner must version and coordinate its actual decisions.
    """

    revision: str
    current: Callable
    authorize: Callable
    guard: Callable
    resources: ProfileHostResources | None = None

    def __post_init__(self):
        if type(self.revision) is not str or not 1 <= len(self.revision) <= 128 or '\0' in self.revision:
            raise ValueError('Explicit bounded host policy revision required')
        if any(not callable(value) for value in (self.current, self.authorize, self.guard)):
            raise TypeError('Explicit host policy callbacks required')
        if self.resources is not None:
            if type(self.resources) is not ProfileHostResources:
                raise TypeError('Exact profile host resource association required')
            ProfileHostResources.__post_init__(self.resources)


@dataclass(frozen=True)
class ProfileHostReceipt:
    """Safe local observation; not a grant or durable operation receipt.

    runtime_applied describes this host's available publication. An underlying
    coordinator may have applied while this host subsequently withheld it.
    """

    status: ProfilePublicationStatus
    snapshot: Mapping[str, object]


class ProfileHost:
    def __init__(self, coordinator, *, policy):
        if type(coordinator) is not CommittedProfileCoordinator:
            raise TypeError('Exact committed profile coordinator required')
        if type(policy) is not ProfileHostPolicy:
            raise TypeError('Exact explicit profile host policy required')
        # Revalidate even a policy changed through unsupported frozen-object
        # mutation before composition. This invokes no authority callback.
        ProfileHostPolicy.__post_init__(policy)
        self._coordinator, self._policy = coordinator, policy
        self._store, self._factory, self._executor = (
            coordinator._store, coordinator._factory, coordinator._executor)
        if (type(self._store) is not ProfileStore or type(self._factory) is not ProfileFactory
                or type(self._executor) not in (PostgresExecutor, ExecutorPool)
                or self._store.executor is not self._executor):
            raise TypeError('Exact original profile resources on one executor required')
        if policy.resources is not None and (policy.resources.executor is not self._executor
                                             or policy.resources.factory is not self._factory):
            raise TypeError('Policy resources must match the original coordinator')
        self._policy_pins = (policy, policy.revision, policy.current, policy.authorize, policy.guard,
                             policy.resources)
        self._coordinator_pins = (coordinator, self._store, self._factory, self._executor,
                                 coordinator._pending_error, coordinator._operation_gate,
                                 coordinator._publication_lock)
        self._factory_names = ('_transport_factory', 'health', '_clock', '_sleep',
                               '_retry_max_attempts', '_retry_max_seconds', '_lock')
        self._factory_pins = tuple(getattr(self._factory, name) for name in self._factory_names)
        pins = []
        for obj in (coordinator, self._factory, self._store):
            for name, function in _METHODS[type(obj)].items():
                method = getattr(obj, name, None)
                if getattr(method, '__self__', None) is not obj or getattr(method, '__func__', None) is not function:
                    raise TypeError('Original bound profile methods required')
                pins.append((obj, name, function))
        self._method_pins = tuple(pins)
        self._gate, self._publication_lock = Lock(), Lock()
        self._publication = self._receipt(ProfilePublicationStatus(None, 'unconfigured', None, False, False))
        self.validate_bindings()

    @staticmethod
    def _receipt(status, snapshot=None):
        return ProfileHostReceipt(status, MappingProxyType(deepcopy(
            public_snapshot() if snapshot is None else snapshot)))

    def _check_pins(self):
        try:
            self._check_present_pins()
        except AttributeError:
            raise ProfileHostUnavailable('Profile host binding unavailable') from None

    def _check_present_pins(self):
        c, p = self._coordinator, self._policy
        if (any(a is not b for a, b in zip(
                (p, p.revision, p.current, p.authorize, p.guard, p.resources), self._policy_pins))
                or (p.resources is not None and
                    (p.resources.executor is not self._executor or p.resources.factory is not self._factory))
                or any(a is not b for a, b in zip(
                    (c, c._store, c._factory, c._executor, c._pending_error,
                     c._operation_gate, c._publication_lock), self._coordinator_pins))
                or any(a is not b for a, b in zip(
                    (self._store, self._factory, self._executor), self._coordinator_pins[1:4]))
                or self._store.executor is not self._executor
                or any(getattr(self._factory, name) is not value
                       for name, value in zip(self._factory_names, self._factory_pins))):
            raise ProfileHostUnavailable('Profile host binding changed')
        for obj, name, function in self._method_pins:
            method = getattr(obj, name)
            if getattr(method, '__self__', None) is not obj or getattr(method, '__func__', None) is not function:
                raise ProfileHostUnavailable('Profile host method binding changed')

    def _set(self, receipt):
        with self._publication_lock:
            self._publication = receipt

    def validate_bindings(self):
        """Inert identity validation; no authority, DB or provider calls."""
        try:
            self._check_pins()
        except BaseException:
            with self._publication_lock:
                s = self._publication.status
                phase = 'committed_pending' if s.committed is True else 'outcome_unknown' if s.committed is None else 'not_committed'
                self._publication = self._receipt(ProfilePublicationStatus(
                    s.operation, phase, s.profile_id, s.committed, False))
            raise

    def snapshot(self):
        self.validate_bindings()
        with self._publication_lock:
            return deepcopy(dict(self._publication.snapshot))

    def status(self):
        self.validate_bindings()
        with self._publication_lock:
            return self._publication.status

    def _current(self):
        self._check_pins()
        current = self._policy.current(self._policy.revision)
        self._check_pins()
        if current is not True:
            raise ProfileHostStale('Profile host policy is stale')

    def _admit(self, operation, value):
        self._current()
        decision_value = deepcopy(value)
        allowed = self._policy.authorize(operation, decision_value, self._policy.revision)
        self._check_pins()
        if decision_value != value:
            raise ProfileHostUnavailable('Profile authorization input changed')
        if allowed is not True:
            raise ProfileHostDenied('Profile host operation denied')
        self._current()

    def _observe(self, operation, error=None):
        # Busy is raised before the coordinator admits this attempt. Its status
        # may describe another operation and must never be borrowed here.
        if not isinstance(error, ProfileMutationBusy):
            try:
                self._check_pins()
                s = self._coordinator.status()
                expected = 'reload' if operation == 'reconcile' else operation
                if type(s) is ProfilePublicationStatus and s.operation == expected:
                    return s
            except Exception:
                pass
            return ProfilePublicationStatus(operation, 'outcome_unknown', None,
                                            None if operation != 'reconcile' else False, False)
        return ProfilePublicationStatus(operation, 'not_committed', None, False, False)

    def _failed(self, operation, outcome):
        # outcome is captured at the delegate's return/exception, before owner
        # exit callbacks. Never reread another coordinator attempt here.
        committed, identifier = outcome.committed, outcome.profile_id
        phase = 'committed_pending' if committed is True else 'outcome_unknown' if committed is None else 'not_committed'
        self._set(self._receipt(ProfilePublicationStatus(operation, phase, identifier, committed, False)))

    def _run(self, operation, value):
        if not self._gate.acquire(blocking=False):
            raise ProfileHostBusy('Profile host operation already in progress')
        outcome = ProfilePublicationStatus(operation, 'not_committed', None, False, False)
        body_error = None
        try:
            self._set(self._receipt(ProfilePublicationStatus(operation, 'in_progress', None, False, False)))
            value = deepcopy(value)
            if operation == 'save' and not isinstance(value, LLMProfile):
                raise ProfileInvalid('Profile value required')
            if operation == 'set_active':
                value = int64(value, 'profile id')
                if value <= 0:
                    raise ProfileInvalid('Positive profile id required')
            self._admit(operation, value)
            guard = self._policy.guard(operation, self._policy.revision)
            self._check_pins()
            if not callable(getattr(guard, '__enter__', None)) or not callable(getattr(guard, '__exit__', None)):
                raise ProfileHostUnavailable('Explicit host guard required')
            with guard:
                try:
                    self._admit(operation, value)
                    # Calling the mutation may commit before its status can be
                    # observed. Only positive no-admission evidence (busy) or a
                    # safely captured outcome can narrow this uncertainty.
                    outcome = ProfilePublicationStatus(operation, 'outcome_unknown', None,
                        None if operation != 'reconcile' else False, False)
                    try:
                        if operation == 'save':
                            result = self._coordinator.save(value)
                        elif operation == 'set_active':
                            result = self._coordinator.set_active(value)
                        else:
                            result = self._coordinator.reload()
                    except BaseException as error:
                        outcome = self._observe(operation, error)
                        raise
                    outcome = self._observe(operation)
                    self._current()
                    s = self._coordinator.status()
                    expected = 'reload' if operation == 'reconcile' else operation
                    if s is not outcome or s.operation != expected or not s.runtime_applied:
                        raise ProfileHostUnavailable('Profile publication observation unavailable')
                    receipt = self._receipt(ProfilePublicationStatus(
                        operation, s.phase, s.profile_id, s.committed, s.runtime_applied), self._coordinator.snapshot())
                    self._check_pins()
                except BaseException as error:
                    body_error = error
                    raise
            if body_error is not None:
                # Owner guards must not suppress failures. Preserve the exact
                # original exception if a supplied context nevertheless does.
                raise body_error
            self._current()
            if self._coordinator.status() is not outcome:
                raise ProfileHostUnavailable('Profile coordinator publication changed')
            self._set(receipt)
            return receipt if operation == 'reconcile' else result
        except BaseException as error:
            self._failed(operation, outcome)
            if (body_error is not None and not isinstance(body_error, Exception)
                    and isinstance(error, Exception)):
                # An ordinary cleanup failure must not replace cancellation or
                # other body control flow. Preserve the exit failure explicitly
                # without converting the original control into pending_error.
                raise body_error from error
            if operation != 'reconcile' and outcome.committed is True and isinstance(error, Exception):
                # Existing ProfileService must not disguise a confirmed commit
                # as a generic failed mutation. Use the pinned per-attempt
                # pending contract, never a later mutable status observation.
                pending = self._coordinator_pins[4]
                if not isinstance(error, pending):
                    message = 'profile persisted but host publication is incomplete'
                    if pending is CommittedProfileApplyPending:
                        raise pending(message, operation=operation,
                                      profile_id=outcome.profile_id) from error
                    raise pending(message) from error
            raise
        finally:
            self._gate.release()

    def save(self, profile):
        return self._run('save', profile)

    def set_active(self, identifier):
        return self._run('set_active', identifier)

    def reconcile(self):
        """Explicit guarded reload, never replaying a profile mutation."""
        return self._run('reconcile', None)

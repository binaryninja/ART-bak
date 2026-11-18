"""Stateful, explicitly granted process-local configuration ownership.

This owns one serial configuration domain and its issued participant handles.
It is not task/run authority, a database commit receipt, or a fence for direct
SQL, other executors/processes, retained providers or hostile Python mutation.
Capabilities are stable grants within this owner lifetime; separate snapshots
are generation-stamped observations. An outer writer advances generation at
admission, including failed/no-SQL attempts, without claiming commit/rollback.

Lock order is owner work barrier -> registered executor -> registered factory.
Outer admission checks caller-owned resource locks/transactions before waiting
and after reserving the barrier. Entry hooks only verify an already-held lease.
The independent state condition is never held across user work or blocking
resource acquisition. Revocation wakes waiters and refuses subsequent access;
entered scopes retain the barrier until their real exit. Drain never closes or
forcibly releases caller resources. No owner lock belongs around network I/O.
"""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
import math
from threading import Condition, Lock, get_ident, local
import time
import weakref


class ConfigurationOwnershipError(RuntimeError):
    pass


class ConfigurationUnavailable(ConfigurationOwnershipError):
    pass


class ConfigurationDenied(ConfigurationOwnershipError):
    pass


class ConfigurationStale(ConfigurationOwnershipError):
    pass


class ConfigurationBusy(ConfigurationOwnershipError):
    pass


class ConfigurationNestingError(ConfigurationOwnershipError):
    pass


class ConfigurationTransactionConflict(ConfigurationOwnershipError):
    pass


def _name(value):
    if type(value) is not str or not 1 <= len(value) <= 128 or '\0' in value or value == '*':
        raise ValueError('Explicit bounded configuration name required')
    return value


def _timeout(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('Finite nonnegative timeout required')
    return float(value)


def _mode(value):
    if type(value) is not str or value not in ('read', 'write'):
        raise ValueError('Explicit read or write mode required')
    return value


@dataclass(frozen=True, kw_only=True)
class ConfigurationPermissions:
    read: frozenset[str]
    write: frozenset[str]

    def __post_init__(self):
        for values in (self.read, self.write):
            if type(values) is not frozenset or len(values) > 128:
                raise ValueError('Explicit bounded operation frozensets required')
            for value in values:
                _name(value)


@dataclass(frozen=True, eq=False, repr=False)
class ConfigurationCapability:
    """Only exact handles present in the issuing owner's registry have rights."""

    _owner: weakref.ReferenceType
    participant: str


@dataclass(frozen=True, eq=False, repr=False)
class ConfigurationSnapshot:
    """Only exact owner-issued observations are accepted; copies grant nothing."""

    capability: ConfigurationCapability
    generation: int


@dataclass(frozen=True, eq=False, repr=False)
class ConfigurationLease:
    capability: ConfigurationCapability
    operation: str
    mode: str
    snapshot: ConfigurationSnapshot
    _owner: weakref.ReferenceType
    _thread: int
    _token: object


@dataclass(frozen=True)
class ConfigurationDrain:
    drained: bool
    active: int
    generation: int
    closed: bool


@dataclass
class _Grant:
    participant: str
    permissions: ConfigurationPermissions
    revoked: bool = False


_THREAD = local()


def _stack():
    if not hasattr(_THREAD, 'stack'):
        _THREAD.stack = []
    return _THREAD.stack


class ConfigurationOwner:
    def __init__(self, domain, *, participants):
        self._domain = _name(domain)
        if not isinstance(participants, Mapping) or not 1 <= len(participants) <= 128:
            raise ValueError('Explicit bounded participant policy required')
        policy = {}
        for name, permissions in participants.items():
            _name(name)
            if type(permissions) is not ConfigurationPermissions:
                raise TypeError('Exact configuration permissions required')
            ConfigurationPermissions.__post_init__(permissions)
            policy[name] = ConfigurationPermissions(read=permissions.read, write=permissions.write)
        self._policy = policy
        self._state, self._work = Condition(), Lock()
        self._issued, self._snapshots = weakref.WeakKeyDictionary(), weakref.WeakKeyDictionary()
        self._resources = []
        self._leases = set()
        self._active = None
        self._reserved_thread = None
        self._reserved_mode = None
        self._generation, self._closed = 0, False

    @property
    def domain(self):
        return self._domain

    def _grant(self, capability, *, allow_revoked=False):
        if self._closed:
            raise ConfigurationUnavailable('Configuration owner is closed')
        if type(capability) is not ConfigurationCapability:
            raise ConfigurationDenied('Foreign configuration capability')
        grant = self._issued.get(capability)
        if (grant is None or type(capability._owner) is not weakref.ReferenceType
                or capability._owner() is not self or capability.participant != grant.participant):
            raise ConfigurationDenied('Unknown configuration capability')
        if grant.revoked and not allow_revoked:
            raise ConfigurationDenied('Configuration capability revoked')
        return grant

    def _snapshot_check(self, capability, snapshot):
        if type(snapshot) is not ConfigurationSnapshot or snapshot.capability is not capability:
            raise ConfigurationStale('Foreign configuration observation')
        record = self._snapshots.get(snapshot)
        if (record is None or record[0]() is not capability or type(snapshot.generation) is not int
                or record[1] != snapshot.generation or snapshot.generation != self._generation):
            raise ConfigurationStale('Configuration observation is stale or unknown')

    def _new_snapshot(self, capability):
        snapshot = ConfigurationSnapshot(capability, self._generation)
        self._snapshots[snapshot] = (weakref.ref(capability), self._generation)
        return snapshot

    def _permitted(self, capability, operation, mode):
        grant = self._grant(capability)
        if operation not in getattr(grant.permissions, mode):
            raise ConfigurationDenied('Configuration operation not granted')

    def issue(self, participant):
        _name(participant)
        with self._state:
            if self._closed:
                raise ConfigurationUnavailable('Configuration owner is closed')
            if participant not in self._policy:
                raise ConfigurationDenied('Unknown configuration participant')
            capability = ConfigurationCapability(weakref.ref(self), participant)
            self._issued[capability] = _Grant(participant, self._policy[participant])
            return capability

    def permits(self, capability, operation, *, mode):
        _name(operation); _mode(mode)
        with self._state:
            try:
                self._permitted(capability, operation, mode)
                return True
            except ConfigurationOwnershipError:
                return False

    def snapshot(self, capability):
        with self._state:
            self._grant(capability)
            if self._reserved_mode == 'write' and self._reserved_thread != get_ident():
                raise ConfigurationBusy('Configuration writer is in progress')
            return self._new_snapshot(capability)

    def current(self, handle):
        with self._state:
            try:
                capability = handle.capability if type(handle) is ConfigurationSnapshot else handle
                self._grant(capability)
                if type(handle) is ConfigurationSnapshot:
                    self._snapshot_check(capability, handle)
                    if self._reserved_mode == 'write' and self._reserved_thread != get_ident():
                        return False
                return True
            except ConfigurationOwnershipError:
                return False

    def _register(self, resource, kind, lock):
        """Called under registry lock; only short owner state follows it."""
        with self._state:
            if self._closed:
                raise ConfigurationUnavailable('Cannot bind a closed configuration owner')
            if self._reserved_thread is not None:
                raise ConfigurationUnavailable('Cannot extend an active configuration domain')
            if not any(ref() is resource for ref, _, _ in self._resources):
                self._resources.append((weakref.ref(resource), kind, lock))

    def _assert_live(self):
        with self._state:
            if self._closed:
                raise ConfigurationUnavailable('Configuration owner is closed')

    def _ambient_check(self):
        # Import here avoids a store/factory import cycle. This performs only
        # exact-resource attribute/lock-ownership checks, never SQL or callbacks.
        from .db.configuration_ownership import _assert_admission_order
        with self._state:
            resources = tuple(self._resources)
        _assert_admission_order(resources)

    def _lease_check(self, lease):
        self._grant(lease.capability)
        if (lease not in self._leases or lease._owner() is not self or lease._thread != get_ident()
                or self._active is None or lease._token is not self._active._token):
            raise ConfigurationDenied('No active configuration admission')
        self._snapshot_check(lease.capability, lease.snapshot)

    def _require(self, operation, mode):
        _name(operation); _mode(mode)
        stack = _stack()
        if not stack or stack[-1]._owner() is not self:
            raise ConfigurationDenied('Matching configuration guard required')
        lease = stack[-1]
        with self._state:
            self._lease_check(lease)
            self._permitted(lease.capability, operation, mode)
            if mode == 'write' and (lease.mode != 'write' or lease.operation != operation):
                raise ConfigurationDenied('Matching admitted writer operation required')

    @contextmanager
    def guard(self, capability, operation, *, mode, expected, timeout):
        _name(operation); _mode(mode); timeout = _timeout(timeout)
        stack = _stack()
        if stack:
            parent = stack[-1]
            if parent._owner() is not self or parent.capability is not capability:
                raise ConfigurationNestingError('Cross-owner or capability nesting refused')
            if parent.mode == 'read' and mode == 'write':
                raise ConfigurationNestingError('Reader to writer upgrade refused')
            with self._state:
                self._lease_check(parent)
                self._permitted(capability, operation, mode)
                self._snapshot_check(capability, expected)
                lease = ConfigurationLease(capability, operation, mode, self._new_snapshot(capability),
                                           weakref.ref(self), get_ident(), parent._token)
                self._leases.add(lease)
            stack.append(lease)
            try:
                yield lease
                with self._state:
                    self._lease_check(lease)
            finally:
                stack.pop()
                with self._state:
                    self._leases.discard(lease)
            return

        self._ambient_check()
        deadline = time.monotonic() + timeout
        with self._state:
            while True:
                self._permitted(capability, operation, mode)
                self._snapshot_check(capability, expected)
                # Nonblocking work-lock attempt under short state lock. A wait
                # releases state; revoke/close can wake and refuse the waiter.
                if self._work.acquire(blocking=False):
                    self._reserved_thread, self._reserved_mode = get_ident(), mode
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ConfigurationBusy('Configuration admission timed out')
                self._state.wait(remaining)
        lease = None
        try:
            self._ambient_check()
            with self._state:
                self._permitted(capability, operation, mode)
                self._snapshot_check(capability, expected)
                if mode == 'write':
                    self._generation += 1
                lease = ConfigurationLease(capability, operation, mode, self._new_snapshot(capability),
                                           weakref.ref(self), get_ident(), object())
                self._active = lease
                self._leases.add(lease)
            stack.append(lease)
            try:
                yield lease
                with self._state:
                    self._lease_check(lease)
            finally:
                stack.pop()
        finally:
            with self._state:
                if lease is not None:
                    self._leases.discard(lease)
                self._active = None
                self._reserved_thread = self._reserved_mode = None
                self._work.release()
                self._state.notify_all()

    def revoke(self, capability):
        with self._state:
            grant = self._grant(capability, allow_revoked=True)
            grant.revoked = True
            self._state.notify_all()

    def close(self):
        with self._state:
            self._closed = True
            self._state.notify_all()

    def drain(self, *, timeout):
        """Wait only for existing admissions; revoke/close first to stop new ones."""
        deadline = time.monotonic() + _timeout(timeout)
        with self._state:
            while self._reserved_thread is not None:
                remaining = deadline - time.monotonic()
                if self._reserved_thread == get_ident() or remaining <= 0:
                    return ConfigurationDrain(False, 1, self._generation, self._closed)
                self._state.wait(remaining)
            return ConfigurationDrain(True, 0, self._generation, self._closed)

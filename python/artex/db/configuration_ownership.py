"""Immutable process-local resource enrollment with fail-closed tombstones.

Registration covers every eligible store using this exact executor and the
exact factory, including instances created later. Unbound legacy resources are
unchanged. A dead/closed owner never silently unbinds a surviving resource.
No unregister/rebind escape is supplied. Weak keys permit collection of dead
resources, not reuse of their authority by another object with the same id.
Enroll at caller-established quiescence before legacy consumers start. Binding
cannot retroactively fence an unbound operation already in flight; refusing to
extend an active owned domain does not prove that legacy callers are quiescent.
This is not a hot-enrollment or global-writer migration mechanism.

These checks do not intercept raw connections, direct SQL, other executors or
processes. Selected entrypoints must call them before original SQL/locks and
again at their publication boundaries. They never acquire the owner work lock.
"""
from threading import Condition, RLock
import weakref

from ..configuration_owner import (
    ConfigurationOwner, ConfigurationUnavailable, ConfigurationTransactionConflict,
)


_REGISTRY_LOCK = RLock()
_REGISTRY = weakref.WeakKeyDictionary()
_RLOCK_TYPE = type(RLock())


def _kind(resource):
    # Lazy imports avoid the ownership hooks importing their caller mid-import.
    from .postgres import PostgresExecutor
    from .pool import ExecutorPool
    from ..providers.factory import ProfileFactory
    if type(resource) is PostgresExecutor:
        return 'postgres'
    if type(resource) is ExecutorPool:
        return 'pool'
    if type(resource) is ProfileFactory:
        return 'factory'
    raise TypeError('Exact original configuration resource required')


def _bind(resource, owner, allowed):
    if type(owner) is not ConfigurationOwner:
        raise TypeError('Exact configuration owner required')
    kind = _kind(resource)
    if kind not in allowed:
        raise TypeError('Wrong configuration resource kind')
    lock = resource._condition if kind == 'pool' else resource._lock
    if (kind == 'pool' and (type(lock) is not Condition or type(lock._lock) is not _RLOCK_TYPE)
            or kind != 'pool' and type(lock) is not _RLOCK_TYPE):
        raise ConfigurationUnavailable('Original resource lock ownership inspection required')
    with _REGISTRY_LOCK:
        binding = _REGISTRY.get(resource)
        if binding is not None:
            if binding[0]() is not owner or binding[1] != kind or binding[2] is not lock:
                raise ConfigurationUnavailable('Configuration resource cannot be rebound')
            owner._assert_live()
            return
        owner._register(resource, kind, lock)
        _REGISTRY[resource] = (weakref.ref(owner), kind, lock)


def bind_executor(executor, owner):
    _bind(executor, owner, ('postgres', 'pool'))


def bind_factory(factory, owner):
    _bind(factory, owner, ('factory',))


def _binding(resource):
    with _REGISTRY_LOCK:
        try:
            return _REGISTRY.get(resource)
        except TypeError:
            # Legacy executors can be unhashable/non-weakrefable; registration
            # only accepts the exact original weak-referenceable resource types.
            return None


def is_bound(resource):
    return _binding(resource) is not None


def is_bound_executor(executor):
    binding = _binding(executor)
    return binding is not None and binding[1] in ('postgres', 'pool')


def is_bound_factory(factory):
    binding = _binding(factory)
    return binding is not None and binding[1] == 'factory'


def bound_owner(resource):
    binding = _binding(resource)
    if binding is None:
        return None
    owner = binding[0]()
    if owner is None:
        raise ConfigurationUnavailable('Configuration resource owner expired')
    lock = resource._condition if binding[1] == 'pool' else resource._lock
    if lock is not binding[2]:
        raise ConfigurationUnavailable('Configuration resource lock changed')
    owner._assert_live()
    return owner


def assert_bound(resource, owner):
    if type(owner) is not ConfigurationOwner or bound_owner(resource) is not owner:
        raise ConfigurationUnavailable('Exact configuration owner binding required')


def require_read(resource, operation):
    owner = bound_owner(resource)
    if owner is not None:
        owner._require(operation, 'read')


def require_write(resource, operation):
    owner = bound_owner(resource)
    if owner is not None:
        owner._require(operation, 'write')


def _owned(lock):
    probe = getattr(lock, '_is_owned', None)
    if not callable(probe):
        raise ConfigurationUnavailable('Resource lock ownership inspection unavailable')
    result = probe()
    if type(result) is not bool:
        raise ConfigurationUnavailable('Invalid resource lock ownership observation')
    return result


def _assert_admission_order(resources):
    for ref, kind, lock in resources:
        resource = ref()
        if resource is None:
            continue
        actual_lock = resource._condition if kind == 'pool' else resource._lock
        if actual_lock is not lock:
            raise ConfigurationUnavailable('Configuration resource lock changed')
        if kind == 'pool':
            if getattr(resource._local, 'lease', None) is not None or _owned(lock):
                raise ConfigurationTransactionConflict('Owner admission inside pool lease/lock refused')
        elif kind == 'factory':
            if _owned(lock):
                raise ConfigurationTransactionConflict('Owner admission inside factory lock refused')
        else:
            owned = _owned(lock)
            if owned:
                raise ConfigurationTransactionConflict('Owner admission inside executor lock refused')
            # Another thread's structured adapter transaction is covered by its
            # own depth. Do not call it this caller's ambient transaction; the
            # owner barrier handles enrolled contention. Raw connection access
            # and callers on other executors remain outside this mechanism.
            if not resource._depth and resource._connection.info.transaction_status != 0:
                raise ConfigurationTransactionConflict('Ambient driver transaction refused')

"""Explicit configuration-owner integration for the opt-in ProfileHost.

This bridges configuration rights, not task/run or provider-request authority.
Both resources must already be enrolled with the supplied owner. The value
authorizer remains an explicit substantive host decision; operation permission
alone never approves a profile value.

Each thread starts with the caller's exact initial observation and adopts only
the ticket from its own admitted guard. Another thread (even using this bridge)
cannot refresh an earlier host's post-exit freshness check. After an external
generation change, explicitly obtain a new observation and construct a new
bridge. There is no automatic observation or capability refresh.
"""
from contextlib import contextmanager
import math
from threading import local

from ..configuration_owner import (
    ConfigurationOwner, ConfigurationCapability, ConfigurationSnapshot,
    ConfigurationDenied, ConfigurationStale,
)
from ..db.configuration_ownership import assert_bound
from .profile_host import ProfileHostPolicy, ProfileHostResources


_OPERATIONS = {
    'save': ('profile.save', 'write'),
    'set_active': ('profile.set_active', 'write'),
    'reconcile': ('profile.reconcile', 'read'),
}


class OwnedProfilePolicy:
    """Required bindings for one explicit observation and substantive policy.

    ``authorize_value(operation, value, revision)`` must return literal True.
    Its exceptions propagate. The reviewed ProfileHost supplies its defensive
    value copy, repeats the decision inside the owner guard, and performs final
    currentness checks. Calling this adapter alone does not perform that flow.
    The owner serializes only enrolled participating operations in this process;
    retained providers, direct SQL and other executors/processes remain outside.
    """

    def __init__(self, owner, capability, *, snapshot, executor, factory,
                 revision, authorize_value, timeout):
        if type(owner) is not ConfigurationOwner:
            raise TypeError('Exact configuration owner required')
        if type(capability) is not ConfigurationCapability:
            raise TypeError('Exact issued configuration capability required')
        if type(snapshot) is not ConfigurationSnapshot or snapshot.capability is not capability:
            raise TypeError('Explicit observation for the supplied capability required')
        if not callable(authorize_value):
            raise TypeError('Explicit substantive profile value authorizer required')
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError('Explicit finite nonnegative guard timeout required')
        self._owner, self._capability, self._initial = owner, capability, snapshot
        self._executor, self._factory = executor, factory
        self._revision, self._authorize_value, self._timeout = revision, authorize_value, timeout
        self._observations = local()
        self._policy = ProfileHostPolicy(revision=revision, current=self.current,
                                         authorize=self.authorize, guard=self.guard,
                                         resources=ProfileHostResources(executor, factory))
        self._check_bindings()
        if not owner.current(snapshot):
            raise ConfigurationStale('Initial configuration observation is stale')

    @property
    def policy(self):
        return self._policy

    def _check_bindings(self):
        assert_bound(self._executor, self._owner)
        assert_bound(self._factory, self._owner)

    def _observation(self):
        return getattr(self._observations, 'snapshot', self._initial)

    def current(self, revision):
        if type(revision) is not str or revision != self._revision:
            return False
        self._check_bindings()
        return (self._owner.current(self._capability)
                and self._owner.current(self._observation()))

    def authorize(self, operation, value, revision):
        if type(operation) is not str or operation not in _OPERATIONS or not self.current(revision):
            return False
        key, mode = _OPERATIONS[operation]
        if not self._owner.permits(self._capability, key, mode=mode):
            return False
        allowed = self._authorize_value(operation, value, revision)
        return allowed is True and self.current(revision)

    @contextmanager
    def guard(self, operation, revision):
        if type(operation) is not str or operation not in _OPERATIONS:
            raise ConfigurationDenied('Unknown profile operation')
        if not self.current(revision):
            raise ConfigurationStale('Profile configuration observation is stale')
        key, mode = _OPERATIONS[operation]
        with self._owner.guard(self._capability, key, mode=mode,
                               expected=self._observation(), timeout=self._timeout) as lease:
            # Adoption is local to this thread and this actual admission, even
            # if the body later fails. Generation is not a SQL commit receipt.
            self._observations.snapshot = lease.snapshot
            yield lease

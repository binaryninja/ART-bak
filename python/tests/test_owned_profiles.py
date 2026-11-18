"""Real owner/store/factory/host objects with the existing injected SQL fixture.

No connection, transport request, application or task is executed. The fixture
SQL projection is bounded test data, not database or deployment evidence.
"""
from dataclasses import replace
import inspect
import sys
from threading import Event, Thread
import unittest

from artex.configuration_owner import (
    ConfigurationOwner, ConfigurationPermissions, ConfigurationOwnershipError,
    ConfigurationStale, ConfigurationUnavailable,
)
from artex.db.configuration_ownership import bind_executor, bind_factory
from artex.db.profiles import ProfileStore, ProfileInvalid, ProfileNotFound, ProfileStorageError
from artex.db.postgres import PostgresExecutor
from artex.providers.factory import ProfileCoordinator
from artex.providers.owned_profiles import OwnedProfilePolicy
from artex.providers.committed_profiles import CommittedProfileCoordinator
from artex.providers.profile_host import (
    ProfileHost, ProfileHostDenied, ProfileHostStale, ProfileHostUnavailable,
    ProfileHostResources,
)
import test_committed_profile_host_contract as fixture


READS = frozenset(('profile.reconcile', 'factory.build', 'factory.pool',
                   'factory.prepare', 'factory.prepare_pool'))
WRITES = frozenset(('profile.save', 'profile.set_active', 'factory.invalidate'))


class OwnedProfilesTests(unittest.TestCase):
    def make(self, *, reads=READS, writes=WRITES, pooled=False):
        f = fixture.CommittedProfileHostContractTests.make(self, pooled=pooled)
        owner = ConfigurationOwner('synthetic-profile-domain', participants={
            'operator': ConfigurationPermissions(read=reads, write=writes)})
        self.addCleanup(owner.close)
        capability = owner.issue('operator')
        observation = owner.snapshot(capability)
        bind_executor(f.executor, owner)
        bind_factory(f.factory, owner)
        return f, owner, capability, observation

    @staticmethod
    def enter(owner, capability, observation, operation, *, mode='write'):
        return owner.guard(capability, operation, mode=mode, expected=observation, timeout=0)

    @staticmethod
    def bridge(f, owner, capability, observation, authorize_value):
        return OwnedProfilePolicy(owner, capability, snapshot=observation,
            executor=f.executor, factory=f.factory, revision='recorded-policy-v1',
            authorize_value=authorize_value, timeout=0)

    def test_unbound_factory_and_original_coordinator_keep_behavior(self):
        f = fixture.CommittedProfileHostContractTests.make(self)
        old = f.factory.build(fixture._profile())
        self.assertIs(f.factory.build(fixture._profile()), old)
        self.assertIsNot(f.factory.prepare(fixture._profile()), old)
        legacy = ProfileCoordinator(f.store, f.factory)
        legacy.reload()
        self.assertTrue(legacy.snapshot()['configured'])
        self.assertEqual(legacy.save(fixture._profile(model='legacy-new')), 1)
        legacy.set_active(2)
        self.assertEqual(f.connection.rows[1].model, 'legacy-new')
        self.assertTrue(f.connection.rows[2].is_default)

    def test_all_store_instances_refuse_before_validation_or_sql(self):
        f, owner, cap, observed = self.make()
        for store in (f.store, ProfileStore(f.executor)):
            for call in (lambda: store.save(None), lambda: store.set_active(True)):
                with self.assertRaises(ConfigurationOwnershipError):
                    call()
        self.assertEqual(f.connection.events, [])
        self.assertEqual(f.connection.mutations, [])

    def test_matching_writer_only_and_original_error_contract(self):
        f, owner, cap, observed = self.make()
        with self.enter(owner, cap, observed, 'profile.save'):
            with self.assertRaises(ProfileInvalid):
                f.store.save(None)
            with self.assertRaises(ConfigurationOwnershipError):
                f.store.set_active(2)
            self.assertEqual(f.connection.events, [])
            self.assertEqual(ProfileStore(f.executor).save(fixture._profile(model='new')), 1)
        self.assertEqual(f.connection.rows[1].model, 'new')
        with self.enter(owner, cap, owner.snapshot(cap), 'profile.set_active'):
            with self.assertRaises(ProfileNotFound):
                f.store.set_active(999)
        self.assertTrue(f.connection.rows[1].is_default)

    def test_reader_cannot_mutate_and_empty_factory_calls_require_access(self):
        f, owner, cap, observed = self.make()
        calls = (lambda: f.factory.build(None), lambda: f.factory.prepare(None),
                 lambda: f.factory.pool([]), lambda: f.factory.prepare_pool([]),
                 lambda: f.factory.invalidate())
        for call in calls:
            with self.assertRaises(ConfigurationOwnershipError):
                call()
        with self.enter(owner, cap, observed, 'profile.reconcile', mode='read'):
            self.assertIsNone(f.factory.pool([]))
            self.assertIsNone(f.factory.prepare_pool([]))
            with self.assertRaises(ConfigurationOwnershipError):
                f.store.set_active(2)
            with self.assertRaises(ConfigurationOwnershipError):
                f.factory.invalidate()
        self.assertEqual(f.connection.events, [])
        self.assertEqual(f.transport.models, [])

    def test_nested_factory_rights_are_explicit_and_checked_before_iteration(self):
        f, owner, cap, observed = self.make(reads=frozenset(('factory.pool',)))
        with self.enter(owner, cap, observed, 'factory.pool', mode='read'):
            with self.assertRaises(ConfigurationOwnershipError):
                f.factory.pool([fixture._profile()])
        self.assertEqual(f.transport.models, [])
        class NoIteration:
            def __iter__(self):
                raise AssertionError('Unowned iterable must not be evaluated')
        with self.assertRaises(ConfigurationOwnershipError):
            f.factory.prepare_pool(NoIteration())

    def test_cache_hit_is_not_unowned_access_and_invalidation_has_own_write_key(self):
        f, owner, cap, observed = self.make()
        with self.enter(owner, cap, observed, 'factory.build', mode='read'):
            provider = f.factory.build(fixture._profile())
            self.assertIs(f.factory.build(fixture._profile()), provider)
        with self.assertRaises(ConfigurationOwnershipError):
            f.factory.build(fixture._profile())
        with self.enter(owner, cap, observed, 'profile.save'):
            with self.assertRaises(ConfigurationOwnershipError):
                f.factory.invalidate()
        self.assertIs(f.factory._cache[1][1], provider)
        with self.enter(owner, cap, owner.snapshot(cap), 'factory.invalidate'):
            f.factory.invalidate()
        self.assertEqual(f.factory._cache, {})

    def test_revocation_during_construction_does_not_publish_cache(self):
        f, owner, cap, observed = self.make()
        def revoke(config):
            owner.revoke(cap)
            return False
        f.transport.refuse = revoke
        with self.assertRaises(ConfigurationOwnershipError):
            with self.enter(owner, cap, observed, 'factory.build', mode='read'):
                f.factory.build(fixture._profile())
        self.assertEqual(f.factory._cache, {})
        self.assertEqual(len(f.transport.models), 1)

    def test_legacy_coordinator_refuses_enrollment_before_any_effect(self):
        f = fixture.CommittedProfileHostContractTests.make(self)
        legacy_before = ProfileCoordinator(f.store, f.factory)
        legacy_before.reload()
        old_pool, old_snapshot = legacy_before.runtime()
        old_cache = dict(f.factory._cache)
        owner = ConfigurationOwner('legacy-refusal', participants={
            'operator': ConfigurationPermissions(read=READS, write=WRITES)})
        self.addCleanup(owner.close)
        cap = owner.issue('operator')
        bind_executor(f.executor, owner)
        bind_factory(f.factory, owner)
        f.connection.events.clear()
        f.transport.models.clear()
        for legacy in (legacy_before, ProfileCoordinator(f.store, f.factory)):
            with self.enter(owner, cap, owner.snapshot(cap), 'profile.save'):
                for call in (lambda: legacy.save(None), lambda: legacy.set_active(None),
                             legacy.reload, legacy._apply):
                    with self.assertRaises(ConfigurationUnavailable):
                        call()
        self.assertEqual(f.connection.events, [])
        self.assertEqual(f.transport.models, [])
        self.assertEqual(f.factory._cache, old_cache)
        self.assertIs(legacy_before.runtime()[0], old_pool)
        self.assertEqual(legacy_before.snapshot(), old_snapshot)

    def test_bridge_requires_explicit_same_owner_bindings_observation_and_value_policy(self):
        f, owner, cap, observed = self.make()
        options = dict(snapshot=observed, executor=f.executor, factory=f.factory,
                       revision='explicit', authorize_value=lambda *args: False, timeout=0)
        for name in options:
            bad = dict(options)
            del bad[name]
            with self.assertRaises(TypeError):
                OwnedProfilePolicy(owner, cap, **bad)
        with self.assertRaises(TypeError):
            OwnedProfilePolicy(owner, cap, **dict(options, authorize_value=None))
        other = fixture.CommittedProfileHostContractTests.make(self)
        with self.assertRaises(ConfigurationOwnershipError):
            OwnedProfilePolicy(owner, cap, **dict(options, factory=other.factory))
        other_cap = owner.issue('operator')
        with self.assertRaises(TypeError):
            OwnedProfilePolicy(owner, other_cap, **options)
        self.assertEqual(f.connection.events, [])

    def test_host_save_activate_reconcile_and_literal_value_decisions(self):
        f, owner, cap, observed = self.make()
        approved = fixture._profile(model='approved-new')
        values = {'save': approved, 'set_active': 2, 'reconcile': None}
        calls = []
        def authorize(operation, value, revision):
            calls.append((operation, value, revision))
            return operation in values and value == values[operation] and revision == 'recorded-policy-v1'
        bridge = self.bridge(f, owner, cap, observed, authorize)
        host = ProfileHost(f.coordinator, policy=bridge.policy)
        self.assertTrue(host.reconcile().snapshot['configured'])
        self.assertEqual(host.save(approved), 1)
        self.assertIsNone(host.set_active(2))
        self.assertTrue(host.snapshot()['configured'])
        self.assertTrue(bridge.current('recorded-policy-v1'))
        self.assertFalse(owner.current(observed))
        self.assertEqual({c[0] for c in calls}, set(values))
        with self.assertRaises(ProfileHostDenied):
            host.save(replace(approved, model='unapproved'))
        self.assertEqual(f.connection.rows[1].model, 'approved-new')

    def test_nonliteral_value_decision_and_original_exception_propagate(self):
        f, owner, cap, observed = self.make()
        bridge = self.bridge(f, owner, cap, observed, lambda *args: 1)
        host = ProfileHost(f.coordinator, policy=bridge.policy)
        with self.assertRaises(ProfileHostDenied):
            host.reconcile()
        class Stop(BaseException):
            pass
        original = Stop()
        def stop(*args):
            raise original
        bridge = self.bridge(f, owner, cap, observed, stop)
        with self.assertRaises(Stop) as caught:
            ProfileHost(f.coordinator, policy=bridge.policy).reconcile()
        self.assertIs(caught.exception, original)
        self.assertEqual(f.connection.events, [])

    def test_external_generation_requires_new_explicit_bridge(self):
        f, owner, cap, observed = self.make()
        bridge = self.bridge(f, owner, cap, observed, lambda op, value, rev: op == 'reconcile' and value is None)
        with self.enter(owner, cap, observed, 'profile.set_active'):
            f.store.set_active(2)
        self.assertFalse(bridge.current('recorded-policy-v1'))
        with self.assertRaises(ProfileHostStale):
            ProfileHost(f.coordinator, policy=bridge.policy).reconcile()
        with self.assertRaises(ConfigurationStale):
            self.bridge(f, owner, cap, observed, lambda *args: False)
        fresh = self.bridge(f, owner, cap, owner.snapshot(cap),
                            lambda op, value, rev: op == 'reconcile' and value is None)
        self.assertTrue(ProfileHost(f.coordinator, policy=fresh.policy).reconcile().snapshot['configured'])

    def test_shared_bridge_cannot_refresh_another_threads_observation(self):
        f, owner, cap, observed = self.make()
        bridge = self.bridge(f, owner, cap, observed, lambda *args: False)
        ready, proceed = Event(), Event()
        observations, errors = [], []
        def earlier_reader():
            try:
                with bridge.guard('reconcile', 'recorded-policy-v1'):
                    observations.append(bridge.current('recorded-policy-v1'))
                ready.set()
                if not proceed.wait(2):
                    raise AssertionError('Bounded writer release missing')
                observations.append(bridge.current('recorded-policy-v1'))
            except BaseException as error:
                errors.append(error)
        thread = Thread(target=earlier_reader, daemon=True)
        thread.start()
        try:
            self.assertTrue(ready.wait(2))
            with bridge.guard('set_active', 'recorded-policy-v1'):
                f.store.set_active(2)
            self.assertTrue(bridge.current('recorded-policy-v1'))
        finally:
            proceed.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(observations, [True, False])

    def test_failed_own_admission_retains_its_ticket_but_never_claims_commit(self):
        f, owner, cap, observed = self.make()
        bridge = self.bridge(f, owner, cap, observed,
                             lambda op, value, rev: op == 'set_active' and value == 999)
        host = ProfileHost(f.coordinator, policy=bridge.policy)
        with self.assertRaises(ProfileNotFound):
            host.set_active(999)
        self.assertTrue(bridge.current('recorded-policy-v1'))
        self.assertFalse(owner.current(observed))
        self.assertIs(host.status().committed, False)
        self.assertEqual(f.connection.mutations, [])

    def test_pooled_executor_enrollment_uses_original_committed_path(self):
        f, owner, cap, observed = self.make(pooled=True)
        bridge = self.bridge(f, owner, cap, observed,
                             lambda op, value, rev: op == 'set_active' and value == 2)
        host = ProfileHost(f.coordinator, policy=bridge.policy)
        host.set_active(2)
        self.assertTrue(f.connection.rows[2].is_default)
        self.assertTrue(host.status().runtime_applied)
        self.assertEqual(len(f.connection.mutations), 2)

    def test_bridge_policy_cannot_be_composed_with_different_resources(self):
        f, owner, cap, observed = self.make()
        calls = []
        bridge = self.bridge(f, owner, cap, observed, lambda *args: calls.append(args))
        other = fixture.CommittedProfileHostContractTests.make(self)
        for coordinator in (
                other.coordinator,
                CommittedProfileCoordinator(other.store, f.factory),
                CommittedProfileCoordinator(f.store, other.factory)):
            with self.assertRaises(TypeError):
                ProfileHost(coordinator, policy=bridge.policy)
        self.assertEqual(calls, [])
        self.assertEqual(f.connection.events, [])
        self.assertEqual(other.connection.events, [])
        self.assertEqual(f.transport.models, [])
        self.assertEqual(other.transport.models, [])

    def test_association_record_and_resource_identity_are_pinned(self):
        for mutate in ('record', 'executor', 'factory'):
            with self.subTest(mutate=mutate):
                f, owner, cap, observed = self.make()
                calls = []
                bridge = self.bridge(f, owner, cap, observed, lambda *args: calls.append(args))
                host = ProfileHost(f.coordinator, policy=bridge.policy)
                original = bridge.policy.resources
                other = fixture.CommittedProfileHostContractTests.make(self)
                # Deliberate unsupported frozen-object mutation tests ordinary
                # binding detection, not a hostile-Python sandbox claim.
                if mutate == 'record':
                    object.__setattr__(bridge.policy, 'resources', ProfileHostResources(f.executor, f.factory))
                elif mutate == 'executor':
                    object.__setattr__(original, 'executor', other.executor)
                else:
                    object.__setattr__(original, 'factory', other.factory)
                with self.assertRaises(ProfileHostUnavailable):
                    host.reconcile()
                self.assertEqual(calls, [])
                self.assertEqual(f.connection.events, [])
                self.assertEqual(other.connection.events, [])

    def test_revoked_while_waiting_for_original_executor_lock_refuses_before_query(self):
        for operation in ('profile.save', 'profile.set_active'):
            with self.subTest(operation=operation):
                f, owner, cap, observed = self.make()
                contended = Event()
                failures, returned = [], []
                original = PostgresExecutor.transaction.__wrapped__
                lines, start = inspect.getsourcelines(original)
                lock_line = start + next(i for i, line in enumerate(lines)
                                         if line.strip() == 'with self._lock:')
                def trace(frame, event, arg):
                    if (event == 'line' and frame.f_code is original.__code__
                            and frame.f_locals.get('self') is f.executor
                            and frame.f_lineno == lock_line and not contended.is_set()):
                        # Observe contention on the actual registered RLock;
                        # neither the original lock nor method is replaced.
                        acquired = f.executor._lock.acquire(blocking=False)
                        if acquired:
                            f.executor._lock.release()
                            raise AssertionError('Original executor lock was not held')
                        contended.set()
                    return trace
                def writer():
                    previous_trace = sys.gettrace()
                    sys.settrace(trace)
                    try:
                        with self.enter(owner, cap, observed, operation):
                            if operation == 'profile.save':
                                f.store.save(fixture._profile(model='must-not-write'))
                            else:
                                f.store.set_active(2)
                            returned.append(True)
                    except BaseException as error:
                        failures.append(error)
                    finally:
                        sys.settrace(previous_trace)
                thread = Thread(target=writer, daemon=True)
                try:
                    # An unrelated original adapter transaction holds its real
                    # lock. Only the enrolled writer may attempt business SQL.
                    with f.executor.transaction():
                        thread.start()
                        self.assertTrue(contended.wait(2))
                        self.assertEqual(f.connection.mutations, [])
                        owner.revoke(cap)
                finally:
                    if thread.ident is not None:
                        thread.join(3)
                self.assertFalse(thread.is_alive())
                self.assertEqual(returned, [],
                    f'query_events={f.connection.events.count("query")}, mutations={len(f.connection.mutations)}')
                self.assertEqual(len(failures), 1)
                self.assertIs(type(failures[0]), ProfileStorageError)
                self.assertEqual(f.connection.mutations, [])
                self.assertNotIn('query', f.connection.events)
                self.assertTrue(f.connection.rows[1].is_default)
                self.assertEqual(f.connection.rows[1].model, 'old-model-1')


if __name__ == '__main__':
    unittest.main()

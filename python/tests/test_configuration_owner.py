"""Production owner/registry tests with normally constructed inert resources.

These test real process-local capability state, not a replacement owner fixture.
The original adapter's injected connection is used only for bounded transaction
ownership probes; no real DB, provider request, task or application is run.
"""
from concurrent.futures import Future
from copy import copy
import gc
from threading import Event, Thread
import unittest
import weakref

from artex.configuration_owner import (
    ConfigurationCapability, ConfigurationDenied, ConfigurationBusy,
    ConfigurationNestingError, ConfigurationOwner, ConfigurationPermissions,
    ConfigurationSnapshot, ConfigurationStale, ConfigurationTransactionConflict,
    ConfigurationUnavailable,
)
from artex.db.configuration_ownership import (
    assert_bound, bind_executor, bind_factory, bound_owner, is_bound,
    is_bound_executor, is_bound_factory, require_read, require_write,
)
from artex.db.pool import ExecutorPool
from artex.db.postgres import PostgresExecutor
from artex.providers.factory import ProfileFactory
import test_committed_profile_host_contract as injected


class Stop(BaseException):
    pass


class ConfigurationOwnerTests(unittest.TestCase):
    def make(self):
        connection = injected._Connection([injected._profile()])
        executor = PostgresExecutor(connection, cursor_factory=injected._Cursor, owns_connection=True)
        self.addCleanup(executor.close)
        transport = injected._ConstructionOnlyTransport()
        self.addCleanup(lambda: self.assertEqual(transport.requests, 0))
        factory = ProfileFactory(transport)
        rights = ConfigurationPermissions(
            read=frozenset({'profile.reconcile', 'factory.build', 'factory.prepare', 'factory.prepare_pool'}),
            write=frozenset({'profile.save', 'profile.set_active', 'factory.invalidate'}))
        owner = ConfigurationOwner('selected-fixture-domain', participants={'actor': rights, 'other': rights})
        return owner, executor, factory, connection

    def bind(self):
        owner, executor, factory, connection = self.make()
        bind_executor(executor, owner); bind_factory(factory, owner)
        cap = owner.issue('actor')
        return owner, cap, executor, factory, connection

    def guard(self, owner, cap, snapshot, *, mode='write', operation='profile.save', timeout=0):
        return owner.guard(cap, operation, mode=mode, expected=snapshot, timeout=timeout)

    def start(self, work):
        future = Future()
        def run():
            try: future.set_result(work())
            except BaseException as error: future.set_exception(error)
        thread = Thread(target=run, daemon=True)
        thread.start()
        return thread, future

    def join(self, thread):
        thread.join(2)
        self.assertFalse(thread.is_alive(), 'Owned test worker remains unresolved')

    def test_explicit_policy_and_exact_enrollment_without_effects(self):
        owner, executor, factory, connection = self.make()
        with self.assertRaises(TypeError): ConfigurationPermissions()
        with self.assertRaises(ValueError): ConfigurationPermissions(read={'factory.build'}, write=frozenset())
        with self.assertRaises(ValueError): ConfigurationOwner('x', participants={})
        with self.assertRaises(ConfigurationDenied): owner.issue('unknown')
        with self.assertRaises(TypeError): bind_executor(object(), owner)
        with self.assertRaises(TypeError): bind_factory(executor, owner)
        bind_executor(executor, owner); bind_factory(factory, owner)
        self.assertIs(bound_owner(executor), owner)
        self.assertTrue(is_bound_executor(executor))
        self.assertTrue(is_bound_factory(factory))
        self.assertIsNone(assert_bound(factory, owner))
        self.assertEqual(connection.events, [])

    def test_unbound_legacy_resources_are_unchanged(self):
        for resource in (None, object(), [], {}):
            self.assertFalse(is_bound(resource))
            self.assertIsNone(bound_owner(resource))
            self.assertIsNone(require_read(resource, 'factory.build'))
            self.assertIsNone(require_write(resource, 'profile.save'))

    def test_unknown_foreign_copied_and_revoked_capabilities_refuse(self):
        owner, cap, executor, factory, connection = self.bind()
        other, _, _, _ = self.make()
        foreign = other.issue('actor')
        forged = ConfigurationCapability(None, 'actor')
        for value in (None, forged, copy(cap), foreign):
            self.assertFalse(owner.current(value))
            self.assertFalse(owner.permits(value, 'profile.save', mode='write'))
            with self.assertRaises(ConfigurationDenied): owner.snapshot(value)
        owner.revoke(cap)
        self.assertFalse(owner.current(cap))
        with self.assertRaises(ConfigurationDenied): owner.snapshot(cap)
        self.assertFalse(connection.events)

    def test_generation_advance_is_admission_not_commit(self):
        owner, cap, executor, factory, connection = self.bind()
        before = owner.snapshot(cap)
        with self.guard(owner, cap, before) as lease:
            self.assertEqual(lease.snapshot.generation, before.generation + 1)
            self.assertFalse(owner.current(before))
            self.assertTrue(owner.current(cap))
            self.assertTrue(owner.current(lease.snapshot))
            require_write(executor, 'profile.save')
        self.assertTrue(owner.current(lease.snapshot))
        self.assertEqual(connection.events, [])
        with self.assertRaises(ConfigurationStale):
            with self.guard(owner, cap, before): pass

    def test_failed_writer_keeps_new_generation_and_original_error(self):
        owner, cap, executor, factory, connection = self.bind()
        for error in (ValueError('body'), Stop('control')):
            before = owner.snapshot(cap)
            with self.assertRaises(type(error)) as caught:
                with self.guard(owner, cap, before) as lease:
                    raise error
            self.assertIs(caught.exception, error)
            self.assertFalse(owner.current(before))
            self.assertEqual(owner.snapshot(cap).generation, lease.snapshot.generation)
            self.assertTrue(owner.drain(timeout=0).drained)
        self.assertFalse(connection.events)

    def test_snapshot_identity_generation_and_capability_are_checked(self):
        owner, cap, executor, factory, connection = self.bind()
        snapshot = owner.snapshot(cap)
        other_cap = owner.issue('other')
        for wrong in (copy(snapshot), ConfigurationSnapshot(cap, snapshot.generation), owner.snapshot(other_cap)):
            with self.assertRaises(ConfigurationStale):
                with self.guard(owner, cap, wrong): pass
        self.assertFalse(owner.current(copy(snapshot)))
        self.assertEqual(owner.snapshot(cap).generation, 0)

    def test_hooks_require_current_thread_lease_and_matching_write_operation(self):
        owner, cap, executor, factory, connection = self.bind()
        with self.assertRaises(ConfigurationDenied): require_write(executor, 'profile.save')
        with self.assertRaises(ConfigurationDenied): require_read(factory, 'factory.build')
        with self.guard(owner, cap, owner.snapshot(cap)):
            require_write(executor, 'profile.save')
            require_read(factory, 'factory.build')
            with self.assertRaises(ConfigurationDenied): require_write(executor, 'profile.set_active')
            with self.assertRaises(ConfigurationDenied): require_write(factory, 'factory.invalidate')
            with self.assertRaises(ConfigurationDenied): require_read(factory, 'not-granted')
            thread, result = self.start(lambda: require_write(executor, 'profile.save'))
            try:
                with self.assertRaises(ConfigurationDenied): result.result(timeout=2)
            finally: self.join(thread)

    def test_read_guard_cannot_write_and_guard_parameters_have_no_defaults(self):
        owner, cap, executor, factory, connection = self.bind()
        with self.guard(owner, cap, owner.snapshot(cap), mode='read', operation='factory.build'):
            require_read(factory, 'factory.build')
            with self.assertRaises(ConfigurationDenied): require_write(executor, 'profile.save')
        self.assertEqual(owner.snapshot(cap).generation, 0)
        with self.assertRaises(TypeError):
            with owner.guard(cap, 'factory.build'): pass

    def test_nested_scopes_reuse_generation_and_refuse_upgrades_or_confusion(self):
        owner, cap, executor, factory, connection = self.bind()
        foreign, _, _, _ = self.make(); foreign_cap = foreign.issue('actor')
        other_cap = owner.issue('other')
        with self.guard(owner, cap, owner.snapshot(cap)) as outer:
            with self.guard(owner, cap, outer.snapshot) as nested:
                self.assertEqual(nested.snapshot.generation, outer.snapshot.generation)
                require_write(executor, 'profile.save')
            with self.guard(owner, cap, outer.snapshot, mode='read', operation='factory.build') as read:
                require_read(factory, 'factory.build')
                with self.assertRaises(ConfigurationNestingError):
                    with self.guard(owner, cap, read.snapshot): pass
            with self.assertRaises(ConfigurationNestingError):
                with self.guard(owner, other_cap, owner.snapshot(other_cap)): pass
            with self.assertRaises(ConfigurationNestingError):
                with self.guard(foreign, foreign_cap, foreign.snapshot(foreign_cap)): pass
        self.assertEqual(owner.snapshot(cap).generation, 1)

    def test_registration_is_idempotent_but_never_rebinds_or_extends_active_domain(self):
        owner, cap, executor, factory, connection = self.bind()
        bind_executor(executor, owner); bind_factory(factory, owner)
        other, spare_executor, spare_factory, _ = self.make()
        with self.assertRaises(ConfigurationUnavailable): bind_executor(executor, other)
        with self.guard(owner, cap, owner.snapshot(cap)):
            with self.assertRaises(ConfigurationUnavailable): bind_executor(spare_executor, owner)
            with self.assertRaises(ConfigurationUnavailable): bind_factory(spare_factory, owner)
        owner.close()
        self.assertTrue(is_bound(executor))
        with self.assertRaises(ConfigurationUnavailable): bind_executor(executor, other)
        with self.assertRaises(ConfigurationUnavailable): require_write(executor, 'profile.save')

    def test_owner_gc_leaves_live_resource_tombstone(self):
        owner, cap, executor, factory, connection = self.bind()
        ref = weakref.ref(owner)
        del owner
        gc.collect()
        self.assertIsNone(ref())
        self.assertTrue(is_bound(executor)); self.assertTrue(is_bound(factory))
        with self.assertRaises(ConfigurationUnavailable): bound_owner(executor)
        with self.assertRaises(ConfigurationUnavailable): require_read(factory, 'factory.build')
        replacement, _, _, _ = self.make()
        with self.assertRaises(ConfigurationUnavailable): bind_factory(factory, replacement)

    def test_dead_resource_binding_does_not_bind_new_instances(self):
        owner, cap, executor, factory, connection = self.bind()
        ref = weakref.ref(factory)
        del factory
        gc.collect()
        self.assertIsNone(ref())
        replacement = ProfileFactory(injected._ConstructionOnlyTransport())
        self.assertFalse(is_bound(replacement))
        bind_factory(replacement, owner)
        self.assertTrue(is_bound(replacement))

    def test_ambient_adapter_and_factory_lock_refuse_before_owner_admission(self):
        owner, cap, executor, factory, connection = self.bind()
        snapshot = owner.snapshot(cap)
        with executor.transaction():
            with self.assertRaises(ConfigurationTransactionConflict):
                with self.guard(owner, cap, snapshot): pass
        with factory._lock:
            with self.assertRaises(ConfigurationTransactionConflict):
                with self.guard(owner, cap, snapshot): pass
        connection.info.transaction_status = 2
        try:
            with self.assertRaises(ConfigurationTransactionConflict):
                with self.guard(owner, cap, snapshot): pass
        finally: connection.info.transaction_status = 0
        self.assertEqual(owner.snapshot(cap).generation, 0)

    def test_ambient_pool_lease_refuses_but_owned_nested_transaction_is_valid(self):
        owner, executor, factory, connection = self.make()
        pool = ExecutorPool(lambda: executor, max_size=1, acquire_timeout=0)
        self.addCleanup(lambda: self.assertTrue(pool.close(timeout=0).drained))
        bind_executor(pool, owner)
        cap = owner.issue('actor'); snapshot = owner.snapshot(cap)
        with pool.transaction():
            with self.assertRaises(ConfigurationTransactionConflict):
                with self.guard(owner, cap, snapshot): pass
        with self.guard(owner, cap, snapshot) as lease:
            with pool.transaction():
                with self.guard(owner, cap, lease.snapshot):
                    require_write(pool, 'profile.save')
        self.assertTrue(owner.drain(timeout=0).drained)

    def test_lock_replacement_cannot_bypass_registration_or_access(self):
        owner, cap, executor, factory, connection = self.bind()
        original = factory._lock
        factory._lock = object()
        try:
            with self.assertRaises(ConfigurationUnavailable): bound_owner(factory)
            with self.assertRaises(ConfigurationUnavailable):
                with self.guard(owner, cap, owner.snapshot(cap)): pass
        finally: factory._lock = original
        other, _, spare, _ = self.make()
        spare._lock = object()
        with self.assertRaises(ConfigurationUnavailable): bind_factory(spare, other)

    def test_revoke_is_independent_and_drain_retains_inflight_owner(self):
        owner, cap, executor, factory, connection = self.bind()
        snapshot = owner.snapshot(cap); entered = Event(); release = Event(); observed = []
        def work():
            with self.guard(owner, cap, snapshot) as lease:
                entered.set()
                if not release.wait(2): raise AssertionError('Owner release not signaled')
                observed.append(owner.current(lease.snapshot))
                with self.assertRaises(ConfigurationDenied): require_write(executor, 'profile.save')
        thread, future = self.start(work)
        try:
            self.assertTrue(entered.wait(2))
            owner.revoke(cap)
            self.assertFalse(owner.current(cap))
            drained = owner.drain(timeout=0)
            self.assertFalse(drained.drained); self.assertEqual(drained.active, 1)
            release.set()
            with self.assertRaises(ConfigurationDenied): future.result(timeout=2)
        finally:
            release.set(); self.join(thread)
        self.assertEqual(observed, [False])
        self.assertTrue(owner.drain(timeout=0).drained)

    def test_close_refuses_success_but_preserves_body_control(self):
        for error in (None, Stop('body')):
            owner, cap, executor, factory, connection = self.bind()
            try:
                with self.guard(owner, cap, owner.snapshot(cap)):
                    owner.close()
                    report = owner.drain(timeout=30)
                    self.assertFalse(report.drained)
                    if error is not None: raise error
            except BaseException as actual:
                if error is None: self.assertIsInstance(actual, ConfigurationUnavailable)
                else: self.assertIs(actual, error)
            else: self.fail('Revoked/failed operation returned success')
            self.assertTrue(owner.drain(timeout=0).drained)

    def test_waiter_timeout_and_revocation_do_not_take_foreign_barrier(self):
        owner, cap, executor, factory, connection = self.bind()
        other = owner.issue('other'); snapshot = owner.snapshot(cap); other_snapshot = owner.snapshot(other)
        entered = Event(); release = Event()
        def holder():
            with self.guard(owner, cap, snapshot, mode='read', operation='factory.build'):
                entered.set()
                if not release.wait(2): raise AssertionError('Holder not released')
        thread, future = self.start(holder)
        try:
            self.assertTrue(entered.wait(2))
            with self.assertRaises(ConfigurationBusy):
                with self.guard(owner, other, other_snapshot, timeout=0): pass
            owner.revoke(other)
            with self.assertRaises(ConfigurationDenied):
                with self.guard(owner, other, other_snapshot, timeout=1): pass
        finally:
            release.set(); self.join(thread)
        future.result(timeout=2)


if __name__ == '__main__':
    unittest.main()

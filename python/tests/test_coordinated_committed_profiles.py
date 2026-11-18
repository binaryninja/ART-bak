"""Profile integration with original ownership and an injected SQL driver."""
from dataclasses import replace
import unittest

from artex.configuration_owner import ConfigurationOwner, ConfigurationPermissions, ConfigurationUnavailable
from artex.db.configuration_ownership import bind_executor, bind_factory
from artex.db.pool import ExecutorPool
from artex.db.postgres import PostgresExecutor
from artex.db.profiles import ProfileInvalid, ProfileStore
from artex.db.transaction_coordination import TransactionCoordinationBinding, TransactionCoordinationBusy
from artex.providers.committed_profiles import CommittedProfileCoordinator
from artex.providers.factory import ProfileFactory
from artex.providers.owned_profiles import OwnedProfilePolicy
from artex.providers.profile_host import ProfileHost, ProfileHostUnavailable
import test_committed_profiles as fixture


class _CoordinationCursor(fixture._Cursor):
    def execute(self, sql, params):
        connection = self.connection
        if sql == 'SELECT pg_catalog.current_database()':
            self.result = [('test_profiles',)]
        elif sql in ('SELECT pg_catalog.pg_try_advisory_xact_lock($1::pg_catalog.int8)',
                     'SELECT pg_catalog.pg_try_advisory_xact_lock_shared($1::pg_catalog.int8)'):
            assert params == (731,)
            mode = 'read' if '_shared(' in sql else 'write'
            connection.lock_modes.append(mode)
            self.result = [(len(connection.lock_modes) != connection.busy_on,)]
        else:
            return super().execute(sql, params)
        assert len(connection.frames) == 1
        self.rowcount = 1


class CoordinatedCommittedProfilesTests(unittest.TestCase):
    def make(self, *, pooled=False, coordinated=True):
        connection = fixture._Connection([fixture._profile(), fixture._profile(2)])
        connection.lock_modes, connection.busy_on = [], None
        adapter = PostgresExecutor(connection, cursor_factory=_CoordinationCursor, owns_connection=True)
        if pooled:
            executor = ExecutorPool(lambda: adapter, max_size=1, acquire_timeout=0)
            self.addCleanup(lambda: self.assertTrue(executor.close(timeout=0).drained))
        else:
            executor = adapter
            self.addCleanup(adapter.close)
        transport = fixture._ConstructionOnlyTransport()
        self.addCleanup(lambda: self.assertEqual(transport.requests, 0))
        transport.refuse = lambda config: self.assertEqual(connection.frames, [])
        factory = ProfileFactory(transport)
        owner = ConfigurationOwner('profiles', participants={'operator': ConfigurationPermissions(
            read=frozenset(('profile.reconcile', 'factory.prepare_pool', 'factory.prepare')),
            write=frozenset(('profile.save', 'profile.set_active')))})
        self.addCleanup(owner.close)
        capability = owner.issue('operator')
        bind_executor(executor, owner)
        bind_factory(factory, owner)
        binding = TransactionCoordinationBinding(executor, owner=owner, database_name='test_profiles',
                                                 domain=owner.domain, key=731)
        coordinator = CommittedProfileCoordinator(ProfileStore(executor), factory, transaction_coordination=binding if coordinated else None)
        approved = {'save': fixture._profile(model='updated'), 'set_active': 2, 'reconcile': None}
        policy = OwnedProfilePolicy(owner, capability, executor=executor, factory=factory,
            snapshot=owner.snapshot(capability), revision='fixture-v1', timeout=0,
            authorize_value=lambda operation, value, revision: operation in approved and approved[operation] == value)
        return connection, ProfileHost(coordinator, policy=policy.policy), coordinator, binding

    def test_original_host_save_select_and_reconcile_own_separate_transactions(self):
        for pooled in (False, True):
            with self.subTest(pooled=pooled):
                connection, host, _, _ = self.make(pooled=pooled)
                self.assertEqual(host.save(fixture._profile(model='updated')), 1)
                self.assertEqual(host.snapshot()['model'], 'updated')
                self.assertIsNone(host.set_active(2))
                self.assertTrue(host.status().committed)
                self.assertEqual(host.reconcile().snapshot['model'], 'old-model-2')
                self.assertIs(host.status().committed, False)
                self.assertEqual(connection.lock_modes, ['write'] * 4 + ['read'])
                self.assertEqual(connection.events.count('outer.commit'), 5)

    def test_busy_mutation_lock_after_preflight_never_writes_or_retries(self):
        connection, host, coordinator, _ = self.make()
        connection.busy_on = 2
        with self.assertRaises(TransactionCoordinationBusy):
            host.set_active(2)
        self.assertEqual(connection.lock_modes, ['write', 'write'])
        self.assertEqual(connection.mutations, [])
        self.assertEqual(connection.events.count('outer.rollback'), 1)
        self.assertEqual(coordinator.status().phase, 'not_committed')
        self.assertIs(host.status().committed, False)
        self.assertFalse(host.snapshot()['configured'])

    def test_lost_commit_ack_preserves_uncertainty_without_replay(self):
        connection, host, coordinator, _ = self.make()
        connection.commit_error = RuntimeError('lost commit acknowledgement')
        connection.commit_error_after_mutation = True
        with self.assertRaises(RuntimeError) as raised:
            host.save(fixture._profile(model='updated'))
        self.assertIs(raised.exception, connection.commit_error)
        self.assertEqual(connection.lock_modes, ['write', 'write'])
        self.assertEqual(len(connection.mutations), 1)
        self.assertEqual(connection.committed[1].model, 'updated')
        self.assertIsNone(host.status().committed)
        self.assertEqual(coordinator.status().phase, 'outcome_unknown')
        self.assertFalse(host.snapshot()['configured'])

    def test_coordination_option_replacement_is_detected_before_sql(self):
        for transition in ('remove', 'add', 'change_key'):
            with self.subTest(transition=transition):
                connection, host, coordinator, binding = self.make(coordinated=transition != 'add')
                replacement = {'remove': None, 'add': binding, 'change_key': replace(binding, key=732)}
                coordinator._transaction_coordination = replacement[transition]
                with self.assertRaises(ProfileHostUnavailable):
                    host.validate_bindings()
                with self.assertRaises(ProfileHostUnavailable):
                    host.set_active(2)
                with self.assertRaisesRegex(ProfileInvalid, 'binding changed'):
                    coordinator.set_active(2)
                self.assertEqual(connection.events, [])
                self.assertEqual(connection.lock_modes, [])
                self.assertEqual(connection.mutations, [])

    def test_mismatched_binding_or_factory_rejected_before_sql(self):
        connection, _, coordinator, binding = self.make()
        other_connection, _, other, other_binding = self.make()
        with self.assertRaises(ProfileInvalid):
            CommittedProfileCoordinator(coordinator._store, coordinator._factory, transaction_coordination=other_binding)
        with self.assertRaises(ProfileInvalid):
            CommittedProfileCoordinator(coordinator._store, coordinator._factory, transaction_coordination=object())
        for factory in (other._factory, ProfileFactory(fixture._ConstructionOnlyTransport())):
            with self.assertRaises(ConfigurationUnavailable):
                CommittedProfileCoordinator(coordinator._store, factory, transaction_coordination=binding)
        self.assertEqual(connection.events, [])
        self.assertEqual(other_connection.events, [])

"""Coordination contracts on original executors with an injected tuple cursor."""
import unittest

from artex.configuration_owner import (
    ConfigurationDenied, ConfigurationOwner, ConfigurationPermissions,
    ConfigurationUnavailable,
)
from artex.db.configuration_ownership import bind_executor
from artex.db.pool import ExecutorPool
from artex.db.postgres import CommitBoundaryError, PostgresExecutor
from artex.db.transaction_coordination import (
    TransactionCoordinationBinding, TransactionCoordinationBusy,
    TransactionCoordinationError, coordinated_transaction,
)
from test_committed_transactions import Connection, ControlFlow, Cursor, UPDATE


DATABASE = 'SELECT pg_catalog.current_database()'
SHARED = 'SELECT pg_catalog.pg_try_advisory_xact_lock_shared($1::pg_catalog.int8)'
EXCLUSIVE = 'SELECT pg_catalog.pg_try_advisory_xact_lock($1::pg_catalog.int8)'


class CoordinationCursor(Cursor):
    def execute(self, sql, params):
        if sql in (DATABASE, SHARED, EXCLUSIVE):
            self.c.events.append(('query', sql, params))
            if self.c.depth != 1:
                raise AssertionError('Coordination query outside owned outer transaction')
            self.row = self.c.database_row if sql == DATABASE else self.c.lock_row
            if sql != DATABASE and self.c.after_lock:
                self.c.after_lock()
        else:
            super().execute(sql, params)

    def fetchone(self):
        return self.row


class CoordinationTests(unittest.TestCase):
    def setUp(self):
        self.connection = Connection()
        self.connection.database_row = ('lab',)
        self.connection.lock_row = (True,)
        self.connection.after_lock = None
        self.db = PostgresExecutor(self.connection, cursor_factory=CoordinationCursor,
                                   owns_connection=True)
        policy = ConfigurationPermissions(read=frozenset({'inspect', 'other'}),
                                          write=frozenset({'change'}))
        self.owner = ConfigurationOwner('configuration', participants={'host': policy})
        self.grant = self.owner.issue('host')
        bind_executor(self.db, self.owner)

    def binding(self, **overrides):
        args = dict(executor=self.db, owner=self.owner, database_name='lab',
                    domain='configuration', key=-(2**63))
        return TransactionCoordinationBinding(**(args | overrides))

    def guard(self, operation='change', mode='write'):
        return self.owner.guard(self.grant, operation, mode=mode, timeout=0,
                                expected=self.owner.snapshot(self.grant))

    def scope(self, operation='change', mode='write', **overrides):
        return coordinated_transaction(self.binding(**overrides), operation=operation, mode=mode)

    def test_configuration_is_explicit_and_non_enrolling(self):
        foreign = ExecutorPool(lambda: self.fail('acquired resource'))
        self.addCleanup(foreign.close)
        with self.assertRaises(ConfigurationUnavailable):
            self.binding(executor=foreign)
        with self.assertRaises(TypeError):
            self.binding(executor=object())
        for args in ({'database_name': ''}, {'domain': 'wrong'}, {'database_name': 'x\0y'},
                     {'key': True}, {'key': 2**63}, {'key': -(2**63)-1}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.binding(**args)
        self.owner.close()
        with self.assertRaises(ConfigurationUnavailable):
            self.binding()
        self.assertEqual(self.connection.events, [])

    def test_exact_admitted_operation_and_mode_required_before_begin(self):
        with self.assertRaises(ConfigurationDenied), self.scope():
            self.fail('unadmitted')
        with self.guard('inspect', 'read'):
            with self.assertRaises(ConfigurationDenied), self.scope('other', 'read'):
                self.fail('different read')
        with self.guard():
            with self.assertRaises(ConfigurationDenied), self.scope('inspect', 'read'):
                self.fail('writer cannot choose shared lock')
        self.assertEqual(self.connection.events, [])

    def test_pinned_pool_uses_one_factory_and_native_binds(self):
        calls = []
        def factory():
            calls.append(1)
            return self.db
        pool = ExecutorPool(factory, max_size=1, acquire_timeout=0)
        self.addCleanup(pool.close)
        bind_executor(pool, self.owner)
        for op, mode, lock_sql in [('inspect', 'read', SHARED), ('change', 'write', EXCLUSIVE)]:
            with self.subTest(mode=mode):
                self.connection.events.clear()
                with self.guard(op, mode), self.scope(op, mode, executor=pool) as tx:
                    self.assertIs(tx, pool)
                    self.assertEqual(pool.state().leased, 1)
                    if mode == 'write':
                        tx.execute(UPDATE)
                expected = [('begin', 1), ('query', DATABASE, ()),
                            ('query', lock_sql, (-(2**63),))]
                if mode == 'write':
                    expected.append(('query', UPDATE.sql, UPDATE.params))
                self.assertEqual(self.connection.events, expected + [('commit', 1)])
        self.assertEqual(calls, [1])

    def test_wrong_database_never_attempts_lock(self):
        for row in [('foreign',), ('lab', 'extra'), ['lab'], (b'lab',)]:
            self.connection.database_row = row
            self.connection.events.clear()
            with self.subTest(row=row), self.guard(), self.assertRaises(TransactionCoordinationError):
                with self.scope():
                    self.fail('business operation reached')
            self.assertEqual(self.connection.events, [('begin', 1), ('query', DATABASE, ()),
                                                     ('rollback', 1)])

    def test_busy_and_malformed_lock_are_distinct_and_consume_one_attempt(self):
        for row, error in [((False,), TransactionCoordinationBusy),
                           ((0,), TransactionCoordinationError), ((), TransactionCoordinationError),
                           ([True], TransactionCoordinationError)]:
            self.connection.lock_row = row
            self.connection.events.clear()
            with self.subTest(row=row), self.guard(), self.assertRaises(error) as caught:
                with self.scope():
                    self.fail('business operation reached')
            self.assertIs(type(caught.exception), error)
            self.assertEqual(self.connection.events, [('begin', 1), ('query', DATABASE, ()),
                        ('query', EXCLUSIVE, (-(2**63),)), ('rollback', 1)])

    def test_revoke_during_pool_acquisition_rolls_back_before_query(self):
        def factory():
            self.owner.revoke(self.grant)
            return self.db
        pool = ExecutorPool(factory)
        self.addCleanup(pool.close)
        bind_executor(pool, self.owner)
        with self.assertRaises(ConfigurationDenied), self.guard(), self.scope(executor=pool):
            self.fail('business operation reached')
        self.assertEqual(self.connection.events, [('begin', 1), ('rollback', 1), ('close', 0)])

    def test_revoke_during_lock_round_trip_refuses_body(self):
        self.connection.after_lock = lambda: self.owner.revoke(self.grant)
        with self.assertRaises(ConfigurationDenied), self.guard(), self.scope():
            self.fail('business operation reached')
        self.assertEqual(self.connection.events[-1], ('rollback', 1))

    def test_revoke_after_mutation_rolls_back_before_normal_exit(self):
        with self.assertRaises(ConfigurationDenied), self.guard(), self.scope() as tx:
            tx.execute(UPDATE)
            self.owner.revoke(self.grant)
        self.assertEqual(self.connection.rows['value'], 0)
        self.assertEqual(self.connection.events[-1], ('rollback', 1))

    def test_uncertain_commit_is_propagated_once_even_if_mutation_persisted(self):
        error = ControlFlow('driver lost acknowledgement')
        self.connection.commit_error = error
        with self.assertRaises(ControlFlow) as caught, self.guard(), self.scope() as tx:
            tx.execute(UPDATE)
        self.assertIs(caught.exception, error)
        self.assertEqual(self.connection.rows['value'], 7)
        self.assertEqual(self.connection.events.count(('commit', 1)), 1)
        self.assertEqual(self.connection.events.count(('query', UPDATE.sql, UPDATE.params)), 1)

    def test_caught_nested_control_exception_aborts_outer_work(self):
        error = ControlFlow('cancelled within a savepoint')
        self.connection.suppress = True
        with self.assertRaises(ControlFlow) as caught, self.guard(), self.scope() as tx:
            tx.execute(UPDATE)
            with self.assertRaises(ControlFlow):
                with tx.transaction():
                    raise error
        self.assertIs(caught.exception, error)
        self.assertEqual(self.connection.rows['value'], 0)
        self.assertEqual(self.connection.events[-3:], [('begin', 2), ('rollback', 2), ('rollback', 1)])

    def test_failed_rollback_exposes_driver_outcome_without_replay(self):
        error = RuntimeError('rollback acknowledgement lost')
        self.connection.rollback_error = error
        with self.assertRaises(RuntimeError) as caught, self.guard(), self.scope() as tx:
            tx.execute(UPDATE)
            raise ControlFlow('cancelled')
        self.assertIs(caught.exception, error)
        self.assertEqual(self.connection.events.count(('begin', 1)), 1)
        self.assertEqual(self.connection.events[-1], ('rollback', 1))

    def test_nested_ambient_transaction_refused_before_coordination_sql(self):
        with self.guard(), self.db.transaction(), self.assertRaises(CommitBoundaryError):
            with self.scope():
                self.fail('nested body reached')
        self.assertEqual(self.connection.events, [('begin', 1), ('commit', 1)])

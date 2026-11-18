"""Injected driver/state tests only; no PostgreSQL connection or SQL execution."""
from copy import deepcopy
from types import SimpleNamespace
import unittest

from artex.db.core import Query
from artex.db.pool import ExecutorPool, PoolFactoryError
from artex.db.postgres import CommitBoundaryError, PostgresExecutor
from artex.db.profiles import ProfileStore


UPDATE = Query('UPDATE fixture SET value=$1', (7,))


class ControlFlow(BaseException):
    pass


class Connection:
    """Explicit synthetic row snapshots, savepoints and uncertain commit model."""
    def __init__(self):
        self.autocommit = True
        self.info = SimpleNamespace(transaction_status=0)
        self.closed = False
        self.rows = {'value': 0, 'profiles': {1: True, 2: False}}
        self.depth = 0
        self.events = []
        self.suppress = False
        self.enter_error = self.commit_error = self.rollback_error = None
        self.query_error = None
        self.post_status = None
        self.close_on_exit = False

    def transaction(self):
        return Transaction(self)

    def close(self):
        self.events.append(('close', self.depth))
        self.closed = True


class Transaction:
    def __init__(self, connection):
        self.c = connection

    def __enter__(self):
        c = self.c
        if c.enter_error is not None:
            raise c.enter_error
        self.before = deepcopy(c.rows)
        c.depth += 1
        c.events.append(('begin', c.depth))
        c.info.transaction_status = 2
        return self

    def __exit__(self, kind, error, traceback):
        c = self.c
        depth = c.depth
        c.depth -= 1
        c.info.transaction_status = 2 if c.depth else 0
        if error is not None:
            c.events.append(('rollback', depth))
            if c.rollback_error is not None:
                raise c.rollback_error
            c.rows = self.before
            return c.suppress
        c.events.append(('commit', depth))
        if c.commit_error is not None:
            # Deliberately retain rows: an error is not proof of rollback.
            raise c.commit_error
        if not c.depth:
            if c.post_status is not None:
                c.info.transaction_status = c.post_status
            if c.close_on_exit:
                c.closed = True
        return False


class Cursor:
    def __init__(self, connection):
        self.c = connection
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params):
        c = self.c
        c.events.append(('query', sql, params))
        if c.query_error is not None:
            c.info.transaction_status = 3
            raise c.query_error
        if sql == UPDATE.sql:
            c.rows['value'] = params[0]
        elif sql == 'UPDATE llm_profiles SET is_default=false WHERE is_default':
            self.rowcount = sum(c.rows['profiles'].values())
            c.rows['profiles'] = dict.fromkeys(c.rows['profiles'], False)
        elif sql == 'UPDATE llm_profiles SET is_default=true WHERE id=$1':
            self.rowcount = int(params[0] in c.rows['profiles'])
            if self.rowcount:
                c.rows['profiles'][params[0]] = True
        elif sql != 'SELECT value FROM fixture':
            raise AssertionError('Unexpected synthetic query')

    def fetchone(self):
        return (self.c.rows['value'],)

    def fetchall(self):
        return [self.fetchone()]


def make():
    connection = Connection()
    executor = PostgresExecutor(connection, cursor_factory=Cursor, owns_connection=True)
    return connection, executor


class CommittedTransactionTests(unittest.TestCase):
    def test_empty_owned_scope_commits_without_inventing_a_query(self):
        c, db = make()
        with db.committed_transaction() as tx:
            self.assertIs(tx, db)
            self.assertEqual(c.info.transaction_status, 2)
        self.assertEqual(c.events, [('begin', 1), ('commit', 1)])
        self.assertEqual(c.info.transaction_status, 0)

    def test_normal_commit_precedes_return_and_preserves_identity(self):
        c, db = make()
        with db.committed_transaction() as tx:
            self.assertIs(tx, db)
            tx.execute(UPDATE)
            self.assertNotIn(('commit', 1), c.events)
        self.assertEqual(c.rows['value'], 7)
        self.assertEqual(c.events[-1], ('commit', 1))
        self.assertIsNone(db._committed)

    def test_original_profile_store_nested_savepoint_succeeds(self):
        c, db = make()
        with db.committed_transaction():
            ProfileStore(db).set_active(2)
        self.assertEqual(c.rows['profiles'], {1: False, 2: True})
        self.assertEqual([e for e in c.events if e[0] == 'commit'], [('commit', 2), ('commit', 1)])

    def test_ordinary_driver_suppression_remains_compatible(self):
        c, db = make()
        c.suppress = True
        with db.transaction():
            db.execute(UPDATE)
            raise ControlFlow('ordinary sentinel')
        self.assertEqual(c.rows['value'], 0)
        self.assertIsNone(db._committed)

    def test_body_error_identity_even_if_driver_suppresses(self):
        for suppress in (False, True):
            for cls in (RuntimeError, ControlFlow):
                with self.subTest(suppress=suppress, error=cls):
                    c, db = make()
                    c.suppress = suppress
                    error = cls('body')
                    with self.assertRaises(cls) as caught:
                        with db.committed_transaction():
                            db.execute(UPDATE)
                            raise error
                    self.assertIs(caught.exception, error)
                    self.assertEqual(c.rows['value'], 0)
                    self.assertNotIn(('commit', 1), c.events)

    def test_caught_inner_failure_is_sticky_before_outer_commit(self):
        for suppress in (False, True):
            with self.subTest(suppress=suppress):
                c, db = make()
                c.suppress = suppress
                error = ControlFlow('inner')
                with self.assertRaises(ControlFlow) as caught:
                    with db.committed_transaction():
                        db.execute(UPDATE)
                        try:
                            with db.transaction():
                                raise error
                        except ControlFlow as nested:
                            self.assertIs(nested, error)
                        self.assertEqual(c.info.transaction_status, 2)
                self.assertIs(caught.exception, error)
                self.assertEqual(c.rows['value'], 0)
                self.assertNotIn(('commit', 1), c.events)

    def test_original_store_inner_suppression_cannot_report_success(self):
        c, db = make()
        c.suppress = True
        error = ControlFlow('query sentinel')
        c.query_error = error
        with self.assertRaises(ControlFlow) as caught:
            with db.committed_transaction():
                ProfileStore(db).set_active(2)
        self.assertIs(caught.exception, error)
        self.assertEqual(c.rows['profiles'], {1: True, 2: False})
        self.assertNotIn(('commit', 1), c.events)

    def test_caught_inner_commit_error_remains_sticky(self):
        c, db = make()
        error = ControlFlow('nested commit uncertain')
        c.commit_error = error
        with self.assertRaises(ControlFlow) as caught:
            with db.committed_transaction():
                db.execute(UPDATE)
                try:
                    with db.transaction():
                        pass
                except ControlFlow as nested:
                    self.assertIs(nested, error)
                self.assertEqual(c.info.transaction_status, 2)
        self.assertIs(caught.exception, error)
        self.assertNotIn(('commit', 1), c.events)
        self.assertEqual(c.rows['value'], 0)

    def test_caught_sql_error_cannot_reach_normal_driver_exit(self):
        c, db = make()
        c.query_error = ValueError('aborted query')
        with self.assertRaises(CommitBoundaryError):
            with db.committed_transaction():
                with self.assertRaises(ValueError):
                    db.execute(UPDATE)
                self.assertEqual(c.info.transaction_status, 3)
        self.assertEqual(c.events[-1], ('rollback', 1))

    def test_nonactive_body_and_unsettled_exit_are_not_success(self):
        c, db = make()
        with self.assertRaises(CommitBoundaryError):
            with db.committed_transaction():
                c.info.transaction_status = 0
        self.assertNotIn(('commit', 1), c.events)
        for field, value in [('post_status', 3), ('close_on_exit', True)]:
            with self.subTest(field=field):
                c, db = make()
                setattr(c, field, value)
                with self.assertRaises(CommitBoundaryError):
                    with db.committed_transaction():
                        db.execute(UPDATE)
                self.assertEqual(c.rows['value'], 7)  # no invented rollback

    def test_driver_commit_enter_and_rollback_errors_are_preserved(self):
        for field in ('enter_error', 'commit_error', 'rollback_error'):
            for cls in (RuntimeError, ControlFlow):
                with self.subTest(field=field, error=cls):
                    c, db = make()
                    error = cls(field)
                    setattr(c, field, error)
                    with self.assertRaises(cls) as caught:
                        with db.committed_transaction():
                            db.execute(UPDATE)
                            if field == 'rollback_error':
                                raise ValueError('original body failure')
                    self.assertIs(caught.exception, error)
                    self.assertIsNone(db._committed)
                    if field != 'enter_error':
                        self.assertEqual(c.rows['value'], 7)  # uncertainty model

    def test_ambient_non_autocommit_and_nested_entry_refused_before_body(self):
        for status, autocommit in [(2, True), (3, True), (0, False)]:
            c, db = make()
            c.info.transaction_status = status
            c.autocommit = autocommit
            with self.assertRaises(CommitBoundaryError):
                with db.committed_transaction():
                    self.fail('body reached')
            self.assertEqual(c.events, [])
        for outer in ('transaction', 'committed_transaction'):
            c, db = make()
            with getattr(db, outer)():
                with self.assertRaises(CommitBoundaryError):
                    with db.committed_transaction():
                        self.fail('nested body reached')
            self.assertEqual(c.events, [('begin', 1), ('commit', 1)])


class CommittedPoolTests(unittest.TestCase):
    def make_pool(self):
        c, db = make()
        pool = ExecutorPool(lambda: db, max_size=1, acquire_timeout=0)
        self.addCleanup(pool.close)
        return c, db, pool

    def test_pins_same_original_resource_through_savepoints_and_commit(self):
        c, db, pool = self.make_pool()
        with pool.committed_transaction() as tx:
            self.assertIs(tx, pool)
            self.assertIs(pool._local.lease.executor, db)
            self.assertEqual(pool.state().leased, 1)
            ProfileStore(pool).set_active(2)
            pool.execute(UPDATE)
            self.assertNotIn(('commit', 1), c.events)
        self.assertEqual(c.rows, {'value': 7, 'profiles': {1: False, 2: True}})
        self.assertEqual(pool.state().idle, 1)

    def test_commit_error_retires_and_never_retries(self):
        c, db, pool = self.make_pool()
        error = ControlFlow('commit uncertain')
        c.commit_error = error
        with self.assertRaises(ControlFlow) as caught:
            with pool.committed_transaction():
                pool.execute(UPDATE)
        self.assertIs(caught.exception, error)
        self.assertEqual(c.rows['value'], 7)
        self.assertTrue(c.closed)
        self.assertEqual(pool.state().total, 0)
        self.assertEqual(c.events.count(('begin', 1)), 1)

    def test_unsupported_executor_is_refused_without_duck_typed_fallback(self):
        class Other(PostgresExecutor):
            def committed_transaction(self):
                raise AssertionError('subclass capability must not run')
        c = Connection()
        other = Other(c, cursor_factory=Cursor, owns_connection=True)
        pool = ExecutorPool(lambda: other, max_size=1)
        self.addCleanup(pool.close)
        with self.assertRaises(PoolFactoryError):
            with pool.committed_transaction():
                self.fail('unsupported body reached')
        self.assertTrue(c.closed)
        self.assertEqual(pool.state().total, 0)

    def test_existing_lease_refuses_new_owned_commit_without_reacquisition(self):
        c, db, pool = self.make_pool()
        with pool.transaction():
            with self.assertRaises(CommitBoundaryError):
                with pool.committed_transaction():
                    self.fail('nested body reached')
            self.assertIs(pool._local.lease.executor, db)
        self.assertEqual(c.events, [('begin', 1), ('commit', 1)])

    def test_caught_pool_operation_failure_rolls_back_before_commit(self):
        c, db, pool = self.make_pool()
        with self.assertRaises(CommitBoundaryError):
            with pool.committed_transaction():
                pool.execute(UPDATE)
                with self.assertRaises(TypeError):
                    pool.execute(object())
        self.assertEqual(c.rows['value'], 0)
        self.assertNotIn(('commit', 1), c.events)
        self.assertTrue(c.closed)


if __name__ == '__main__':
    unittest.main()

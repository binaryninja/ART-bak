"""Existing-row task-chain transactions on an injected PostgreSQL driver."""
import unittest

from artex.configuration_owner import ConfigurationDenied, ConfigurationOwner, ConfigurationPermissions
from artex.db.configuration_ownership import bind_executor
from artex.db.pool import ExecutorPool
from artex.db.postgres import PostgresExecutor
from artex.db.tasks import TaskInvalid, TaskStore
from artex.db.transaction_coordination import (
    TransactionCoordinationBinding, TransactionCoordinationBusy, TransactionCoordinationError,
)
from test_committed_transactions import Connection, ControlFlow
from test_transaction_coordination import CoordinationCursor, DATABASE, EXCLUSIVE


OPERATION = 'task_profiles.replace_idle'


class ExistingTaskCursor(CoordinationCursor):
    def execute(self, sql, params):
        if sql in (DATABASE, EXCLUSIVE):
            return super().execute(sql, params)
        c = self.c
        if c.depth != 1:
            raise AssertionError('Business SQL outside the owned transaction')
        c.events.append(('query', sql, params))
        self.result = []
        task = c.rows['task']
        if sql.startswith('SELECT status,paused,queued,llm_chain_revision'):
            if params == (10, 20) and sql.endswith('FOR UPDATE'):
                self.result = [(task['status'], False, False, task['revision'])]
        elif sql.startswith('SELECT id FROM exploration_nodes'):
            self.result = [(99,)] if c.quota_blocked else []
        elif sql.startswith('SELECT id FROM llm_profiles'):
            if sql.endswith('ORDER BY id FOR SHARE'):
                self.result = [(i,) for i in sorted(set(params[0]) & c.rows['available'])]
        elif sql.startswith('DELETE FROM task_llm_profiles'):
            if params != (10,):
                raise AssertionError('Wrong task')
            self.rowcount = len(task['chain'])
            task['chain'].clear()
        elif sql.startswith('INSERT INTO task_llm_profiles'):
            if params[0] != 10 or params[2] != len(task['chain']):
                raise AssertionError('Unexpected chain position')
            task['chain'].append(params[1])
            self.rowcount = 1
        elif sql.startswith('UPDATE tasks SET active_llm_profile_id'):
            task_id, active, revision = params
            if task_id == 10 and revision == task['revision'] and not c.cas_misses:
                task['active'] = active
                task['revision'] += 1
                self.result = [(task['revision'],)]
        else:
            raise AssertionError('Unexpected existing-row query')

    def fetchall(self):
        return self.result


class ExistingTaskProfileCoordinationTests(unittest.TestCase):
    def setUp(self):
        c = self.connection = Connection()
        c.rows = {'task': dict(status='done', revision=4, chain=[8], active=8),
                  'available': {5, 7, 8}}
        c.cas_misses = c.quota_blocked = False
        c.database_row, c.lock_row, c.after_lock = ('lab',), (True,), None
        self.db = PostgresExecutor(c, cursor_factory=ExistingTaskCursor, owns_connection=True)
        permissions = ConfigurationPermissions(read=frozenset(), write=frozenset({OPERATION}))
        self.owner = ConfigurationOwner('configuration', participants={'host': permissions})
        self.grant = self.owner.issue('host')

    def enroll(self, executor):
        bind_executor(executor, self.owner)
        return TransactionCoordinationBinding(executor, owner=self.owner, domain=self.owner.domain,
                                              database_name='lab', key=123)

    def guard(self):
        return self.owner.guard(self.grant, OPERATION, mode='write',
                                expected=self.owner.snapshot(self.grant), timeout=0)

    def replace(self, executor=None, **opts):
        return TaskStore(executor or self.db).replace_llm_profiles_idle(10, 20, 4, [7, 5], **opts)

    def queries(self):
        return [event[1:] for event in self.connection.events if event[0] == 'query']

    def test_default_and_explicit_none_preserve_legacy_unenrolled_transaction(self):
        for options in ({}, {'transaction_coordination': None}):
            with self.subTest(options=options):
                self.connection.rows['task']['revision'] = 4
                self.connection.events.clear()
                self.assertEqual(self.replace(**options), 5)
                self.assertTrue(self.queries()[0][0].endswith('FOR UPDATE'))
                self.assertEqual(self.connection.events[-1], ('commit', 1))

    def test_pinned_pool_protects_original_lock_order_and_cas(self):
        pool = ExecutorPool(lambda: self.db, max_size=1, acquire_timeout=0)
        self.addCleanup(pool.close)
        binding = self.enroll(pool)
        with self.guard():
            self.assertEqual(self.replace(pool, transaction_coordination=binding), 5)
        queries = self.queries()
        self.assertEqual(queries[:2], [(DATABASE, ()), (EXCLUSIVE, (123,))])
        self.assertTrue(queries[2][0].endswith('FOR UPDATE'))
        self.assertTrue(queries[3][0].endswith('ORDER BY id FOR SHARE'))
        self.assertEqual([args for _, args in queries[4:]], [(10,), (10, 7, 0), (10, 5, 1), (10, 7, 4)])
        self.assertEqual(self.connection.rows['task'], dict(status='done', revision=5, chain=[7, 5], active=7))
        self.assertEqual(pool.state().idle, 1)
        self.assertEqual(self.connection.events.count(('begin', 1)), 1)

    def test_missing_admission_or_foreign_binding_refused_before_sql(self):
        binding = self.enroll(self.db)
        with self.assertRaises(ConfigurationDenied):
            self.replace(transaction_coordination=binding)
        pool = ExecutorPool(lambda: self.fail('foreign acquisition'))
        self.addCleanup(pool.close)
        other = self.enroll(pool)
        with self.guard():
            for binding, error in ((other, ValueError), (False, TypeError)):
                with self.subTest(error=error), self.assertRaises(error):
                    self.replace(transaction_coordination=binding)
        self.assertEqual(self.connection.events, [])

    def test_executor_replaced_while_pool_connects_aborts_before_task_sql(self):
        replacement = ExecutorPool(lambda: self.fail('replacement pool acquired'))
        self.addCleanup(replacement.close)

        def factory():
            store.executor = replacement
            return self.db

        pool = ExecutorPool(factory, max_size=1, acquire_timeout=0)
        self.addCleanup(pool.close)
        store = TaskStore(pool)
        binding = self.enroll(pool)
        with self.guard(), self.assertRaisesRegex(ValueError, 'executor changed'):
            store.replace_llm_profiles_idle(10, 20, 4, [7], transaction_coordination=binding)
        self.assertEqual(self.queries(), [(DATABASE, ()), (EXCLUSIVE, (123,))])
        self.assertEqual(self.connection.events[-2:], [('rollback', 1), ('close', 0)])
        self.assertEqual(self.connection.rows['task']['chain'], [8])

    def test_validation_before_admission_or_transaction(self):
        binding = self.enroll(self.db)
        with self.assertRaises(TaskInvalid):
            TaskStore(self.db).replace_llm_profiles_idle(10, 20, 4, [7, 7], transaction_coordination=binding)
        self.assertEqual(self.connection.events, [])

    def test_database_failure_and_contention_precede_all_task_queries(self):
        binding = self.enroll(self.db)
        for database, lock, queries, error in [
            (('other',), (True,), [DATABASE], TransactionCoordinationError),
            (('lab',), (False,), [DATABASE, EXCLUSIVE], TransactionCoordinationBusy),
            (('lab',), (1,), [DATABASE, EXCLUSIVE], TransactionCoordinationError),
        ]:
            self.connection.database_row, self.connection.lock_row = database, lock
            self.connection.events.clear()
            with self.subTest(database=database, lock=lock), self.guard(), self.assertRaises(error):
                self.replace(transaction_coordination=binding)
            self.assertEqual([s for s, _ in self.queries()], queries)
            self.assertEqual(self.connection.events[-1], ('rollback', 1))

    def test_lifecycle_revision_selection_and_quota_still_gate_removal(self):
        binding = self.enroll(self.db)
        for status, revision, available, blocked in [('running', 4, {5, 7}, False),
                ('done', 3, {5, 7}, False), ('done', 4, {5}, False), ('created', 4, {5, 7}, True)]:
            self.connection.rows['task'].update(status=status, revision=revision)
            self.connection.rows['available'] = available
            self.connection.quota_blocked = blocked
            self.connection.events.clear()
            with self.subTest(status=status, revision=revision), self.guard(), self.assertRaises(TaskInvalid):
                self.replace(transaction_coordination=binding)
            self.assertFalse(any(s.startswith('DELETE') for s, _ in self.queries()))
            self.assertEqual(self.connection.rows['task']['chain'], [8])

    def test_final_cas_miss_rolls_back_partial_chain_reset(self):
        binding = self.enroll(self.db)
        self.connection.cas_misses = True
        with self.guard(), self.assertRaisesRegex(RuntimeError, 'revision update unconfirmed'):
            self.replace(transaction_coordination=binding)
        self.assertEqual(self.connection.rows['task'], dict(status='done', revision=4, chain=[8], active=8))
        self.assertEqual(self.connection.events[-1], ('rollback', 1))

    def test_uncertain_commit_exposes_original_error_without_replay(self):
        binding = self.enroll(self.db)
        error = ControlFlow('driver did not acknowledge commit')
        self.connection.commit_error = error
        with self.assertRaises(ControlFlow) as raised, self.guard():
            self.replace(transaction_coordination=binding)
        self.assertIs(raised.exception, error)
        self.assertEqual(self.connection.rows['task']['revision'], 5)
        self.assertEqual(self.connection.events.count(('commit', 1)), 1)
        self.assertEqual(sum(s.startswith('UPDATE tasks') for s, _ in self.queries()), 1)

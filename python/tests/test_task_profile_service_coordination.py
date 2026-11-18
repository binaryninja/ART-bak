"""Existing-row service tests with an injected driver and original store/owner."""
from contextlib import contextmanager, nullcontext
import unittest

from artex.configuration_owner import ConfigurationOwner, ConfigurationPermissions
from artex.db.agents import AgentStore
from artex.db.configuration_ownership import bind_executor
from artex.db.pool import ExecutorPool
from artex.db.postgres import PostgresExecutor
from artex.db.profiles import ProfileStore
from artex.db.tasks import TaskStore
from artex.db.transaction_coordination import TransactionCoordinationBinding
from artex.server.core import Request
from artex.server.task_profiles import TaskProfileBinding, TaskProfileService
from test_configuration_task_bindings import Driver, Cursor
from test_transaction_coordination import DATABASE, EXCLUSIVE


OPERATION = 'task_profiles.replace_idle'


class ServiceDriver(Driver):
    busy = False
    lose_ack = False
    mutated = False

    @contextmanager
    def transaction(self):
        with super().transaction() as transaction:
            yield transaction
        if self.mutated and self.lose_ack:
            raise RuntimeError('synthetic commit acknowledgement lost')


class ServiceCursor(Cursor):
    def execute(self, sql, params):
        if sql in (DATABASE, EXCLUSIVE):
            self.driver.queries.append((sql, params))
            self.rows = [('fixture',)] if sql == DATABASE else [(not self.driver.busy,)]
        else:
            super().execute(sql, params)
            if sql.startswith('UPDATE tasks SET active_llm_profile_id'):
                self.driver.mutated = True


class TaskProfileServiceCoordinationTests(unittest.TestCase):
    def setUp(self):
        self.driver = ServiceDriver()
        original = PostgresExecutor(self.driver, cursor_factory=ServiceCursor)
        self.pool = ExecutorPool(lambda: original, max_size=1, acquire_timeout=0)
        self.addCleanup(self.pool.close)
        perms = ConfigurationPermissions(read=frozenset(), write=frozenset({OPERATION}))
        self.owner = ConfigurationOwner('fixture', participants={'operator': perms})
        self.grant = self.owner.issue('operator')
        bind_executor(self.pool, self.owner)
        self.option = TransactionCoordinationBinding(self.pool, owner=self.owner, domain='fixture',
                                                     database_name='fixture', key=42)
        self.request = Request('PUT', '/api/tasks/10/llm', body=b'{"llm_profile_ids":[7]}')
        self.task_binding = TaskProfileBinding(10, 20, 'fixture-revision')
        self.service = self.make(transaction_coordination=self.option)

    def make(self, tasks=None, **options):
        return TaskProfileService(
            tasks or TaskStore(self.pool), ProfileStore(self.pool), AgentStore(self.pool),
            resolve=lambda request, task_id: self.task_binding if task_id == 10 else None,
            authorize=lambda request, action, binding, change: request is self.request and action == 'replace',
            profile_authorize=lambda request, binding, profile: request is self.request and profile == 7,
            current=lambda binding: binding is self.task_binding, lifecycle_guard=lambda binding: nullcontext(),
            configuration_valid=lambda profile: profile.id == 7, environment=lambda *args: None,
            mutation_authorize=lambda request, binding, change: change.profile_ids == (7,),
            idle=lambda binding: binding is self.task_binding,
            coordinate=lambda binding, change, write: write(), **options)

    def call(self, *, admit=True):
        guard = self.owner.guard(self.grant, OPERATION, mode='write',
                                 expected=self.owner.snapshot(self.grant), timeout=0) if admit else nullcontext()
        with guard:
            return self.service.handle('replace', self.request, {'id': '10'})

    def queries(self, prefix):
        return [(sql, params) for sql, params in self.driver.queries if sql.startswith(prefix)]

    def test_existing_row_on_original_pool_and_confirmed_commit(self):
        result = self.call()
        self.assertEqual(result.status, 200, result.body)
        self.assertEqual(result.json()['llm_profile_ids'], [7])
        self.assertEqual(self.driver.state['revision'], 5)
        self.assertEqual(self.queries(EXCLUSIVE), [(EXCLUSIVE, (42,))])
        sql = [s for s, _ in self.driver.queries]
        self.assertEqual(sql[sql.index(EXCLUSIVE)-1], DATABASE)
        self.assertTrue(sql[sql.index(EXCLUSIVE)+1].endswith('FOR UPDATE'))

    def test_legacy_uses_ordinary_store_transaction(self):
        self.service = self.make()
        self.assertEqual(self.call().status, 200)
        self.assertFalse(self.queries(DATABASE))

    def test_option_type_executor_and_store_identity_required(self):
        other = ExecutorPool(lambda: self.fail('foreign pool acquired'))
        self.addCleanup(other.close)
        bind_executor(other, self.owner)
        foreign = TransactionCoordinationBinding(other, owner=self.owner, domain='fixture',
                                                 database_name='fixture', key=42)
        for option in (False, foreign):
            with self.subTest(option=option), self.assertRaises(ValueError):
                self.make(transaction_coordination=option)
        class OtherStore(TaskStore):
            pass
        with self.assertRaises(ValueError):
            self.make(tasks=OtherStore(self.pool), transaction_coordination=self.option)
        self.assertEqual(self.driver.queries, [])

    def test_coordinator_cannot_replace_store_or_disable_coordination(self):
        for name, value in [('tasks', TaskStore(self.pool)), ('profiles', ProfileStore(self.pool)),
                            ('agents', AgentStore(self.pool)), ('transaction_coordination', None)]:
            self.service = self.make(transaction_coordination=self.option)
            def coordinate(binding, change, write):
                setattr(self.service, name, value)
                return write()
            self.service.coordinate = coordinate
            with self.subTest(name=name):
                response = self.call()
                self.assertEqual(response.status, 503)
                self.assertNotIn('committed', response.json())
                self.assertFalse(self.queries(EXCLUSIVE))
                self.assertEqual(self.driver.state['revision'], 4)

    def test_busy_and_missing_admission_refuse_before_mutation_without_retry(self):
        self.driver.busy = True
        for admit, tries in [(False, 0), (True, 1)]:
            self.driver.queries.clear()
            with self.subTest(admit=admit):
                response = self.call(admit=admit)
                self.assertEqual(response.status, 503)
                if admit:
                    self.assertIsNone(response.json()['committed'])
                    self.assertTrue(response.json()['needs_reconciliation'])
                else:
                    self.assertNotIn('committed', response.json())
                    self.assertEqual(self.driver.queries, [])
                self.assertEqual(len(self.queries(EXCLUSIVE)), tries)
                self.assertFalse(self.queries('DELETE FROM task_llm_profiles'))
                self.assertEqual(self.driver.state['revision'], 4)

    def test_original_lifecycle_guard_supplies_admission_only_while_entered(self):
        def guard(binding):
            return self.owner.guard(self.grant, OPERATION, mode='write',
                                    expected=self.owner.snapshot(self.grant), timeout=0)
        self.service.guard = guard
        self.assertEqual(self.call(admit=False).status, 200)
        self.assertEqual(self.driver.state['revision'], 5)

    def test_revocation_after_authorization_precedes_existing_task_read(self):
        def authorize(*args):
            self.owner.revoke(self.grant)
            return True
        self.service.authorize = authorize
        self.service.guard = lambda binding: self.owner.guard(
            self.grant, OPERATION, mode='write', expected=self.owner.snapshot(self.grant), timeout=0)
        response = self.call(admit=False)
        self.assertEqual(response.status, 503)
        self.assertNotIn('committed', response.json())
        self.assertEqual(self.driver.queries, [])

    def test_swapped_writer_cannot_manufacture_a_successful_receipt(self):
        substituted_calls = []
        def cached_revision(*args, **kwargs):
            substituted_calls.append(args)
            return 5
        def coordinate(binding, change, write):
            self.service.tasks.replace_llm_profiles_idle = cached_revision
            return write()
        self.service.coordinate = coordinate
        response = self.call()
        self.assertEqual(response.status, 503)
        self.assertNotIn('committed', response.json())
        self.assertFalse(substituted_calls)
        self.assertFalse(self.queries(EXCLUSIVE))
        self.assertEqual(self.driver.state['revision'], 4)

    def test_changed_task_and_profile_read_methods_fail_before_reading(self):
        for attribute, name in [('tasks', 'get'), ('tasks', '_hydrate'),
                                ('profiles', 'get'), ('profiles', '_rows')]:
            self.service = self.make(transaction_coordination=self.option)
            def authorize(*args):
                store = getattr(self.service, attribute)
                setattr(store, name, lambda *args: self.fail('substituted read invoked'))
                return True
            self.service.authorize = authorize
            with self.subTest(attribute=attribute, name=name):
                response = self.call()
                self.assertEqual(response.status, 503)
                self.assertEqual(self.driver.queries, [])
                self.assertEqual(self.driver.state['revision'], 4)

    def test_commit_ack_loss_keeps_uncertain_outcome(self):
        self.driver.lose_ack = True
        response = self.call()
        self.assertEqual(response.status, 503)
        self.assertIsNone(response.json()['committed'])
        self.assertEqual(self.driver.state['revision'], 5)
        self.assertEqual(len(self.queries('UPDATE tasks SET active_llm_profile_id')), 1)

    def test_exit_callback_removing_binding_after_commit_needs_reconciliation(self):
        @contextmanager
        def guard(binding):
            yield
            self.service.transaction_coordination = None
        self.service.guard = guard
        response = self.call()
        self.assertEqual(response.status, 503)
        self.assertIs(response.json()['committed'], True)
        self.assertTrue(response.json()['needs_reconciliation'])
        self.assertEqual(self.driver.state['revision'], 5)

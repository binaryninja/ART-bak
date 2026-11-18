"""Inert enrolled existing-task/conversation writers and provider construction.

Actual original stores/adapter/factory/resolver and production configuration owner;
only connection, transport and finite recorded task-policy decisions are synthetic.
No task creation, restore, startup, actual DB or provider request is performed.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import gc
import json
import sys
import threading
import time
from types import SimpleNamespace
import unittest
import weakref

from artex.configuration_owner import ConfigurationOwner, ConfigurationPermissions, ConfigurationOwnershipError
from artex.db.configuration_ownership import bind_executor, bind_factory, is_bound
from artex.db.postgres import PostgresExecutor
from artex.db.tasks import TaskStore, TaskInvalid, TASK_COLS
from artex.db.conversations import ConversationStore, ConversationMissing
from artex.db.agents import AgentStore, COLUMNS as AGENT_COLUMNS
from artex.db.profiles import ProfileStore, PRIVATE_COLUMNS
from artex.providers.factory import ProfileFactory
from artex.providers import Response
from artex.agent.task_providers import TaskProviderResolver, TaskProviderUnavailable
from artex.agent.goals import GoalScope

CHAIN = 'task_profiles.replace_idle'
CONVERSATION = 'conversations.update_profile'
BUILD = 'factory.build'
STAMP = datetime(2024, 1, 1, tzinfo=timezone.utc)


class Halt(BaseException):
    pass


class Driver:
    def __init__(self):
        self.autocommit = True
        self.info = SimpleNamespace(transaction_status=0)
        self.closed = False
        self.frames = []
        self.events = []
        self.queries = []
        self.state = {'revision': 4, 'chain': [7], 'active': 7, 'conversation': 7}
        self.model = 'fixture-model'
        self.streaming = False
        self.error = None
        self.task_exists = True
        self.conversation_exists = True
        self.on_query = None

    @contextmanager
    def transaction(self):
        before = deepcopy(self.state)
        self.frames.append(before)
        self.info.transaction_status = 2
        self.events.append('begin')
        try:
            yield self
        except BaseException:
            self.state = before
            self.events.append('rollback')
            raise
        else:
            self.events.append('commit')
        finally:
            self.frames.pop()
            self.info.transaction_status = 2 if self.frames else 0

    def close(self):
        self.closed = True


class Cursor:
    def __init__(self, driver):
        self.driver = driver
        self.rows = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params):
        d = self.driver
        d.queries.append((sql, params))
        if d.on_query is not None:
            d.on_query(sql, params)
        if d.error is not None:
            raise d.error
        self.rows = []
        self.rowcount = 1
        if sql.startswith('SELECT status,paused,queued,llm_chain_revision FROM tasks'):
            assert params == (10, 20)
            self.rows = [('done', False, False, d.state['revision'])] if d.task_exists else []
        elif sql.startswith('SELECT id FROM llm_profiles WHERE id=ANY'):
            self.rows = [(i,) for i in sorted(params[0])]
        elif sql == 'DELETE FROM task_llm_profiles WHERE task_id=$1':
            d.state['chain'] = []
        elif sql.startswith('INSERT INTO task_llm_profiles('):
            assert params[2] == len(d.state['chain'])
            d.state['chain'].append(params[1])
        elif sql.startswith('UPDATE tasks SET active_llm_profile_id=$2,llm_profile_id=$2,llm_chain_revision='):
            if params[2] == d.state['revision']:
                d.state['revision'] += 1
                d.state['active'] = params[1]
                self.rows = [(d.state['revision'],)]
        elif sql == 'SELECT id FROM conversations WHERE id=$1 FOR UPDATE':
            assert params == (12,)
            self.rows = [(12,)] if d.conversation_exists else []
        elif sql == 'SELECT id FROM llm_profiles WHERE id=$1 FOR KEY SHARE':
            self.rows = [(params[0],)]
        elif sql == 'UPDATE conversations SET llm_profile_id=$2 WHERE id=$1':
            d.state['conversation'] = params[1]
        elif sql == 'SELECT ' + TASK_COLS + ' FROM tasks WHERE id=$1 AND deleted_at IS NULL':
            assert params == (10,)
            self.rows = [(10, 'Existing fixture', None, '', 'Description', 'Goal', 20,
                          'done', False, False, None, '', 7, 7, '', None, STAMP, None,
                          0, 600, True, None, None)]
        elif sql.startswith('SELECT t.id, t.llm_profile_id, t.active_llm_profile_id, t.llm_chain_revision,'):
            self.rows = [(10, 7, 7, d.state['revision'], 7, 0, 'ready', '', None)]
        elif sql.startswith('SELECT task_id, source_task_id FROM task_relations') or sql.startswith('SELECT task_id, company_id FROM task_scope'):
            self.rows = []
        elif sql == 'SELECT ' + AGENT_COLUMNS + ' FROM agents WHERE key=$1':
            assert params == ('goals',)
            self.rows = [(1, 'goals', 'Goals', 'Fixture', 'goals', True, True, 0, 1200,
                          False, False, '', 0, '', 0, 'serial', 'all', 5, None)]
        elif sql == 'SELECT ' + PRIVATE_COLUMNS + ' FROM llm_profiles WHERE id=$1':
            assert params == (7,)
            self.rows = [(7, 'Fixture', 'openai', 'https://example.invalid/v1', '',
                          d.model, 'synthetic-not-a-credential', 0.0, 0.0, 200, '',
                          True, 0, False, '', d.streaming, 0, '', '', 0, 0, 0, 0, 0, 0)]
        else:
            raise AssertionError('Unexpected original non-held SQL: ' + sql)
        if sql.startswith('SELECT') or 'RETURNING' in sql:
            self.rowcount = len(self.rows)

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None


def resources(*, bound=True, read=frozenset({BUILD}), write=frozenset({CHAIN, CONVERSATION})):
    d = Driver()
    executor = PostgresExecutor(d, cursor_factory=Cursor)
    owner = ConfigurationOwner('existing-bindings-fixture', participants={
        'recorded-operator': ConfigurationPermissions(read=read, write=write)})
    capability = owner.issue('recorded-operator')
    if bound:
        bind_executor(executor, owner)
    return SimpleNamespace(driver=d, executor=executor, owner=owner, capability=capability,
                           tasks=TaskStore(executor), conversations=ConversationStore(executor))


def admitted(h, operation, mode='write', expected=None):
    return h.owner.guard(h.capability, operation, mode=mode,
                         expected=h.owner.snapshot(h.capability) if expected is None else expected,
                         timeout=0.2)


def write(h, operation):
    if operation == CHAIN:
        return h.tasks.replace_llm_profiles_idle(10, 20, 4, [7, 3], 3)
    return h.conversations.update_profile(12, 3)


class StoreEnrollmentTests(unittest.TestCase):
    def test_unbound_writers_preserve_original_query_order(self):
        h = resources(bound=False)
        self.assertEqual(write(h, CHAIN), 5)
        write(h, CONVERSATION)
        self.assertEqual(h.driver.state, {'revision': 5, 'chain': [7, 3], 'active': 3, 'conversation': 3})
        self.assertEqual(h.driver.events, ['begin', 'commit', 'begin', 'commit'])
        self.assertIn('FOR UPDATE', h.driver.queries[0][0])
        self.assertIn('FOR SHARE', h.driver.queries[1][0])
        self.assertEqual(h.driver.queries[-3][0], 'SELECT id FROM conversations WHERE id=$1 FOR UPDATE')
        self.assertEqual(h.driver.queries[-2][0], 'SELECT id FROM llm_profiles WHERE id=$1 FOR KEY SHARE')

    def test_all_instances_on_enrolled_executor_refuse_before_sql(self):
        h = resources()
        h.tasks = TaskStore(h.executor)
        h.conversations = ConversationStore(h.executor)
        for operation in (CHAIN, CONVERSATION):
            with self.subTest(operation=operation), self.assertRaises(ConfigurationOwnershipError):
                write(h, operation)
        self.assertEqual(h.driver.queries, [])
        self.assertEqual(h.driver.events, [])

    def test_matching_writer_uses_original_sql_and_clear_semantics(self):
        h = resources()
        with admitted(h, CHAIN):
            self.assertEqual(write(h, CHAIN), 5)
        with admitted(h, CONVERSATION):
            h.conversations.update_profile(12, None)
        self.assertIsNone(h.driver.state['conversation'])
        self.assertEqual(h.driver.state['chain'], [7, 3])
        self.assertEqual(len(h.driver.frames), 0)

    def test_wrong_active_operation_does_not_borrow_another_right(self):
        h = resources()
        with admitted(h, CONVERSATION):
            with self.assertRaises(ConfigurationOwnershipError):
                write(h, CHAIN)
        self.assertEqual(h.driver.queries, [])
        self.assertEqual(h.driver.events, [])

    def test_read_scope_cannot_write(self):
        h = resources(read=frozenset({BUILD, CHAIN, CONVERSATION}))
        for operation in (CHAIN, CONVERSATION):
            with admitted(h, operation, 'read'):
                with self.assertRaises(ConfigurationOwnershipError):
                    write(h, operation)
        self.assertEqual(h.driver.queries, [])
        self.assertEqual(h.driver.events, [])

    def test_foreign_owner_scope_cannot_write(self):
        h, other = resources(), resources()
        with admitted(other, CHAIN):
            with self.assertRaises(ConfigurationOwnershipError):
                write(h, CHAIN)
        self.assertEqual(h.driver.queries, [])

    def test_stale_expected_generation_refuses_before_sql(self):
        h = resources()
        old = h.owner.snapshot(h.capability)
        with admitted(h, CHAIN):
            pass
        with self.assertRaises(ConfigurationOwnershipError):
            with admitted(h, CHAIN, expected=old):
                write(h, CHAIN)
        self.assertEqual(h.driver.queries, [])

    def test_revoked_active_writer_cannot_reach_sql(self):
        h = resources()
        with self.assertRaises(ConfigurationOwnershipError):
            with admitted(h, CHAIN):
                h.owner.revoke(h.capability)
                write(h, CHAIN)
        self.assertEqual(h.driver.queries, [])
        self.assertEqual(h.driver.events, [])

    def test_closed_and_collected_owner_bindings_do_not_restore_legacy(self):
        h = resources()
        h.owner.close()
        with self.assertRaises(ConfigurationOwnershipError):
            write(h, CONVERSATION)
        self.assertTrue(is_bound(h.executor))
        h = resources()
        owner_ref = weakref.ref(h.owner)
        del h.owner
        del h.capability
        gc.collect()
        self.assertIsNone(owner_ref())
        self.assertTrue(is_bound(h.executor))
        with self.assertRaises(ConfigurationOwnershipError):
            write(h, CHAIN)
        self.assertEqual(h.driver.queries, [])

    def test_unowned_ambient_transaction_cannot_acquire_guard(self):
        h = resources()
        with h.executor.transaction():
            with self.assertRaises(ConfigurationOwnershipError):
                with admitted(h, CHAIN):
                    write(h, CHAIN)
        self.assertEqual(h.driver.queries, [])

    def _revoked_while_waiting_for_original_executor(self, operation):
        h = resources()
        original_lock = h.executor._lock
        entered = threading.Event()
        revoke_done = threading.Event()
        finished = threading.Event()
        probes, errors, observer_errors, profiles = [], [], [], []
        target = PostgresExecutor.transaction.__wrapped__.__code__

        def observer(frame, event, arg):
            # Read-only observation of the original adapter, not a replacement
            # lock or a callback that raises and disables its own profiler.
            if probes or observer_errors or event != 'call' or frame.f_code is not target or frame.f_locals.get('self') is not h.executor:
                return
            try:
                acquired = original_lock.acquire(blocking=False)
                probes.append(acquired)
                if acquired:
                    original_lock.release()
                entered.set()
            except BaseException as error:
                observer_errors.append(error)
                entered.set()

        def work():
            previous = sys.getprofile()
            try:
                sys.setprofile(observer)
                with admitted(h, operation):
                    write(h, operation)
            except BaseException as error:
                errors.append(error)
            finally:
                sys.setprofile(previous)
                profiles.append(sys.getprofile() is previous)
                finished.set()

        def revoke():
            try:
                h.owner.revoke(h.capability)
            except BaseException as error:
                observer_errors.append(error)
            finally:
                revoke_done.set()

        worker = threading.Thread(target=work, daemon=True)
        revoker = threading.Thread(target=revoke, daemon=True)
        started = []
        observed = signalled = False
        original_lock.acquire()
        try:
            worker.start(); started.append(worker)
            observed = entered.wait(1.0)
            if observed:
                revoker.start(); started.append(revoker)
                signalled = revoke_done.wait(1.0)
        finally:
            original_lock.release()
            # A failed wait cannot skip cleanup of another known worker. No
            # forced stop; unresolved daemon workers are an explicit failure.
            deadline = time.monotonic() + 2.0
            for thread in started:
                thread.join(max(0.0, deadline - time.monotonic()))
        self.assertFalse([t.name for t in started if t.is_alive()], 'Unresolved fixture worker')
        self.assertTrue(observed, 'Original transaction entry was not observed')
        self.assertTrue(signalled, 'Revocation did not finish while executor was held')
        self.assertEqual(observer_errors, [])
        self.assertEqual(probes, [False])
        self.assertEqual(profiles, [True])
        self.assertTrue(finished.is_set())
        self.assertIs(h.executor._lock, original_lock)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ConfigurationOwnershipError)
        self.assertEqual(h.driver.queries, [])
        self.assertEqual(h.driver.events, ['begin', 'rollback'])
        self.assertEqual(h.driver.frames, [])
        self.assertEqual(h.driver.state, {'revision': 4, 'chain': [7], 'active': 7, 'conversation': 7})

    def test_task_chain_revoked_during_original_executor_lock_wait(self):
        self._revoked_while_waiting_for_original_executor(CHAIN)

    def test_conversation_binding_revoked_during_original_executor_lock_wait(self):
        self._revoked_while_waiting_for_original_executor(CONVERSATION)

    def test_original_errors_and_controls_preserved(self):
        for operation in (CHAIN, CONVERSATION):
            for error in (RuntimeError('synthetic statement failure'), Halt('synthetic control')):
                with self.subTest(operation=operation, error=type(error).__name__):
                    h = resources()
                    h.driver.error = error
                    with self.assertRaises(type(error)) as caught:
                        with admitted(h, operation):
                            write(h, operation)
                    self.assertIs(caught.exception, error)
                    self.assertEqual(h.driver.events, ['begin', 'rollback'])
                    self.assertEqual(h.driver.frames, [])

    def test_original_missing_record_and_input_validation(self):
        h = resources()
        with self.assertRaises(TaskInvalid):
            h.tasks.replace_llm_profiles_idle(0, 20, 4, [7])
        self.assertEqual(h.driver.queries, [])
        h.driver.conversation_exists = False
        with self.assertRaises(ConversationMissing):
            with admitted(h, CONVERSATION):
                write(h, CONVERSATION)
        self.assertEqual(h.driver.events, ['begin', 'rollback'])


class Context:
    def check(self):
        return None


class ResolverFixture:
    """Finite seeded records and explicit configuration/task/execution decisions."""
    def __init__(self, *, bound=True):
        h = resources(bound=bound)
        self.__dict__.update(vars(h))
        self.snapshot = {'task_id': 10, 'exploration_id': 20}
        self.configuration_live = True
        self.admit_configuration_owner = True
        self.task_allowed = True
        self.profile_allowed = True
        self.execution_live = True
        self.configuration_depth = 0
        self.execution_depth = 0
        self.constructions = []
        self.requests = []
        self.guard_entries = 0
        self.on_guard_entry = None
        self.construction_error = None
        self.on_construct = None
        self.closes = []
        self.suppress = False
        self.decisions = []
        self.scope = None
        self.factory = ProfileFactory(self.transport)
        if bound:
            bind_factory(self.factory, self.owner)
        self.resolver = TaskProviderResolver(self.tasks, AgentStore(self.executor),
            ProfileStore(self.executor), self.factory, current=self.current,
            lifecycle_guard=self.configuration_guard, authorize_task=self.authorize_task,
            authorize_profile=self.authorize_profile, allow_global=False)

    def current(self, snapshot):
        self.decisions.append(('current', deepcopy(snapshot)))
        return self.configuration_live and snapshot == self.snapshot

    def authorize_task(self, snapshot, role):
        self.decisions.append(('task', deepcopy(snapshot), role))
        return self.task_allowed and snapshot == self.snapshot and role == 'goals'

    def authorize_profile(self, snapshot, role, profile_id):
        self.decisions.append(('profile', deepcopy(snapshot), role, profile_id))
        return self.profile_allowed and snapshot == self.snapshot and role == 'goals' and profile_id == 7

    @contextmanager
    def configuration_guard(self, snapshot):
        if snapshot != self.snapshot:
            raise AssertionError('Unexpected task configuration snapshot')
        self.guard_entries += 1
        if self.on_guard_entry is not None:
            self.on_guard_entry(self.guard_entries)
        if not self.admit_configuration_owner:
            # Negative fixture only: same original supplied callback identity,
            # but no actual owner admission. Production must refuse before SQL.
            self.configuration_depth += 1
            try:
                yield
            finally:
                self.configuration_depth -= 1
            return
        try:
            with admitted(self, BUILD, 'read'):
                self.configuration_depth += 1
                try:
                    yield
                finally:
                    self.configuration_depth -= 1
        except BaseException:
            if not self.suppress:
                raise

    @contextmanager
    def execution_guard(self, scope):
        if scope is not self.scope:
            raise AssertionError('Unexpected execution scope')
        self.execution_depth += 1
        try:
            yield
        finally:
            self.execution_depth -= 1

    def execution_current(self, scope):
        return self.execution_live and scope is self.scope

    def transport(self, config):
        self.constructions.append((config.model, self.configuration_depth, self.execution_depth))
        if self.construction_error is not None:
            raise self.construction_error
        if self.on_construct is not None:
            self.on_construct()
        def request(*args, **kwargs):
            self.requests.append((self.configuration_depth, self.execution_depth))
            if self.configuration_depth or self.execution_depth:
                raise AssertionError('A supplied guard crossed a synthetic provider request')
            if self.driver.streaming:
                data = b'data: {"choices":[{"delta":{"content":"synthetic"},"finish_reason":null}]}\n\ndata: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
                return Response(200, data, lambda: self.closes.append((self.configuration_depth, self.execution_depth)))
            return Response(200, json.dumps({'choices': [{'message': {'role': 'assistant',
                'content': 'synthetic'}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 1, 'completion_tokens': 1}}))
        return request

    def binding(self):
        model = self.resolver.select(self.snapshot, 'goals').model
        self.scope = GoalScope(10, 20, 71, 'recorded-execution-r1', model, 'inert fixture label')
        return self.resolver.bind(self.scope, 'goals', model,
            current=self.execution_current, lifecycle_guard=self.execution_guard)


class ResolverOwnershipTests(unittest.TestCase):
    def test_public_selection_and_binding_do_not_construct(self):
        h = ResolverFixture()
        h.binding()
        self.assertEqual(h.constructions, [])
        self.assertEqual(h.requests, [])

    def test_owned_construction_uses_original_guard_request_is_outside(self):
        h = ResolverFixture()
        binding = h.binding()
        binding.provider.complete(Context(), {'messages': []})
        self.assertEqual(h.constructions, [('fixture-model', 1, 0)])
        self.assertEqual(h.requests, [(0, 0)])
        self.assertTrue(any(item[0] == 'task' for item in h.decisions))
        self.assertTrue(any(item[0] == 'profile' for item in h.decisions))

    def test_unbound_default_build_remains_after_selection_guard(self):
        h = ResolverFixture(bound=False)
        binding = h.binding()
        binding.provider.complete(Context(), {'messages': []})
        self.assertEqual(h.constructions, [('fixture-model', 0, 0)])
        self.assertEqual(h.requests, [(0, 0)])

    def test_fresh_model_mismatch_before_build(self):
        h = ResolverFixture()
        binding = h.binding()
        # The first guard belongs to check(); the second is the fresh owned
        # construction selection. A seeded nonparticipating edit is intentional.
        target = h.guard_entries + 2
        def intervene(count):
            if count == target:
                h.driver.model = 'changed-before-build'
        h.on_guard_entry = intervene
        with self.assertRaises(TaskProviderUnavailable):
            binding.provider._provider(Context())
        self.assertEqual(h.constructions, [])
        self.assertEqual(h.requests, [])

    def test_task_profile_and_execution_decisions_remain_required(self):
        for name in ('task_allowed', 'profile_allowed', 'configuration_live', 'execution_live'):
            with self.subTest(name=name):
                h = ResolverFixture()
                binding = h.binding()
                setattr(h, name, False)
                with self.assertRaises(TaskProviderUnavailable):
                    binding.provider._provider(Context())
                self.assertEqual(h.constructions, [])
                self.assertEqual(h.requests, [])

    def test_suppressed_construction_control_cannot_return_partial_provider(self):
        h = ResolverFixture()
        binding = h.binding()
        error = Halt('synthetic construction control')
        h.construction_error = error
        h.suppress = True
        with self.assertRaises(Halt) as caught:
            binding.provider._provider(Context())
        self.assertIs(caught.exception, error)
        self.assertEqual(h.requests, [])
        self.assertEqual(h.configuration_depth, 0)

    def test_guard_exit_failure_never_dispatches(self):
        h = ResolverFixture()
        binding = h.binding()
        h.on_construct = lambda: h.owner.revoke(h.capability)
        with self.assertRaises(ConfigurationOwnershipError):
            binding.provider._provider(Context())
        self.assertEqual(h.requests, [])

    def test_stream_request_and_cleanup_run_outside_supplied_guards(self):
        h = ResolverFixture()
        h.driver.streaming = True
        binding = h.binding()
        events = list(binding.provider.stream(Context(), {'messages': []}))
        self.assertTrue(events)
        self.assertEqual(h.constructions, [('fixture-model', 1, 0)])
        self.assertEqual(h.requests, [(0, 0)])
        self.assertEqual(h.closes, [(0, 0)])

    def test_suppressed_final_grant_failure_cannot_return_constructed_provider(self):
        h = ResolverFixture()
        binding = h.binding()
        h.on_construct = lambda: setattr(h, 'task_allowed', False)
        h.suppress = True
        with self.assertRaises(TaskProviderUnavailable):
            binding.provider._provider(Context())
        self.assertEqual(h.constructions, [('fixture-model', 1, 0)])
        self.assertEqual(h.requests, [])
        self.assertEqual(h.configuration_depth, 0)

    def test_owned_binding_guard_without_owner_lease_refuses_before_initial_check_sql(self):
        h = ResolverFixture()
        binding = h.binding()
        original_guard = h.resolver.guard
        h.admit_configuration_owner = False
        h.driver.queries.clear(); h.driver.events.clear()
        with self.assertRaises(ConfigurationOwnershipError):
            binding.provider._provider(Context())
        self.assertIs(h.resolver.guard, original_guard)
        self.assertEqual(h.driver.queries, [])
        self.assertEqual(h.driver.events, [])
        self.assertEqual(h.constructions, [])
        self.assertEqual(h.requests, [])
        self.assertEqual(h.configuration_depth, 0)

    def test_owned_public_selection_requires_actual_owner_read_scope_before_sql(self):
        h = ResolverFixture()
        h.admit_configuration_owner = False
        with self.assertRaises(ConfigurationOwnershipError):
            h.resolver.select(h.snapshot, 'goals')
        self.assertEqual(h.driver.queries, [])
        self.assertEqual(h.driver.events, [])
        self.assertEqual(h.constructions, [])
        self.assertEqual(h.requests, [])
        self.assertEqual(h.configuration_depth, 0)

    def test_factory_enrolled_after_binding_requires_enrolled_pinned_executor(self):
        h = ResolverFixture(bound=False)
        binding = h.binding()
        bind_factory(h.factory, h.owner)
        h.driver.queries.clear(); h.driver.events.clear()
        with self.assertRaises(ConfigurationOwnershipError):
            binding.provider._provider(Context())
        self.assertEqual(h.driver.queries, [])
        self.assertEqual(h.driver.events, [])
        self.assertEqual(h.constructions, [])
        self.assertEqual(h.requests, [])
        self.assertFalse(is_bound(h.executor))

    def test_factory_and_pinned_executor_must_have_same_owner(self):
        h = ResolverFixture(bound=False)
        binding = h.binding()
        other = ConfigurationOwner('different-configuration-domain', participants={
            'different-operator': ConfigurationPermissions(read=frozenset({BUILD}), write=frozenset())})
        bind_factory(h.factory, h.owner)
        bind_executor(h.executor, other)
        h.driver.queries.clear(); h.driver.events.clear()
        with self.assertRaises(ConfigurationOwnershipError):
            binding.provider._provider(Context())
        self.assertEqual(h.driver.queries, [])
        self.assertEqual(h.driver.events, [])
        self.assertEqual(h.constructions, [])
        self.assertEqual(h.requests, [])
        self.assertTrue(is_bound(h.executor))

    def test_closed_factory_binding_remains_owned_and_refuses(self):
        h = ResolverFixture()
        binding = h.binding()
        h.owner.close()
        self.assertTrue(is_bound(h.factory))
        with self.assertRaises(ConfigurationOwnershipError):
            binding.provider._provider(Context())
        self.assertEqual(h.constructions, [])
        self.assertEqual(h.requests, [])


if __name__ == '__main__':
    unittest.main()

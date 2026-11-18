"""Author tests with actual stores/executors and a status-faithful injected driver.

Base SQL fixture adapted with permission from log_history's concurrent
host-contract author fixture. These shared-helper results are author validation,
not independent review. No PostgreSQL, network, runtime or provider calls.
"""
from copy import deepcopy
from dataclasses import dataclass, replace
import re
from types import SimpleNamespace
import unittest

from artex.db.pool import ExecutorPool
from artex.db.postgres import PostgresExecutor, CommitBoundaryError
from artex.db.profiles import (LLMProfile, PRIVATE_COLUMNS, ProfileStore,
    ProfileInvalid, ProfileNotFound, ProfileStorageError, RetryOverride, RetryRule)
from artex.providers.factory import ProfileFactory
from artex.providers.committed_profiles import (
    CommittedProfileCoordinator, CommittedProfileApplyPending, ProfileMutationBusy,
)

class _RollbackSignal(BaseException):
    """Injected control outcome with the driver's suppression semantics."""


def _profile(identifier=1, **changes):
    profile = LLMProfile(id=identifier, name=f'Fixture {identifier}',
                         model=f'old-model-{identifier}',
                         api_key='synthetic-fixture-key', is_default=identifier == 1)
    return replace(profile, **changes)


def _private_row(profile):
    return (profile.id, profile.name, profile.format, profile.base_url,
            profile.proxy, profile.model, profile.api_key,
            profile.rate_per_second, profile.rate_per_minute,
            profile.context_window_k, profile.reasoning_effort,
            profile.is_default, profile.priority, profile.pool_exclude,
            profile.thinking_type, profile.streaming, profile.max_tokens,
            profile.max_tokens_field, profile.session_header_key,
            *profile.retry.values())


class _Transaction:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        c = self.connection
        self.outer = not c.frames
        self.before = deepcopy(c.rows)
        c.frames.append(self)
        c.info.transaction_status = 2
        c.events.append('outer.begin' if self.outer else 'savepoint.begin')
        return self

    def __exit__(self, kind, error, tb):
        c = self.connection
        try:
            if error is not None:
                c.rows = self.before
                c.events.append('outer.rollback' if self.outer else 'savepoint.rollback')
                return error is c.suppressed_signal
            if self.outer:
                if c.before_commit is not None:
                    c.before_commit()
                # A commit acknowledgement failure may have committed. Tests
                # must not infer a rollback or safely repeat the mutation.
                c.committed = deepcopy(c.rows)
                c.events.append('outer.commit')
                if c.commit_error is not None and (
                        not c.commit_error_after_mutation or c.mutations):
                    raise c.commit_error
            else:
                c.events.append('savepoint.release')
            return False
        finally:
            assert c.frames[-1] is self
            c.frames.pop()
            c.info.transaction_status = 2 if c.frames else 0


class _Connection:
    def __init__(self, profiles):
        self.autocommit = True
        self.info = SimpleNamespace(transaction_status=0)
        self.closed = False
        self.rows = {p.id: deepcopy(p) for p in profiles}
        self.committed = deepcopy(self.rows)
        self.frames = []
        self.events = []
        self.mutations = []
        self.before_commit = None
        self.commit_error = None
        self.commit_error_after_mutation = False
        self.suppressed_signal = None
        self.fail_after_default_clear = None
        self.ignore_update = False
        self.insert_receipt = None
        self.pool_omit_active = False

    def transaction(self):
        return _Transaction(self)

    def close(self):
        self.closed = True
        self.events.append('connection.close')


class _Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.result = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params):
        c = self.connection
        if not c.frames:
            raise AssertionError('Original adapter must own a transaction')
        c.events.append('query')
        select = 'SELECT ' + PRIVATE_COLUMNS + ' FROM llm_profiles'
        if sql == select + ' WHERE id=$1':
            values = [c.rows[params[0]]] if params[0] in c.rows else []
        elif sql == select + ' WHERE is_default LIMIT 1':
            values = [p for p in c.rows.values() if p.is_default][:1]
        elif sql == select + " WHERE COALESCE(api_key,'') <> '' AND (is_default OR NOT pool_exclude) ORDER BY is_default DESC, priority DESC, id ASC":
            values = sorted((p for p in c.rows.values() if not (c.pool_omit_active and p.is_default) and p.api_key and
                             (p.is_default or not p.pool_exclude)),
                            key=lambda p: (not p.is_default, -p.priority, p.id))
        else:
            return self._mutate(sql, params)
        self.result = [_private_row(p) for p in values]
        self.rowcount = len(self.result)

    def _mutate(self, sql, params):
        c = self.connection
        c.mutations.append(sql)
        if sql == 'UPDATE llm_profiles SET is_default=false WHERE is_default':
            self.rowcount = sum(p.is_default for p in c.rows.values())
            for profile in c.rows.values():
                profile.is_default = False
            if c.fail_after_default_clear is not None:
                raise c.fail_after_default_clear
            return
        if sql == 'UPDATE llm_profiles SET is_default=true WHERE id=$1':
            self.rowcount = int(params[0] in c.rows)
            if self.rowcount:
                c.rows[params[0]].is_default = True
            return
        if sql.startswith('INSERT INTO llm_profiles('):
            columns = sql.split('(', 1)[1].split(')', 1)[0].split(',')
            identifier = max(c.rows, default=0) + 1
            profile = LLMProfile(id=identifier)
            values = dict(zip(columns, params))
            for field, value in values.items():
                if not field.startswith('retry_'):
                    setattr(profile, field, value)
            profile.retry = RetryOverride(*(RetryRule(values['retry_' + name + '_attempts'],
                values['retry_' + name + '_interval_ms']) for name in ('connect', 'empty', 'stream')))
            c.rows[identifier] = profile
            self.result = [(identifier if c.insert_receipt is None else c.insert_receipt,)]
            self.rowcount = 1
            return
        if sql.startswith('UPDATE llm_profiles SET name=$1,'):
            identifier = params[-1]
            self.rowcount = int(identifier in c.rows)
            if not self.rowcount or c.ignore_update:
                return
            assignments = re.findall(r'(\w+)=(?:NULLIF\()?\$(\d+)', sql.split(' WHERE ')[0])
            for field, index in assignments:
                # These fixtures use default retries; their native parameters
                # still reach this cursor. No broad SQL parser is fabricated.
                if field.startswith('retry_'):
                    if params[int(index) - 1] != 0:
                        raise AssertionError('Unexpected nondefault fixture retry')
                    continue
                setattr(c.rows[identifier], field, params[int(index) - 1])
            return
        raise AssertionError('Unexpected original profile SQL shape')

    def fetchall(self):
        return list(self.result)

    def fetchone(self):
        return self.result[0] if self.result else None


class _ConstructionOnlyTransport:
    def __init__(self):
        self.models = []
        self.requests = 0
        self.refuse = None

    def __call__(self, config):
        self.models.append(config.model)
        if self.refuse is not None and self.refuse(config):
            raise RuntimeError('injected construction failure')
        return self.request

    def request(self, *args, **kwargs):
        self.requests += 1
        raise AssertionError('Provider execution is outside host-contract acceptance')


class CommittedProfilesTests(unittest.TestCase):
    def make(self, *, profiles=None, pooled=False, pending_error=CommittedProfileApplyPending):
        connection = _Connection(profiles if profiles is not None else [_profile(), _profile(2)])
        adapter = PostgresExecutor(connection, cursor_factory=_Cursor, owns_connection=True)
        if pooled:
            executor = ExecutorPool(lambda: adapter, max_size=1, acquire_timeout=0)
            self.addCleanup(lambda: self.assertTrue(executor.close(timeout=0).drained))
        else:
            executor = adapter
            self.addCleanup(adapter.close)
        transport = _ConstructionOnlyTransport()
        self.addCleanup(lambda: self.assertEqual(transport.requests, 0))
        factory = ProfileFactory(transport)
        store = ProfileStore(executor)
        coordinator = CommittedProfileCoordinator(store, factory, pending_error=pending_error)
        return SimpleNamespace(connection=connection, executor=executor, store=store,
            factory=factory, transport=transport, coordinator=coordinator)

    def unavailable(self, h, phase, committed):
        self.assertIsNone(h.coordinator.runtime()[0])
        self.assertFalse(h.coordinator.snapshot()['configured'])
        self.assertEqual(h.coordinator.status().phase, phase)
        self.assertIs(h.coordinator.status().committed, committed)
        self.assertFalse(h.coordinator.status().runtime_applied)

    def test_constructor_is_inert_and_status_is_safe_immutable(self):
        h = self.make()
        self.assertEqual(h.connection.events, [])
        self.assertEqual(h.transport.models, [])
        self.unavailable(h, 'unconfigured', False)
        with self.assertRaises(AttributeError):
            h.coordinator.status().committed = True
        self.assertNotIn('synthetic-fixture-key', repr(h.coordinator.status()))

    def test_create_uses_owned_boundaries_and_save_never_selects_active(self):
        h = self.make(profiles=[])
        candidate = _profile(0, model='created', is_default=True)
        observations = []
        def observe(config):
            observations.append((len(h.connection.frames), h.coordinator.status().phase,
                                 h.coordinator.runtime()[0]))
            return False
        h.transport.refuse = observe
        identifier = h.coordinator.save(candidate)
        self.assertEqual(identifier, 1)
        self.assertEqual(candidate.id, 0)
        self.assertTrue(candidate.is_default)
        self.assertFalse(h.connection.committed[1].is_default)
        self.assertFalse(h.coordinator.snapshot()['configured'])
        self.assertIsNotNone(h.coordinator.runtime()[0])
        self.assertEqual(observations, [(0, 'in_progress', None), (0, 'committed_pending', None)])
        self.assertEqual(h.connection.events.count('outer.commit'), 2)
        self.assertEqual(h.coordinator.status().profile_id, identifier)
        self.assertTrue(h.coordinator.status().committed)
        self.assertEqual(h.factory._cache, {})

    def test_update_preserves_blank_key_selection_and_input(self):
        h = self.make()
        candidate = _profile(model='changed', api_key='', api_key_hint='display-only', is_default=False)
        self.assertEqual(h.coordinator.save(candidate), 1)
        self.assertEqual(candidate.api_key, '')
        self.assertFalse(candidate.is_default)
        saved = h.store.get(1)
        self.assertEqual(saved.api_key, 'synthetic-fixture-key')
        self.assertTrue(saved.is_default)
        self.assertEqual(h.coordinator.snapshot()['model'], 'changed')
        snapshot = h.coordinator.snapshot()
        snapshot['model'] = 'caller edit'
        self.assertEqual(h.coordinator.snapshot()['model'], 'changed')

    def test_profile_subclass_uses_original_store_base_projection(self):
        constructed = []
        @dataclass
        class CallerProfile(LLMProfile):
            caller_only: str = 'caller metadata'

            def __post_init__(self):
                constructed.append(self.model)
        h = self.make()
        update = CallerProfile(id=1, name='Caller profile', model='subclass', api_key='')
        self.assertEqual(h.coordinator.save(update), 1)
        self.assertIs(type(h.store.get(1)), LLMProfile)
        self.assertEqual(h.coordinator.snapshot()['model'], 'subclass')
        create = CallerProfile(name='Caller create', model='created', api_key='synthetic')
        identifier = h.coordinator.save(create)
        self.assertEqual(identifier, 3)
        self.assertIs(type(h.store.get(identifier)), LLMProfile)
        self.assertEqual(h.store.get(identifier).model, 'created')
        self.assertFalse(hasattr(h.store.get(identifier), 'caller_only'))
        self.assertEqual(constructed, ['subclass', 'created'])
        self.assertIs(type(update), CallerProfile)
        self.assertEqual(update.api_key, '')

    def test_active_switch_and_reload_use_original_pool_resources(self):
        h = self.make(pooled=True)
        old = h.factory.build(_profile())
        cache_before = dict(h.factory._cache)
        h.coordinator.reload()
        previous_pool = h.coordinator.runtime()[0]
        self.assertIsNone(h.coordinator.set_active(2))
        self.assertEqual(h.coordinator.snapshot()['model'], 'old-model-2')
        self.assertFalse(h.connection.committed[1].is_default)
        self.assertTrue(h.connection.committed[2].is_default)
        self.assertIsNot(h.coordinator.runtime()[0], previous_pool)
        self.assertEqual(h.factory._cache, cache_before)
        self.assertIs(h.factory.build(_profile()), old)
        self.assertEqual(h.executor.state().leased, 0)
        h.coordinator.reload()
        self.assertFalse(h.coordinator.status().committed)
        self.assertTrue(h.coordinator.status().runtime_applied)

    def test_publication_is_unavailable_before_commit_without_holding_state_lock(self):
        h = self.make()
        h.coordinator.reload()
        observed = []
        def before_commit():
            observed.append((h.coordinator.runtime(), h.coordinator.status(),
                             h.connection.committed[1].model))
        h.connection.before_commit = before_commit
        h.coordinator.save(_profile(model='changed'))
        self.assertEqual(len(observed), 2)
        self.assertTrue(all(runtime[0] is None and not runtime[1]['configured']
                            for runtime, _, _ in observed))
        self.assertEqual([status.phase for _, status, _ in observed], ['in_progress', 'outcome_unknown'])
        self.assertEqual([model for _, _, model in observed], ['old-model-1', 'old-model-1'])

    def test_invalid_preflight_clears_publication_without_mutating(self):
        h = self.make()
        h.coordinator.reload()
        with self.assertRaises(ProfileInvalid):
            h.coordinator.save(_profile(format='openai-responses'))
        self.assertEqual(h.connection.mutations, [])
        self.unavailable(h, 'not_committed', False)

    def test_ambient_transaction_rejected_before_construction_or_queries(self):
        for operation in ('save', 'set_active', 'reload'):
            with self.subTest(operation=operation):
                h = self.make()
                with h.executor.transaction():
                    with self.assertRaises(CommitBoundaryError):
                        getattr(h.coordinator, operation)(*({'save': [_profile(0)], 'set_active': [2], 'reload': []}[operation]))
                    self.assertNotIn('query', h.connection.events)
                    self.assertEqual(h.transport.models, [])
                self.unavailable(h, 'not_committed', False)

    def test_original_store_error_rolls_back_and_does_not_publish(self):
        h = self.make()
        h.connection.fail_after_default_clear = RuntimeError('injected failure')
        with self.assertRaises(ProfileStorageError):
            h.coordinator.set_active(2)
        self.assertTrue(h.connection.rows[1].is_default)
        self.assertFalse(h.connection.rows[2].is_default)
        self.assertIn('outer.rollback', h.connection.events)
        self.unavailable(h, 'outcome_unknown', None)
        h.connection.fail_after_default_clear = None
        writes = len(h.connection.mutations)
        h.coordinator.reload()
        self.assertEqual(len(h.connection.mutations), writes)
        self.assertEqual(h.coordinator.snapshot()['model'], 'old-model-1')

    def test_exposed_mutation_commit_error_preserves_identity_and_uncertainty(self):
        for error in (RuntimeError('commit acknowledgement lost'), _RollbackSignal('cancel during commit')):
            with self.subTest(error_type=type(error).__name__):
                h = self.make()
                h.connection.commit_error = error
                h.connection.commit_error_after_mutation = True
                with self.assertRaises(type(error)) as raised:
                    h.coordinator.save(_profile(model='possibly committed'))
                self.assertIs(raised.exception, error)
                self.assertEqual(h.connection.committed[1].model, 'possibly committed')
                self.assertEqual(h.transport.models, ['possibly committed'])
                self.unavailable(h, 'outcome_unknown', None)

    def test_apply_failure_has_confirmed_outcome_and_explicit_reload_does_not_replay(self):
        h = self.make()
        h.transport.refuse = lambda config: bool(h.connection.mutations)
        with self.assertRaises(CommittedProfileApplyPending) as raised:
            h.coordinator.save(_profile(model='persisted'))
        self.assertEqual(raised.exception.profile_id, 1)
        self.assertEqual(raised.exception.operation, 'save')
        self.assertTrue(raised.exception.committed)
        self.assertIsNone(raised.exception.__cause__)
        self.assertTrue(raised.exception.__suppress_context__)
        self.assertEqual(h.connection.committed[1].model, 'persisted')
        self.unavailable(h, 'committed_pending', True)
        writes = len(h.connection.mutations)
        h.transport.refuse = None
        h.coordinator.reload()
        self.assertEqual(len(h.connection.mutations), writes)
        self.assertEqual(h.coordinator.snapshot()['model'], 'persisted')

    def test_postcommit_baseexception_retains_original_identity_and_pending_status(self):
        h = self.make()
        error = _RollbackSignal('construction cancelled')
        def refuse(config):
            if h.connection.mutations:
                raise error
            return False
        h.transport.refuse = refuse
        with self.assertRaises(_RollbackSignal) as raised:
            h.coordinator.set_active(2)
        self.assertIs(raised.exception, error)
        self.assertTrue(h.connection.committed[2].is_default)
        self.unavailable(h, 'committed_pending', True)

    def test_suppressed_nested_rollback_cannot_be_success(self):
        h = self.make()
        error = _RollbackSignal('rollback inside original store')
        h.connection.suppressed_signal = h.connection.fail_after_default_clear = error
        with self.assertRaises(_RollbackSignal) as raised:
            h.coordinator.set_active(2)
        self.assertIs(raised.exception, error)
        self.assertEqual(h.connection.events.count('outer.commit'), 1)
        self.assertTrue(h.connection.rows[1].is_default)
        self.assertFalse(h.connection.rows[2].is_default)
        self.unavailable(h, 'outcome_unknown', None)

    def test_preparation_gap_recheck_refuses_changed_profile(self):
        h = self.make()
        def external_writer(config):
            h.connection.rows[1].model = 'external change'
            return False
        h.transport.refuse = external_writer
        with self.assertRaisesRegex(ProfileInvalid, 'changed during preparation'):
            h.coordinator.save(_profile(model='attempt'))
        self.assertEqual(h.connection.mutations, [])
        self.assertEqual(h.connection.rows[1].model, 'external change')
        self.unavailable(h, 'not_committed', False)

    def test_saved_readback_mismatch_prevents_commit(self):
        h = self.make()
        h.connection.ignore_update = True
        with self.assertRaisesRegex(ProfileStorageError, 'read-back'):
            h.coordinator.save(_profile(model='not saved'))
        self.assertEqual(h.connection.committed[1].model, 'old-model-1')
        self.assertEqual(h.connection.events.count('outer.commit'), 1)
        self.unavailable(h, 'outcome_unknown', None)

    def test_invalid_create_receipts_roll_back_without_adopting_identity(self):
        for receipt in (True, 0, -1, 2**63, '1'):
            with self.subTest(receipt=receipt):
                h = self.make(profiles=[])
                h.connection.insert_receipt = receipt
                with self.assertRaisesRegex(ProfileStorageError, 'identifier'):
                    h.coordinator.save(_profile(0))
                self.assertEqual(h.connection.rows, {})
                self.assertIsNone(h.coordinator.status().profile_id)
                self.unavailable(h, 'outcome_unknown', None)

    def test_inconsistent_active_pool_rows_abort_before_mutation_commit(self):
        h = self.make()
        h.connection.pool_omit_active = True
        with self.assertRaisesRegex(ProfileInvalid, 'inconsistent'):
            h.coordinator.set_active(2)
        self.assertTrue(h.connection.rows[1].is_default)
        self.assertFalse(h.connection.rows[2].is_default)
        self.unavailable(h, 'outcome_unknown', None)

    def test_reentrant_operation_refused_before_sql_or_status_changes(self):
        h = self.make()
        observations = []
        def reenter(config):
            before = (len(h.connection.events), h.coordinator.status())
            with self.assertRaises(ProfileMutationBusy):
                h.coordinator.reload()
            observations.append(before == (len(h.connection.events), h.coordinator.status()))
            return False
        h.transport.refuse = reenter
        h.coordinator.save(_profile(model='changed'))
        self.assertTrue(observations and all(observations))
        self.assertEqual(h.coordinator.status().phase, 'applied')

    def test_changed_store_executor_refused_before_sql(self):
        h, replacement = self.make(), self.make()
        h.store.executor = replacement.executor
        with self.assertRaisesRegex(ProfileInvalid, 'binding'):
            h.coordinator.save(_profile(model='changed'))
        self.assertEqual(h.connection.events, [])
        self.assertEqual(replacement.connection.events, [])
        self.assertEqual(h.transport.models, [])
        self.unavailable(h, 'not_committed', False)

    def test_custom_pending_error_only_after_confirmed_mutation(self):
        class HostPending(RuntimeError):
            pass
        h = self.make(pending_error=HostPending)
        h.transport.refuse = lambda config: True
        with self.assertRaises(ProfileInvalid):
            h.coordinator.save(_profile(model='not persisted'))
        self.unavailable(h, 'not_committed', False)
        h.transport.refuse = lambda config: bool(h.connection.mutations)
        with self.assertRaises(HostPending):
            h.coordinator.save(_profile(model='persisted'))
        self.unavailable(h, 'committed_pending', True)
        with self.assertRaises(ProfileInvalid):
            h.coordinator.reload()
        self.unavailable(h, 'not_committed', False)


if __name__ == '__main__':
    unittest.main()

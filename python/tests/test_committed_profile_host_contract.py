"""Opt-in profile host acceptance using original objects and an injected driver.

No database connection, provider request, application, task or authority callback
is executed. The driver below models only the exact original ProfileStore SQL
needed by these tests; it is not a PostgreSQL substitute or owner implementation.
"""
from copy import deepcopy
from dataclasses import replace
import json
import re
from types import SimpleNamespace
import unittest

from artex.db.pool import ExecutorPool
from artex.db.postgres import PostgresExecutor
from artex.db.profiles import LLMProfile, PRIVATE_COLUMNS, ProfileStore
from artex.providers.factory import ProfileFactory
from artex.providers.committed_profiles import (
    CommittedProfileApplyPending, CommittedProfileCoordinator,
)
from artex.server.core import Request
from artex.server.profiles import ProfileApplyPending, ProfileService


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
            values = sorted((p for p in c.rows.values() if p.api_key and
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
        if sql.startswith('UPDATE llm_profiles SET name=$1,'):
            identifier = params[-1]
            self.rowcount = int(identifier in c.rows)
            if not self.rowcount:
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


class CommittedProfileHostContractTests(unittest.TestCase):
    def make(self, *, pooled=False, pending_error=None):
        connection = _Connection([_profile(), _profile(2)])
        adapter = PostgresExecutor(connection, cursor_factory=_Cursor,
                                   owns_connection=True)
        leases = []
        if pooled:
            def acquire():
                leases.append(adapter)
                return adapter
            executor = ExecutorPool(acquire, max_size=1, acquire_timeout=0)
            def close():
                self.assertTrue(executor.close(timeout=0).drained)
            self.addCleanup(close)
        else:
            executor = adapter
            self.addCleanup(adapter.close)
        transport = _ConstructionOnlyTransport()
        self.addCleanup(lambda: self.assertEqual(transport.requests, 0))
        factory = ProfileFactory(transport)
        store = ProfileStore(executor)
        options = {} if pending_error is None else {'pending_error': pending_error}
        coordinator = CommittedProfileCoordinator(store, factory, **options)
        return SimpleNamespace(connection=connection, adapter=adapter,
                               executor=executor, leases=leases, store=store,
                               factory=factory, transport=transport,
                               coordinator=coordinator)

    def test_original_resources_publish_only_after_commit_and_preserve_blank_key(self):
        h = self.make()
        self.assertFalse(h.connection.events)
        h.coordinator.reload()
        old_pool, old_snapshot = h.coordinator.runtime()
        candidate = _profile(model='new-model', api_key='')
        observations = []
        def before_commit():
            observations.append((bool(h.connection.mutations),
                                 h.connection.committed[1].model,
                                 h.coordinator.runtime()))
        h.connection.before_commit = before_commit
        self.assertEqual(h.coordinator.save(candidate), 1)
        h.connection.before_commit = None
        self.assertEqual(candidate.api_key, '')
        self.assertEqual(h.store.get(1).api_key, 'synthetic-fixture-key')
        mutation_observations = [o for o in observations if o[0]]
        self.assertTrue(mutation_observations)
        # The callback runs at every outer exit; only the mutation boundary
        # still observes the old committed model, before the new publication.
        boundary = next(o for o in mutation_observations if o[1] == 'old-model-1')
        self.assertIsNone(boundary[2][0])
        self.assertFalse(boundary[2][1]['configured'])
        pool, snapshot = h.coordinator.runtime()
        self.assertEqual(snapshot['model'], 'new-model')
        self.assertIsNot(pool, old_pool)
        self.assertEqual(old_snapshot['model'], 'old-model-1')
        self.assertEqual(old_pool.head.model, 'old-model-1')
        status = h.coordinator.status()
        self.assertIs(status.committed, True)
        self.assertIs(status.runtime_applied, True)
        self.assertEqual(status.profile_id, 1)
        self.assertIs(h.store.executor, h.adapter)

    def test_pool_path_keeps_original_store_executor_and_one_owned_connection(self):
        h = self.make(pooled=True)
        h.coordinator.reload()
        self.assertIsNone(h.coordinator.set_active(2))
        self.assertEqual(h.coordinator.snapshot()['model'], 'old-model-2')
        self.assertTrue(h.connection.committed[2].is_default)
        self.assertFalse(h.connection.committed[1].is_default)
        self.assertEqual(h.leases, [h.adapter])
        self.assertIs(h.store.executor, h.executor)
        state = h.executor.state()
        self.assertEqual((state.total, state.idle, state.leased), (1, 1, 0))
        self.assertIn('savepoint.begin', h.connection.events)
        self.assertFalse(h.connection.closed)

    def test_caller_owned_transaction_refuses_before_profile_queries(self):
        for pooled in (False, True):
            with self.subTest(pooled=pooled):
                h = self.make(pooled=pooled)
                with h.executor.transaction():
                    events = list(h.connection.events)
                    with self.assertRaises(RuntimeError):
                        h.coordinator.save(_profile(model='must-not-apply'))
                    self.assertEqual(h.connection.events, events)
                    self.assertFalse(h.connection.mutations)
                self.assertEqual(h.connection.committed[1].model, 'old-model-1')
                self.assertFalse(h.coordinator.snapshot()['configured'])

    def test_suppressed_nested_store_rollback_never_reports_default_switch_success(self):
        for pooled in (False, True):
            with self.subTest(pooled=pooled):
                h = self.make(pooled=pooled)
                h.coordinator.reload()
                signal = _RollbackSignal('injected nested rollback')
                h.connection.suppressed_signal = signal
                h.connection.fail_after_default_clear = signal
                with self.assertRaises(_RollbackSignal) as raised:
                    h.coordinator.set_active(2)
                self.assertIs(raised.exception, signal)
                self.assertTrue(h.connection.committed[1].is_default)
                self.assertFalse(h.connection.committed[2].is_default)
                self.assertFalse(h.coordinator.snapshot()['configured'])
                self.assertIsNot(h.coordinator.status().committed, True)
                self.assertIn('savepoint.rollback', h.connection.events)

    def test_confirmed_commit_then_prepare_failure_is_pending_until_explicit_reload(self):
        h = self.make()
        h.coordinator.reload()
        h.transport.refuse = lambda config: (
            config.model == 'new-model' and
            h.connection.committed[1].model == 'new-model')
        with self.assertRaises(CommittedProfileApplyPending) as raised:
            h.coordinator.save(_profile(model='new-model'))
        self.assertIs(raised.exception.committed, True)
        self.assertIs(raised.exception.runtime_applied, False)
        self.assertEqual(raised.exception.profile_id, 1)
        self.assertEqual(h.connection.committed[1].model, 'new-model')
        self.assertEqual(len(h.connection.mutations), 1)
        self.assertFalse(h.coordinator.snapshot()['configured'])
        self.assertIs(h.coordinator.status().committed, True)
        self.assertIs(h.coordinator.status().runtime_applied, False)
        h.transport.refuse = None
        h.coordinator.reload()
        self.assertEqual(h.coordinator.snapshot()['model'], 'new-model')
        self.assertEqual(len(h.connection.mutations), 1)

    def test_direct_store_bypass_remains_outside_coordinator_publication(self):
        h = self.make()
        h.coordinator.reload()
        retained_pool, retained_snapshot = h.coordinator.runtime()
        h.store.save(_profile(model='direct-writer-model'))
        self.assertEqual(h.connection.committed[1].model, 'direct-writer-model')
        pool, snapshot = h.coordinator.runtime()
        self.assertIs(pool, retained_pool)
        self.assertEqual(snapshot, retained_snapshot)
        self.assertEqual(snapshot['model'], 'old-model-1')

    @staticmethod
    def service_save(service, **changes):
        data = dict(id=1, name='Fixture 1', model='new-model', api_key='')
        data.update(changes)
        # Direct transport-independent handler projection only. No router,
        # positive authorization callback, authenticated host or listener.
        return service._handle('save', Request(
            'POST', '/api/llm/profiles', body=json.dumps(data).encode()))

    def test_explicit_profile_service_adapter_preserves_confirmed_pending_response(self):
        h = self.make(pending_error=ProfileApplyPending)
        service = ProfileService(h.store, coordinator=h.coordinator,
                                 runtime_snapshot=h.coordinator.snapshot)
        h.coordinator.reload()
        h.transport.refuse = lambda config: (
            config.model == 'new-model' and
            h.connection.committed[1].model == 'new-model')
        response = self.service_save(service)
        self.assertEqual(response.status, 503)
        self.assertIs(response.json()['persisted'], True)
        self.assertIs(response.json()['runtime_applied'], False)
        self.assertEqual(h.connection.committed[1].model, 'new-model')
        self.assertNotIn(b'synthetic-fixture-key', response.body)
        self.assertEqual(len(h.connection.mutations), 1)
        self.assertIs(h.coordinator.status().committed, True)

    def test_profile_service_preflight_and_uncertainty_are_not_confirmed_persistence(self):
        for failure in ('preflight', 'uncertain-ack'):
            with self.subTest(failure=failure):
                h = self.make(pending_error=ProfileApplyPending)
                service = ProfileService(h.store, coordinator=h.coordinator,
                                         runtime_snapshot=h.coordinator.snapshot)
                h.coordinator.reload()
                if failure == 'preflight':
                    response = self.service_save(service, proxy='http://fixture.invalid')
                    self.assertEqual(response.status, 400)
                    self.assertFalse(h.connection.mutations)
                else:
                    h.connection.commit_error = RuntimeError('injected acknowledgement loss')
                    h.connection.commit_error_after_mutation = True
                    response = self.service_save(service)
                    self.assertEqual(response.status, 500)
                    self.assertEqual(h.connection.committed[1].model, 'new-model')
                    self.assertIsNone(h.coordinator.status().committed)
                    self.assertEqual(len(h.connection.mutations), 1)
                self.assertNotIn('persisted', response.json())
                self.assertNotIn('runtime_applied', response.json())
                self.assertFalse(h.coordinator.snapshot()['configured'])

    def test_profile_service_success_uses_actual_coordinator_snapshot_contract(self):
        h = self.make(pending_error=ProfileApplyPending)
        service = ProfileService(h.store, coordinator=h.coordinator,
                                 runtime_snapshot=h.coordinator.snapshot)
        saved = self.service_save(service)
        self.assertEqual((saved.status, saved.json()), (200, {'id': 1}))
        response = service._handle('runtime', Request('GET', '/api/llm'))
        self.assertEqual(response.status, 200)
        self.assertTrue(response.json()['configured'])
        self.assertEqual(response.json()['model'], 'new-model')
        self.assertNotIn('api_key', response.json())
        self.assertNotIn(b'synthetic-fixture-key', response.body)

    def test_profile_service_reentrant_busy_response_is_not_confirmed_persistence(self):
        h = self.make(pending_error=ProfileApplyPending)
        service = ProfileService(h.store, coordinator=h.coordinator,
                                 runtime_snapshot=h.coordinator.snapshot)
        h.coordinator.reload()
        reentrant = []
        def observe_busy(config):
            if config.model == 'new-model' and not reentrant:
                reentrant.append(self.service_save(service, model='must-not-write'))
            return False
        h.transport.refuse = observe_busy
        response = self.service_save(service)
        self.assertEqual((response.status, response.json()), (200, {'id': 1}))
        self.assertEqual(len(reentrant), 1)
        self.assertEqual(reentrant[0].status, 500)
        self.assertNotIn('persisted', reentrant[0].json())
        self.assertNotIn('runtime_applied', reentrant[0].json())
        self.assertEqual(h.connection.committed[1].model, 'new-model')
        self.assertEqual(len(h.connection.mutations), 1)


if __name__ == '__main__':
    unittest.main()

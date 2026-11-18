"""Existing conversation metadata writer with an injected transactional driver."""
from contextlib import contextmanager
from types import SimpleNamespace
import unittest

from artex.configuration_owner import ConfigurationDenied, ConfigurationOwner, ConfigurationPermissions
from artex.db.configuration_ownership import bind_executor
from artex.db.conversations import ConversationInvalid, ConversationMissing, ConversationStore
from artex.db.pool import ExecutorPool
from artex.db.postgres import PostgresExecutor
from artex.db.transaction_coordination import (
    TransactionCoordinationBinding, TransactionCoordinationBusy, TransactionCoordinationError,
)
import test_configuration_task_bindings as fixture


_OPERATION = 'conversations.update_profile'
_DATABASE = 'SELECT pg_catalog.current_database()'
_LOCK = 'SELECT pg_catalog.pg_try_advisory_xact_lock($1::pg_catalog.int8)'
_CONVERSATION = 'SELECT id FROM conversations WHERE id=$1 FOR UPDATE'
_PROFILE = 'SELECT id FROM llm_profiles WHERE id=$1 FOR KEY SHARE'
_UPDATE = 'UPDATE conversations SET llm_profile_id=$2 WHERE id=$1'


class _Driver(fixture.Driver):
    def __init__(self):
        super().__init__()
        self.database = 'fixture'
        self.lock = True
        self.profile_exists = True
        self.commit_error = None

    @contextmanager
    def transaction(self):
        with super().transaction():
            yield self
        if self.commit_error is not None:
            raise self.commit_error


class _Cursor(fixture.Cursor):
    def execute(self, sql, params):
        assert self.driver.frames
        if sql == _DATABASE:
            self.rows = [(self.driver.database,)]
        elif sql == _LOCK:
            self.rows = [(self.driver.lock,)]
        elif sql == _PROFILE and not self.driver.profile_exists:
            self.rows = []
        else:
            return super().execute(sql, params)
        self.driver.queries.append((sql, params))
        if self.driver.on_query is not None:
            self.driver.on_query(sql, params)
        self.rowcount = len(self.rows)


class ConversationProfileCoordinationTests(unittest.TestCase):
    def make(self, *, pooled=False):
        driver = _Driver()
        adapter = PostgresExecutor(driver, cursor_factory=_Cursor, owns_connection=True)
        factories = []
        if pooled:
            def acquire():
                factories.append(adapter)
                return adapter
            executor = ExecutorPool(acquire, max_size=1, acquire_timeout=0)
            self.addCleanup(lambda: self.assertTrue(executor.close(timeout=0).drained))
        else:
            executor = adapter
            self.addCleanup(adapter.close)
        owner = ConfigurationOwner('metadata', participants={'editor': ConfigurationPermissions(
            read=frozenset(), write=frozenset({_OPERATION}))})
        self.addCleanup(owner.close)
        bind_executor(executor, owner)
        cap = owner.issue('editor')
        binding = TransactionCoordinationBinding(executor, owner=owner, domain=owner.domain,
                                                database_name='fixture', key=71)
        return SimpleNamespace(driver=driver, store=ConversationStore(executor), binding=binding,
                               factories=factories, owner=owner, cap=cap)

    def guard(self, h):
        return h.owner.guard(h.cap, _OPERATION, mode='write', expected=h.owner.snapshot(h.cap), timeout=0)

    def test_enrolled_pooled_update_and_clear_use_expected_lock_order(self):
        h = self.make(pooled=True)
        with self.guard(h):
            self.assertIsNone(h.store.update_profile(12, 3, transaction_coordination=h.binding))
        self.assertEqual(h.driver.queries, [(_DATABASE, ()), (_LOCK, (71,)), (_CONVERSATION, (12,)),
                                            (_PROFILE, (3,)), (_UPDATE, (12, 3))])
        h.driver.queries.clear()
        with self.guard(h):
            h.store.update_profile(12, None, transaction_coordination=h.binding)
        self.assertEqual(h.driver.queries, [(_DATABASE, ()), (_LOCK, (71,)), (_CONVERSATION, (12,)),
                                            (_UPDATE, (12, None))])
        self.assertEqual(len(h.factories), 1)
        self.assertEqual(h.driver.events, ['begin', 'commit'] * 2)
        self.assertIsNone(h.driver.state['conversation'])

    def test_ids_admission_and_exact_executor_before_transaction(self):
        h, other = self.make(), self.make()
        for args in [(0, 1), (12, False)]:
            with self.assertRaises(ConversationInvalid):
                h.store.update_profile(*args, transaction_coordination=object())
        with self.assertRaises(ConfigurationDenied):
            h.store.update_profile(12, 3, transaction_coordination=h.binding)
        with self.guard(h):
            for binding in (object(), other.binding):
                with self.assertRaises(ConversationInvalid):
                    h.store.update_profile(12, 3, transaction_coordination=binding)
        self.assertEqual(h.driver.events, [])
        self.assertEqual(other.driver.events, [])

    def test_coordination_failure_runs_no_business_sql(self):
        for attr, value, error, queries in [
            ('database', 'other', TransactionCoordinationError, [_DATABASE]),
            ('lock', False, TransactionCoordinationBusy, [_DATABASE, _LOCK]),
            ('lock', 1, TransactionCoordinationError, [_DATABASE, _LOCK]),
        ]:
            with self.subTest(attribute=attr, value=value):
                h = self.make()
                setattr(h.driver, attr, value)
                with self.guard(h), self.assertRaises(error):
                    h.store.update_profile(12, 3, transaction_coordination=h.binding)
                self.assertEqual([q for q, _ in h.driver.queries], queries)
                self.assertEqual(h.driver.events, ['begin', 'rollback'])

    def test_executor_replacement_during_lock_acquisition_rolls_back(self):
        h, other = self.make(), self.make()
        def replace_executor(sql, params):
            if sql == _LOCK:
                h.store.executor = other.store.executor
        h.driver.on_query = replace_executor
        with self.guard(h), self.assertRaisesRegex(ConversationInvalid, 'executor changed'):
            h.store.update_profile(12, 3, transaction_coordination=h.binding)
        self.assertEqual(h.driver.events, ['begin', 'rollback'])
        self.assertEqual(h.driver.queries, [(_DATABASE, ()), (_LOCK, (71,))])
        self.assertEqual(other.driver.events, [])

    def test_missing_metadata_rolls_back(self):
        for attr, error in [('conversation_exists', ConversationMissing), ('profile_exists', ConversationInvalid)]:
            with self.subTest(attribute=attr):
                h = self.make()
                setattr(h.driver, attr, False)
                with self.guard(h), self.assertRaises(error):
                    h.store.update_profile(12, 3, transaction_coordination=h.binding)
                self.assertEqual(h.driver.events, ['begin', 'rollback'])
                self.assertNotIn(_UPDATE, [q for q, _ in h.driver.queries])
                self.assertEqual(h.driver.state['conversation'], 7)

    def test_commit_acknowledgement_error_propagates_once(self):
        h = self.make()
        h.driver.commit_error = RuntimeError('acknowledgement lost')
        with self.guard(h), self.assertRaises(RuntimeError) as caught:
            h.store.update_profile(12, 3, transaction_coordination=h.binding)
        self.assertIs(caught.exception, h.driver.commit_error)
        self.assertEqual(h.driver.events, ['begin', 'commit'])
        self.assertEqual(h.driver.state['conversation'], 3)
        self.assertEqual(h.driver.queries.count((_UPDATE, (12, 3))), 1)

    def test_explicit_none_keeps_original_nested_transaction(self):
        h = self.make()
        with self.guard(h), h.store.executor.transaction():
            h.store.update_profile(12, None, transaction_coordination=None)
        self.assertEqual(h.driver.events, ['begin', 'begin', 'commit', 'commit'])
        self.assertEqual(h.driver.queries, [(_CONVERSATION, (12,)), (_UPDATE, (12, None))])

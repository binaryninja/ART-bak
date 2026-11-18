"""Existing PATCH route with real store/owner/executor and injected row data."""
from dataclasses import replace
from types import SimpleNamespace
import unittest

from artex.configuration_owner import ConfigurationDenied, ConfigurationOwner, ConfigurationPermissions
from artex.db.configuration_ownership import bind_executor
from artex.db.conversations import COLS, ConversationStore, ConversationUnavailable
from artex.db.postgres import PostgresExecutor
from artex.db.transaction_coordination import TransactionCoordinationBinding
from artex.server.conversations import ConversationService
from artex.server.core import Request, Router
import test_conversation_profile_coordination as fixture


_GET = 'SELECT ' + COLS + ' FROM conversations WHERE id=$1'


class _Cursor(fixture._Cursor):
    def execute(self, sql, params):
        if sql == _GET:
            assert params == (12,)
            driver = self.driver
            driver.queries.append((sql, params))
            if driver.on_query is not None:
                driver.on_query(sql, params)
            self.rows = [(12, 'local', 'Fixture', 7, None, None, None)] if driver.conversation_exists else []
            self.rowcount = len(self.rows)
            return
        super().execute(sql, params)


class ConversationServiceCoordinationTests(unittest.TestCase):
    def make(self, *, coordinated=True):
        driver = fixture._Driver()
        executor = PostgresExecutor(driver, cursor_factory=_Cursor, owns_connection=True)
        self.addCleanup(executor.close)
        owner = ConfigurationOwner('metadata', participants={'editor': ConfigurationPermissions(
            read=frozenset(), write=frozenset({fixture._OPERATION}))})
        self.addCleanup(owner.close)
        cap = owner.issue('editor')
        bind_executor(executor, owner)
        binding = TransactionCoordinationBinding(executor, owner=owner, domain=owner.domain,
                                                 database_name='fixture', key=71)
        store = ConversationStore(executor)
        service = ConversationService(store, profile_available=lambda identifier: identifier == 3,
                                      transaction_coordination=binding if coordinated else None)
        router = Router(lambda request: request.header('Authorization') == 'fixture')
        service.register(router)
        return SimpleNamespace(driver=driver, store=store, owner=owner, cap=cap, service=service,
                               router=router, binding=binding)

    def guard(self, h):
        return h.owner.guard(h.cap, fixture._OPERATION, mode='write', expected=h.owner.snapshot(h.cap), timeout=0)

    def patch(self, h, body=b'{"llm_profile_id":3}', auth='fixture'):
        return h.router.dispatch(Request('PATCH', '/api/conversations/12/profile', {'Authorization': auth}, body))

    def test_coordinated_route_and_explicit_none(self):
        for coordinated in (True, False):
            with self.subTest(coordinated=coordinated):
                h = self.make(coordinated=coordinated)
                with self.guard(h):
                    response = self.patch(h)
                self.assertEqual(response.status, 200)
                self.assertEqual(response.json(), {'ok': True})
                expected = [fixture._DATABASE, fixture._LOCK] if coordinated else []
                self.assertEqual([q for q, _ in h.driver.queries],
                                 [_GET, *expected, fixture._CONVERSATION, fixture._PROFILE, fixture._UPDATE])
                self.assertEqual(h.driver.state['conversation'], 3)

    def test_option_store_executor_and_writer_replacements_in_callback(self):
        for changed in ('remove', 'replace', 'add', 'store', 'executor', 'writer'):
            with self.subTest(changed=changed):
                h, other = self.make(coordinated=changed != 'add'), self.make()
                def change(identifier):
                    if changed == 'remove':
                        h.service.transaction_coordination = None
                    elif changed == 'replace':
                        h.service.transaction_coordination = replace(h.binding, key=72)
                    elif changed == 'add':
                        h.service.transaction_coordination = h.binding
                    elif changed == 'store':
                        h.service.store = ConversationStore(h.store.executor)
                    elif changed == 'executor':
                        h.store.executor = other.store.executor
                    else:
                        h.store.update_profile = lambda *args, **kwargs: self.fail('substituted writer')
                    return identifier == 3
                h.service.profile_available = change
                with self.guard(h):
                    self.assertEqual(self.patch(h).status, 503)
                self.assertEqual([q for q, _ in h.driver.queries], [_GET])
                self.assertEqual(other.driver.queries, [])

    def test_get_callback_drop_is_detected_before_profile_availability(self):
        h = self.make()
        h.driver.on_query = lambda sql, params: setattr(h.service, 'transaction_coordination', None)
        h.service.profile_available = lambda identifier: self.fail('invalid binding')
        with self.guard(h):
            self.assertEqual(self.patch(h).status, 503)
        self.assertEqual([q for q, _ in h.driver.queries], [_GET])

    def test_revocation_in_existence_read_refuses_availability_and_write(self):
        h = self.make()
        h.driver.on_query = lambda sql, params: h.owner.revoke(h.cap)
        h.service.profile_available = lambda identifier: self.fail('revoked writer')
        with self.assertRaises(ConfigurationDenied), self.guard(h):
            self.assertEqual(self.patch(h).status, 500)
        self.assertEqual([q for q, _ in h.driver.queries], [_GET])

    def test_ctor_refuses_mismatch_and_non_original_store(self):
        h, other = self.make(), self.make()
        for store, binding in ((h.store, other.binding), (object(), h.binding), (h.store, object())):
            with self.assertRaises(ConversationUnavailable):
                ConversationService(store, transaction_coordination=binding)
        self.assertEqual(h.driver.queries, [])
        self.assertEqual(other.driver.queries, [])

    def test_auth_admission_and_normal_request_errors(self):
        h = self.make()
        self.assertEqual(self.patch(h, auth='wrong').status, 401)
        self.assertEqual(self.patch(h).status, 500)
        self.assertEqual(h.driver.queries, [])
        with self.guard(h):
            self.assertEqual(self.patch(h, b'{"wrong":3}').status, 400)
            self.assertEqual(self.patch(h, b'{"llm_profile_id":9}').status, 400)
            h.driver.conversation_exists = False
            self.assertEqual(self.patch(h).status, 404)
        self.assertTrue(all(q == _GET for q, _ in h.driver.queries))

    def test_busy_or_uncertain_commit_is_sanitized_and_never_retried(self):
        for busy in (True, False):
            with self.subTest(busy=busy):
                h = self.make()
                h.driver.lock = not busy
                if not busy:
                    def lose_ack(sql, params):
                        if sql == fixture._UPDATE:
                            h.driver.commit_error = RuntimeError('fixture acknowledgement loss')
                    h.driver.on_query = lose_ack
                with self.guard(h):
                    response = self.patch(h)
                self.assertEqual(response.status, 500)
                self.assertNotIn('acknowledgement', str(response.json()))
                queries = [q for q, _ in h.driver.queries]
                self.assertEqual(queries.count(fixture._LOCK), 1)
                self.assertEqual(queries.count(fixture._UPDATE), 0 if busy else 1)
                self.assertEqual(h.driver.state['conversation'], 7 if busy else 3)

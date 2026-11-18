"""Inert service tests with original resources and explicit recorded decisions.

The inherited profile SQL fixture uses Python rows, not a database. Synthetic
JWTs and the conditional owner/request callbacks are test decisions only; they
do not grant production authority or establish cross-process writer exclusion.
No provider request, application builder, runtime or listener is invoked.
"""
from contextlib import contextmanager
from dataclasses import replace
from threading import Lock
import unittest

from artex.db.postgres import PostgresExecutor
from artex.db.profiles import ProfileStore
from artex.providers.factory import ProfileFactory, public_snapshot
from artex.providers.committed_profiles import CommittedProfileCoordinator, ProfilePublicationStatus
from artex.providers.profile_host import ProfileHost, ProfileHostPolicy, ProfileHostReceipt
from artex.server.auth import AuthService, sign_jwt
from artex.server.core import Request, Router
from artex.server.profile_reconciliation import (
    ProfileReconciliationService, RECONCILE_PATH, MAX_BODY, _project,
)
from test_committed_profiles import _Connection, _Cursor, _profile, _ConstructionOnlyTransport


class Abort(BaseException):
    pass


class RecordedOwner:
    """A finite test decision oracle, never a deployment ownership provider."""
    revision = 'fixture-owner-r7'

    def __init__(self):
        self.calls = []
        self.live = True
        self.allowed = True
        self.exit_error = None
        self.revoke_on_exit = False
        self.lock = Lock()

    def current(self, revision):
        self.calls.append(('current', revision))
        return revision == self.revision and self.live

    def authorize(self, operation, value, revision):
        self.calls.append(('authorize', operation, value, revision))
        return (operation == 'reconcile' and value is None
                and revision == self.revision and self.live and self.allowed)

    @contextmanager
    def guard(self, operation, revision):
        if operation != 'reconcile' or revision != self.revision or not self.live:
            raise RuntimeError('Recorded owner did not admit this operation')
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('Recorded owner is already held')
        self.calls.append(('enter', operation, revision))
        try:
            yield
            if self.revoke_on_exit:
                self.live = False
            if self.exit_error is not None:
                raise self.exit_error
        finally:
            self.calls.append(('exit', operation, revision))
            self.lock.release()


class Fixture:
    def __init__(self):
        self.connection = _Connection([_profile()])
        self.executor = PostgresExecutor(self.connection, cursor_factory=_Cursor,
                                         owns_connection=True)
        self.transport = _ConstructionOnlyTransport()
        self.store = ProfileStore(self.executor)
        self.factory = ProfileFactory(self.transport)
        self.coordinator = CommittedProfileCoordinator(self.store, self.factory)
        self.owner = RecordedOwner()
        policy = ProfileHostPolicy(revision=self.owner.revision,
            current=self.owner.current, authorize=self.owner.authorize, guard=self.owner.guard)
        self.host = ProfileHost(self.coordinator, policy=policy)
        self.request_calls = []
        self.selected_request = None
        self.request_decision = True
        self.request_error = None
        self.request_hook = None
        self.service = ProfileReconciliationService(self.host, authorize=self.authorize)
        self.key = b'synthetic-service-test-signing-key'
        self.auth = AuthService(None, None, self.key, clock=lambda: 1000)
        self.router = Router(self.auth.authorized)
        self.service.register(self.router)

    def authorize(self, request):
        self.request_calls.append(request)
        if self.request_error is not None:
            raise self.request_error
        if self.request_hook is not None:
            self.request_hook()
        return self.request_decision if request is self.selected_request else False

    def request(self, *, body=b'', method='POST', target=RECONCILE_PATH, jwt=True):
        headers = {'Authorization': 'Bearer ' + sign_jwt(self.key, now=1000)} if jwt else {}
        request = Request(method, target, headers, body)
        self.selected_request = request
        return request


class ProfileReconciliationTests(unittest.TestCase):
    def make(self):
        fixture = Fixture()
        self.addCleanup(fixture.executor.close)
        self.addCleanup(lambda: self.assertEqual(fixture.transport.requests, 0))
        return fixture

    def no_host_work(self, fixture):
        self.assertEqual(fixture.owner.calls, [])
        self.assertEqual(fixture.connection.events, [])
        self.assertEqual(fixture.transport.models, [])

    def test_constructor_requires_explicit_callback_and_original_host(self):
        h = self.make()
        with self.assertRaises(TypeError):
            ProfileReconciliationService(h.host)
        for callback in (None, True, object()):
            with self.subTest(callback_type=type(callback).__name__):
                with self.assertRaises(ValueError):
                    ProfileReconciliationService(h.host, authorize=callback)
        with self.assertRaises(ValueError):
            ProfileReconciliationService(object(), authorize=h.authorize)
        self.no_host_work(h)

    def test_original_router_jwt_refusal_precedes_request_policy_and_body(self):
        h = self.make()
        response = h.router.dispatch(h.request(body=b'x' * (MAX_BODY + 1), jwt=False))
        self.assertEqual(response.status, 401)
        self.assertEqual(h.request_calls, [])
        self.no_host_work(h)

    def test_request_policy_requires_literal_true_before_invalid_body_or_host(self):
        for decision in (False, None, 0, 1, 'yes', object()):
            with self.subTest(decision_type=type(decision).__name__):
                h = self.make()
                h.request_decision = decision
                request = h.request(body=b'not json')
                response = h.router.dispatch(request)
                self.assertEqual(response.status, 403)
                self.assertEqual(h.request_calls, [request])
                self.assertIs(h.request_calls[0], request)
                self.no_host_work(h)

    def test_request_policy_exception_denies_safely_and_control_flow_survives(self):
        h = self.make()
        h.request_error = RuntimeError('private request policy detail')
        response = h.router.dispatch(h.request())
        self.assertEqual(response.status, 403)
        self.assertNotIn(b'private', response.body)
        self.no_host_work(h)
        error = Abort('request policy interrupted')
        h.request_error = error
        with self.assertRaises(Abort) as raised:
            h.router.dispatch(h.request())
        self.assertIs(raised.exception, error)
        self.no_host_work(h)

    def test_no_get_or_head_reconciliation_and_direct_method_guard(self):
        h = self.make()
        for method in ('GET', 'HEAD', 'DELETE'):
            response = h.router.dispatch(h.request(method=method))
            self.assertEqual(response.status, 405)
            self.assertEqual(response.headers['Allow'], 'POST')
        self.assertEqual(h.request_calls, [])
        direct = h.request(method='GET')
        h.request_decision = False
        self.assertEqual(h.service._handle(direct).status, 403)
        h.request_decision = True
        self.assertEqual(h.service._handle(direct).status, 405)
        self.assertEqual(h.service._handle(h.request(target='/other')).status, 404)
        self.no_host_work(h)

    def test_body_and_query_contract_refuses_before_host_work(self):
        bodies = (b' ', b'[]', b'null', b'true', b'0', b'NaN', b'""', b'{',
                  b'{"grant":true}', b'{"x":1,"x":2}', b'{"nested":{}}',
                  b'\xff', b'\xef\xbb\xbf{}')
        h = self.make()
        for body in bodies:
            with self.subTest(body=body):
                self.assertEqual(h.router.dispatch(h.request(body=body)).status, 400)
        self.assertEqual(h.router.dispatch(h.request(body=b' ' * 255 + b'{}')).status, 413)
        self.assertEqual(h.router.dispatch(h.request(body='{}')).status, 400)
        for suffix in ('?', '?force=true', '?token=anything', '#', '#fragment'):
            with self.subTest(suffix=suffix):
                self.assertEqual(h.router.dispatch(h.request(target=RECONCILE_PATH + suffix)).status, 400)
        self.no_host_work(h)

    def test_absent_empty_and_boundary_object_reconcile_without_mutation(self):
        for body in (b'', b'{}', b' \n{}\t', b' ' * 254 + b'{}'):
            with self.subTest(body_length=len(body)):
                h = self.make()
                request = h.request(body=body)
                response = h.router.dispatch(request)
                self.assertEqual(response.status, 200)
                value = response.json()
                self.assertEqual(set(value), {'reconciled', 'runtime_applied', 'runtime'})
                self.assertIs(value['reconciled'], True)
                self.assertIs(value['runtime_applied'], True)
                self.assertEqual(value['runtime']['model'], 'old-model-1')
                self.assertNotIn(b'synthetic-fixture-key', response.body)
                self.assertNotIn('persisted', value)
                self.assertEqual(h.request_calls, [request])
                self.assertTrue(any(call[0] == 'authorize' for call in h.owner.calls))
                self.assertTrue(any(call[0] == 'enter' for call in h.owner.calls))
                self.assertTrue(any(call[0] == 'exit' for call in h.owner.calls))
                self.assertEqual(h.connection.mutations, [])

    def test_valid_request_still_needs_substantive_host_authority(self):
        h = self.make()
        h.owner.allowed = False
        response = h.router.dispatch(h.request())
        self.assertEqual(response.status, 403)
        self.assertEqual(h.connection.events, [])
        self.assertEqual(h.transport.models, [])
        h.owner.allowed = True
        h.owner.live = False
        response = h.router.dispatch(h.request())
        self.assertEqual(response.status, 409)
        self.assertEqual(h.connection.events, [])
        self.assertEqual(h.transport.models, [])

    def test_missing_host_is_unavailable_only_after_request_authorization(self):
        requests = []
        selected = Request('POST', RECONCILE_PATH)
        def authorize(request):
            requests.append(request)
            return request is selected
        service = ProfileReconciliationService(None, authorize=authorize)
        self.assertEqual(service._handle(Request('POST', RECONCILE_PATH)).status, 403)
        self.assertEqual(service._handle(selected).status, 503)
        self.assertIs(requests[-1], selected)

    def test_replaced_host_or_callback_is_never_invoked(self):
        h = self.make()
        replacement_calls = []
        h.service.authorize = lambda request: replacement_calls.append(request)
        self.assertEqual(h.service._handle(h.request()).status, 503)
        self.assertEqual(replacement_calls, [])
        self.assertEqual(h.request_calls, [])
        self.no_host_work(h)
        h = self.make()
        h.service.host = object()
        self.assertEqual(h.service._handle(h.request()).status, 503)
        self.assertEqual(h.request_calls, [])
        self.no_host_work(h)

    def test_request_policy_cannot_replace_binding_before_host_use(self):
        h = self.make()
        h.request_hook = lambda: setattr(h.service, 'host', None)
        self.assertEqual(h.service._handle(h.request()).status, 503)
        self.assertEqual(len(h.request_calls), 1)
        self.no_host_work(h)

    def test_host_method_replacement_is_not_a_service_bypass(self):
        h = self.make()
        calls = []
        h.host.reconcile = lambda: calls.append('replacement')
        self.assertEqual(h.service._handle(h.request()).status, 503)
        self.assertEqual(calls, [])
        self.assertEqual(h.request_calls, [])
        self.no_host_work(h)

    def test_cached_reconciliation_target_replacement_is_not_invoked(self):
        h = self.make()
        calls = []
        h.service._reconcile = lambda: calls.append('cached replacement')
        self.assertEqual(h.service._handle(h.request()).status, 503)
        self.assertEqual(calls, [])
        self.assertEqual(h.request_calls, [])
        self.no_host_work(h)

    def test_host_control_flow_preserved_without_persisted_projection(self):
        h = self.make()
        error = Abort('guard exit interrupted')
        h.owner.exit_error = error
        with self.assertRaises(Abort) as raised:
            h.router.dispatch(h.request())
        self.assertIs(raised.exception, error)
        self.assertEqual(h.connection.mutations, [])
        self.assertFalse(h.owner.lock.locked())

    def test_host_construction_failure_is_not_a_client_error_or_persisted_claim(self):
        h = self.make()
        h.transport.refuse = lambda config: True
        response = h.router.dispatch(h.request())
        self.assertEqual(response.status, 500)
        self.assertEqual(response.json(), {'error': 'Profile reconciliation failed'})
        self.assertEqual(h.connection.mutations, [])

    def test_late_stale_guard_does_not_report_reconciliation_success(self):
        h = self.make()
        h.owner.revoke_on_exit = True
        response = h.router.dispatch(h.request())
        self.assertEqual(response.status, 409)
        self.assertNotIn('persisted', response.json())
        self.assertNotIn('reconciled', response.json())
        self.assertEqual(h.connection.mutations, [])

    def test_projection_requires_applied_reconciliation_not_mutation_status(self):
        valid = ProfilePublicationStatus('reconcile', 'applied', None, False, True)
        snapshot = public_snapshot()
        receipt = ProfileHostReceipt(status=valid, snapshot=snapshot)
        self.assertEqual(_project(receipt)['runtime'], snapshot)
        for changed in ({'operation': 'save'}, {'phase': 'committed_pending'},
                        {'profile_id': 1}, {'committed': True}, {'committed': None},
                        {'runtime_applied': False}):
            with self.subTest(changed=changed):
                with self.assertRaises(ValueError):
                    _project(ProfileHostReceipt(status=replace(valid, **changed), snapshot=snapshot))
        with self.assertRaises(ValueError):
            _project(object())

    def test_public_projection_drops_private_fields_and_redacts_url(self):
        status = ProfilePublicationStatus('reconcile', 'applied', None, False, True)
        snapshot = {**public_snapshot(), 'api_key': 'never-copy-private-field',
                    'base_url': 'https://user:secret@fixture.invalid/v1?key=secret#secret'}
        value = _project(ProfileHostReceipt(status=status, snapshot=snapshot))
        self.assertNotIn('api_key', value['runtime'])
        self.assertNotIn('secret', value['runtime']['base_url'])
        self.assertNotIn('persisted', value)


if __name__ == '__main__':
    unittest.main()

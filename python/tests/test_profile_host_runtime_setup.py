"""Explicit profile wiring with the persisted-app boundary always replaced.

The real persisted builder body is NEVER invoked. Actual original stores and
adapter use the previously reviewed in-memory profile fixture; no database,
transport request, task/manager/startup or listener is constructed or run.
Synthetic owner decisions below are exact recorded fixture choices, not ART
lifecycle authority. Service/host policy implementations have separate tests.
"""
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
import unittest
from unittest.mock import patch

from artex.db.postgres import PostgresExecutor
from artex.health import Registry
from artex.providers.factory import ProfileCoordinator
from artex.providers.profile_host import (
    ProfileHost, ProfileHostPolicy, ProfileHostDenied, ProfileHostStale,
)
from artex.server import runtime_setup
from artex.server.core import Request, Router, json_response
from artex.server.wsgi import WSGIApplication
from test_committed_profiles import _Connection, _Cursor, _ConstructionOnlyTransport, _profile

ROUTE = '/api/llm/profiles/reconcile'


class _Abort(BaseException):
    pass


class _Codec:
    def hash(self, *args):
        raise AssertionError('Password processing is outside this wiring test')

    def verify(self, *args):
        raise AssertionError('Password processing is outside this wiring test')


class _Owner:
    """Records only reconcile for one synthetic revision; other work denies."""
    def __init__(self):
        self.revision = 'synthetic-profile-revision-1'
        self.live_revision = self.revision
        self.allowed = {('reconcile', self.revision)}
        self.events = []
        self.inside = 0
        self.failure = None
        self.policy = ProfileHostPolicy(revision=self.revision,
            current=self.current, authorize=self.authorize, guard=self.guard)

    def current(self, revision):
        self.events.append(('current', revision))
        if self.failure is not None:
            raise self.failure
        return revision == self.live_revision

    def authorize(self, operation, value, revision):
        self.events.append(('authorize', operation, revision))
        return (operation, revision) in self.allowed and value is None

    @contextmanager
    def guard(self, operation, revision):
        if operation != 'reconcile' or revision != self.revision:
            raise AssertionError('Unknown synthetic guard domain')
        self.events.append(('guard.enter', operation, revision))
        self.inside += 1
        try:
            yield self
        finally:
            self.inside -= 1
            self.events.append(('guard.exit', operation, revision))


class ProfileHostRuntimeSetupTests(unittest.TestCase):
    def setUp(self):
        self.connection = _Connection([_profile(), _profile(2)])
        self.executor = PostgresExecutor(self.connection, cursor_factory=_Cursor)
        self.transport = _ConstructionOnlyTransport()
        self.owner = _Owner()
        self.parent_auth_calls = []
        self.request_auth_calls = []
        self.codec = _Codec()
        self.health = Registry()
        self.returned = []
        self.boundary_calls = []
        self.boundary_hook = None
        self.app_factory = self.make_application
        self.addCleanup(lambda: self.assertEqual(self.transport.requests, 0))
        self.addCleanup(lambda: self.assertEqual(self.owner.inside, 0))
        # autospec preserves the actual public signature for the builder's
        # inspect.signature preflight, without ever executing its body.
        boundary = patch.object(runtime_setup, 'build_persisted_application',
            autospec=True, side_effect=self.persisted_boundary)
        self.boundary = boundary.start()
        self.addCleanup(boundary.stop)

    def parent_authorize(self, request):
        self.parent_auth_calls.append(request)
        return request.header('Authorization') == 'Bearer synthetic-parent-token'

    def request_authorize(self, request):
        self.request_auth_calls.append(request)
        return request.header('X-Synthetic-Profile-Admin') == 'permitted'

    def make_application(self):
        router = Router(self.parent_authorize)
        router.add('GET', '/existing', lambda request, params: json_response(200, {'existing': True}))
        return WSGIApplication(router)

    def persisted_boundary(self, executor, signing_key, password_codec, **options):
        self.assertIs(executor, self.executor)
        self.assertEqual(signing_key, b'x' * 32)
        self.assertIs(password_codec, self.codec)
        self.assertEqual(self.owner.inside, 0)
        self.boundary_calls.append(options)
        if self.boundary_hook is not None:
            self.boundary_hook(options)
        application = self.app_factory()
        self.returned.append(application)
        return application

    def build(self, *, opt_in=True, **overrides):
        arguments = dict(transport_factory=self.transport, health=self.health)
        if opt_in:
            arguments.update(profile_host_policy=self.owner.policy,
                profile_reconcile_authorize=self.request_authorize)
        arguments.update(overrides)
        return runtime_setup.build_profile_runtime_application(
            self.executor, b'x' * 32, self.codec, **arguments)

    def assert_no_profile_or_app_work(self):
        self.assertEqual(self.connection.events, [])
        self.assertEqual(self.connection.mutations, [])
        self.assertEqual(self.transport.models, [])
        self.assertEqual(self.boundary_calls, [])
        self.assertEqual(self.returned, [])

    def test_default_branch_keeps_legacy_coordinator_and_old_bundle_shape(self):
        bundle = self.build(opt_in=False)
        self.assertIs(type(bundle.profile_coordinator), ProfileCoordinator)
        self.assertIsNone(bundle.profile_host)
        self.assertIsNotNone(bundle.profile_coordinator.runtime()[0])
        self.assertEqual(self.owner.events, [])
        self.assertEqual(self.request_auth_calls, [])
        self.assertEqual([row[1] for row in bundle.application.router._routes], ['/existing'])
        old_shape = runtime_setup.RuntimeBundle(bundle.application, bundle.profile_store,
            bundle.profile_factory, bundle.profile_coordinator, bundle.health)
        self.assertIsNone(old_shape.profile_host)
        with self.assertRaises(FrozenInstanceError):
            old_shape.profile_host = object()

    def test_opt_in_uses_same_resources_and_guarded_initial_reconcile(self):
        original_construct = self.transport.__call__
        def construct(config):
            self.assertEqual(self.owner.inside, 1)
            self.assertEqual(self.connection.frames, [])
            return original_construct(config)
        self.connection.before_commit = lambda: self.assertEqual(self.owner.inside, 1)
        bundle = self.build(transport_factory=construct)
        self.assertIs(type(bundle.profile_host), ProfileHost)
        self.assertIs(bundle.profile_coordinator, bundle.profile_host)
        self.assertIs(bundle.profile_store.executor, self.executor)
        self.assertIs(bundle.health, self.health)
        self.assertIs(self.boundary_calls[0]['profile_coordinator'], bundle.profile_host)
        self.assertIs(self.boundary_calls[0]['runtime_snapshot'].__self__, bundle.profile_host)
        self.assertTrue(bundle.profile_host.snapshot()['configured'])
        self.assertEqual(bundle.profile_host.status().operation, 'reconcile')
        self.assertEqual(self.connection.mutations, [])
        self.assertEqual(self.request_auth_calls, [])
        self.assertFalse(hasattr(bundle.profile_host, 'runtime'))
        self.assertEqual([(r[0], r[1], r[4]) for r in bundle.application.router._routes],
            [('GET', '/existing', False), ('POST', ROUTE, False)])
        self.assertIs(bundle.application.router.authorize.__self__, self)
        self.assertIs(bundle.application, self.returned[0])

    def test_only_one_opt_in_input_refuses_before_profile_work(self):
        for overrides in ({'profile_host_policy': self.owner.policy},
                          {'profile_reconcile_authorize': self.request_authorize}):
            with self.subTest(keys=tuple(overrides)), self.assertRaises(ValueError):
                self.build(opt_in=False, **overrides)
        self.assert_no_profile_or_app_work()

    def test_invalid_policy_or_request_callback_refuses_before_profile_work(self):
        for policy, authorize in ((object(), self.request_authorize),
                                  (self.owner.policy, object())):
            with self.subTest(policy=type(policy).__name__), self.assertRaises(ValueError):
                self.build(profile_host_policy=policy, profile_reconcile_authorize=authorize)
        self.assert_no_profile_or_app_work()

    def test_invalid_reserved_or_unknown_options_refuse_before_profile_work(self):
        for options in ({'profile_coordinator': object()}, {'runtime_snapshot': lambda: {}},
                        {'unknown_option': True}):
            with self.subTest(options=tuple(options)), self.assertRaises((ValueError, TypeError)):
                self.build(application_options=options)
        self.assert_no_profile_or_app_work()

    def test_denied_initial_reconcile_never_constructs_application(self):
        self.owner.allowed.clear()
        with self.assertRaises(ProfileHostDenied):
            self.build()
        self.assert_no_profile_or_app_work()

    def test_stale_initial_reconcile_never_constructs_application(self):
        self.owner.live_revision = 'newer-synthetic-revision'
        with self.assertRaises(ProfileHostStale):
            self.build()
        self.assert_no_profile_or_app_work()

    def test_initial_owner_baseexception_is_original_no_application(self):
        original = _Abort('synthetic owner interruption')
        self.owner.failure = original
        with self.assertRaises(_Abort) as caught:
            self.build()
        self.assertIs(caught.exception, original)
        self.assert_no_profile_or_app_work()

    def test_original_parent_auth_and_explicit_request_auth_both_remain(self):
        bundle = self.build()
        router = bundle.application.router
        original_authorizer = router.authorize
        before_events = list(self.owner.events)
        unauthorized = Request('POST', ROUTE, body=b'{}')
        self.assertEqual(router.dispatch(unauthorized).status, 401)
        self.assertEqual(self.request_auth_calls, [])
        request_denied = Request('POST', ROUTE,
            {'Authorization': 'Bearer synthetic-parent-token'}, b'not-json')
        self.assertEqual(router.dispatch(request_denied).status, 403)
        self.assertIs(self.request_auth_calls[-1], request_denied)
        self.assertEqual(self.owner.events, before_events)
        self.assertIs(router.authorize, original_authorizer)

    def test_get_does_not_reconcile_or_invoke_request_authorizer(self):
        bundle = self.build()
        before_events = list(self.owner.events)
        response = bundle.application.router.dispatch(Request('GET', ROUTE,
            {'Authorization': 'Bearer synthetic-parent-token'}))
        self.assertEqual(response.status, 405)
        self.assertEqual(self.request_auth_calls, [])
        self.assertEqual(self.owner.events, before_events)

    def test_route_conflict_is_atomic_and_returns_no_bundle(self):
        application = self.make_application()
        application.router.add('POST', ROUTE, lambda request, params: json_response(200, {}))
        before = list(application.router._routes)
        original_authorizer = application.router.authorize
        self.app_factory = lambda: application
        with self.assertRaises(ValueError):
            self.build()
        self.assertEqual(application.router._routes, before)
        self.assertIs(application.router.authorize, original_authorizer)
        self.assertEqual(len(self.boundary_calls), 1)

    def test_wrong_application_or_router_refuses_before_mount(self):
        for application in (object(), WSGIApplication(object()), WSGIApplication(Router(None))):
            with self.subTest(kind=type(application).__name__):
                self.app_factory = lambda: application
                with self.assertRaises(ValueError):
                    self.build()
        self.assertEqual(self.owner.inside, 0)

    def test_persisted_boundary_error_keeps_identity_without_replay(self):
        for original in (RuntimeError('synthetic application failure'), _Abort('synthetic abort')):
            with self.subTest(kind=type(original).__name__):
                before = len(self.boundary_calls)
                def fail(options):
                    raise original
                self.boundary_hook = fail
                with self.assertRaises(type(original)) as caught:
                    self.build()
                self.assertIs(caught.exception, original)
                self.assertEqual(len(self.boundary_calls), before + 1)
                self.assertEqual(self.returned, [])
                self.assertFalse(self.connection.closed)
                self.assertEqual(self.connection.mutations, [])


if __name__ == '__main__':
    unittest.main()

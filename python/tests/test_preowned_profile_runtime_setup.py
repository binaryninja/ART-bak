"""Reuse actual enrolled resources; the persisted builder body is never run.

Only that existing held boundary is patched. All owners, capabilities, stores,
factories, host policies, hosts, routers and WSGI wrappers are their original
classes. The existing injected SQL fixture and construction-only transport do
not connect, issue provider requests or create/start tasks and applications.
"""
import sys
from types import MethodType
import unittest
from unittest.mock import patch

from artex.configuration_owner import (
    ConfigurationOwner, ConfigurationPermissions, ConfigurationOwnershipError,
)
from artex.db.configuration_ownership import bind_executor, bind_factory
from artex.db.profiles import ProfileStore
from artex.health import Registry
from artex.providers.committed_profiles import CommittedProfileCoordinator
from artex.providers.factory import ProfileFactory
from artex.providers.owned_profiles import OwnedProfilePolicy
from artex.providers.profile_host import (
    ProfileHost, ProfileHostPolicy, ProfileHostResources, ProfileHostDenied,
    ProfileHostStale, ProfileHostUnavailable,
)
from artex.server import runtime_setup
from artex.server.core import Request, Router, json_response
from artex.server.profile_reconciliation import RECONCILE_PATH
from artex.server.profiles import ProfileApplyPending
from artex.server.wsgi import WSGIApplication
import test_committed_profile_host_contract as fixture


READS = frozenset(('profile.reconcile', 'factory.build', 'factory.pool',
                   'factory.prepare', 'factory.prepare_pool'))
WRITES = frozenset(('profile.save', 'profile.set_active', 'factory.invalidate'))


class ControlStop(BaseException):
    pass


class Codec:
    def hash(self, *args):
        raise AssertionError('Password work is outside composition acceptance')

    def verify(self, *args):
        raise AssertionError('Password work is outside composition acceptance')


class PreownedProfileRuntimeSetupTests(unittest.TestCase):
    def setUp(self):
        self.codec = Codec()
        self.parent_calls, self.request_calls, self.value_calls = [], [], []
        self.boundary_calls, self.applications = [], []
        self.allow_value = True
        self.boundary_hook = None
        self.application_factory = self.make_application
        self.parent_authorizer = self.parent_authorize
        self.request_authorizer = self.request_authorize
        self.configure()
        boundary = patch.object(runtime_setup, 'build_persisted_application',
            autospec=True, side_effect=self.persisted_boundary)
        self.boundary = boundary.start()
        self.addCleanup(boundary.stop)

    def configure(self, *, pending_error=ProfileApplyPending):
        self.f = fixture.CommittedProfileHostContractTests.make(self, pending_error=pending_error)
        self.owner = ConfigurationOwner('preowned-profile-fixture', participants={
            'operator': ConfigurationPermissions(read=READS, write=WRITES)})
        self.addCleanup(self.owner.close)
        self.cap = self.owner.issue('operator')
        self.initial = self.owner.snapshot(self.cap)
        bind_executor(self.f.executor, self.owner)
        bind_factory(self.f.factory, self.owner)
        self.bridge = OwnedProfilePolicy(self.owner, self.cap, snapshot=self.initial,
            executor=self.f.executor, factory=self.f.factory, revision='approved-profile-values-v1',
            authorize_value=self.authorize_value, timeout=0)
        self.host = ProfileHost(self.f.coordinator, policy=self.bridge.policy)

    def authorize_value(self, operation, value, revision):
        self.value_calls.append((operation, value, revision))
        return (self.allow_value and operation == 'reconcile' and value is None
                and revision == 'approved-profile-values-v1')

    def parent_authorize(self, request):
        self.parent_calls.append(request)
        return request.header('Authorization') == 'Bearer synthetic-parent'

    def request_authorize(self, request):
        self.request_calls.append(request)
        return request.header('X-Profile-Decision') == 'approved'

    def make_application(self):
        router = Router(self.parent_authorizer)
        router.add('GET', '/existing', lambda request, params: json_response(200, {}))
        return WSGIApplication(router)

    def persisted_boundary(self, executor, signing_key, password_codec, **options):
        self.assertIs(executor, self.f.executor)
        self.assertEqual(signing_key, b's' * 32)
        self.assertIs(password_codec, self.codec)
        self.assertIs(options['profile_coordinator'], self.host)
        self.assertIs(options['runtime_snapshot'].__self__, self.host)
        self.assertIs(options['runtime_snapshot'].__func__, ProfileHost.snapshot)
        self.assertTrue(self.host.status().runtime_applied)
        self.boundary_calls.append(options)
        app = self.application_factory()
        self.applications.append(app)
        if self.boundary_hook is not None:
            self.boundary_hook(app)
        return app

    def build(self, **overrides):
        options = dict(transport_factory=self.f.transport, health=self.f.factory.health,
                       profile_host=self.host, profile_reconcile_authorize=self.request_authorizer)
        options.update(overrides)
        executor = options.pop('executor', self.f.executor)
        return runtime_setup.build_profile_runtime_application(executor, b's' * 32, self.codec, **options)

    def assert_no_profile_or_application_work(self):
        self.assertEqual(self.f.connection.events, [])
        self.assertEqual(self.f.connection.mutations, [])
        self.assertEqual(self.f.transport.models, [])
        self.assertEqual(self.value_calls, [])
        self.assertEqual(self.boundary_calls, [])
        self.assertEqual(self.applications, [])

    def assert_unmounted(self, app):
        response = app.router.dispatch(Request('POST', RECONCILE_PATH,
            {'Authorization': 'Bearer synthetic-parent', 'X-Profile-Decision': 'approved'}, b'{}'))
        self.assertEqual(response.status, 404)
        self.assertEqual(self.request_calls, [])

    def test_original_resources_and_host_are_reused_without_reconstruction(self):
        constructors = {kind.__init__.__code__: kind.__name__ for kind in
                        (ProfileStore, ProfileFactory, CommittedProfileCoordinator, ProfileHost)}
        called = []
        previous = sys.getprofile()
        def observe(frame, event, arg):
            if event == 'call' and frame.f_code in constructors:
                called.append(constructors[frame.f_code])
        sys.setprofile(observe)
        try:
            bundle = self.build()
        finally:
            sys.setprofile(previous)
        self.assertEqual(called, [])
        self.assertIs(bundle.profile_host, self.host)
        self.assertIs(bundle.profile_coordinator, self.host)
        self.assertIs(bundle.profile_store, self.f.store)
        self.assertIs(bundle.profile_factory, self.f.factory)
        self.assertIs(bundle.health, self.f.factory.health)
        self.assertIs(self.f.factory._transport_factory, self.f.transport)
        self.assertIs(bundle.application, self.applications[0])
        self.assertIs(bundle.application.router.authorize, self.parent_authorizer)
        self.assertEqual(len(self.boundary_calls), 1)
        self.assertTrue(self.host.snapshot()['configured'])
        self.assertEqual(self.f.connection.mutations, [])
        self.assertEqual(self.request_calls, [])
        self.assertTrue(self.owner.current(self.initial))

    def test_conflicting_missing_or_wrong_host_options_refuse_before_work(self):
        for overrides in ({'profile_host_policy': self.bridge.policy},
                          {'profile_reconcile_authorize': None},
                          {'profile_reconcile_authorize': True},
                          {'profile_host': object()}, {'profile_host': None}):
            with self.subTest(overrides=tuple(overrides)):
                with self.assertRaises(ValueError):
                    self.build(**overrides)
        self.assert_no_profile_or_application_work()

    def test_exact_executor_transport_and_health_identity_required(self):
        other = fixture.CommittedProfileHostContractTests.make(self)
        for overrides in ({'executor': other.executor}, {'health': Registry()},
                          {'transport_factory': other.transport}):
            with self.subTest(overrides=tuple(overrides)):
                with self.assertRaises(ValueError):
                    self.build(**overrides)
        self.assert_no_profile_or_application_work()
        self.assertEqual(other.connection.events, [])
        self.assertEqual(other.transport.models, [])

    def test_reserved_and_unknown_application_options_refuse_before_work(self):
        for options in ({'profile_coordinator': object()}, {'runtime_snapshot': object()},
                        {'unrecognized_fixture_option': True}):
            with self.subTest(options=tuple(options)):
                with self.assertRaises((ValueError, TypeError)):
                    self.build(application_options=options)
        self.assert_no_profile_or_application_work()

    def test_required_resource_association_and_server_pending_contract(self):
        def forbidden(*args):
            raise AssertionError('Association refusal must precede authority callbacks')
        generic = ProfileHostPolicy(revision='explicit', current=forbidden,
                                    authorize=forbidden, guard=forbidden)
        host = ProfileHost(self.f.coordinator, policy=generic)
        with self.assertRaises(ValueError):
            self.build(profile_host=host)
        self.assert_no_profile_or_application_work()
        self.configure(pending_error=None)
        with self.assertRaises(ValueError):
            self.build()
        self.assert_no_profile_or_application_work()

    def test_missing_or_different_resource_enrollment_refuses_without_callbacks(self):
        for enrollment in ('none', 'executor-only', 'different-owner'):
            with self.subTest(enrollment=enrollment):
                f = fixture.CommittedProfileHostContractTests.make(self, pending_error=ProfileApplyPending)
                def forbidden(*args):
                    raise AssertionError('Enrollment refusal must precede callbacks')
                policy = ProfileHostPolicy(revision='explicit', current=forbidden,
                    authorize=forbidden, guard=forbidden,
                    resources=ProfileHostResources(f.executor, f.factory))
                host = ProfileHost(f.coordinator, policy=policy)
                if enrollment != 'none':
                    bind_executor(f.executor, self.owner)
                if enrollment == 'different-owner':
                    other = ConfigurationOwner('different-fixture-owner', participants={
                        'reader': ConfigurationPermissions(read=READS, write=frozenset())})
                    self.addCleanup(other.close)
                    bind_factory(f.factory, other)
                with self.assertRaises(ValueError):
                    self.build(profile_host=host, executor=f.executor,
                               transport_factory=f.transport, health=f.factory.health)
                self.assertEqual(f.connection.events, [])
                self.assertEqual(f.transport.models, [])
        self.assert_no_profile_or_application_work()

    def test_revoked_and_closed_ownership_refuse_before_profile_work(self):
        self.owner.revoke(self.cap)
        with self.assertRaises(ProfileHostStale):
            self.build()
        self.assert_no_profile_or_application_work()
        self.configure()
        self.owner.close()
        with self.assertRaises(ConfigurationOwnershipError):
            self.build()
        self.assert_no_profile_or_application_work()

    def test_external_generation_does_not_silently_refresh_existing_host(self):
        with self.owner.guard(self.cap, 'profile.save', mode='write',
                              expected=self.initial, timeout=0):
            pass
        with self.assertRaises(ProfileHostStale):
            self.build()
        self.assert_no_profile_or_application_work()

    def test_substantive_initial_reconciliation_denial_never_reaches_application(self):
        self.allow_value = False
        with self.assertRaises(ProfileHostDenied):
            self.build()
        self.assertEqual(len(self.value_calls), 1)
        self.assertEqual(self.f.connection.events, [])
        self.assertEqual(self.f.transport.models, [])
        self.assertEqual(self.boundary_calls, [])

    def test_boundary_revocation_refuses_before_mount_without_reconcile_retry(self):
        events_at_boundary = []
        def revoke(app):
            events_at_boundary.extend(self.f.connection.events)
            self.owner.revoke(self.cap)
        self.boundary_hook = revoke
        with self.assertRaises(ProfileHostStale):
            self.build()
        self.assertEqual(len(self.boundary_calls), 1)
        self.assertEqual(self.f.connection.events, events_at_boundary)
        self.assert_unmounted(self.applications[0])

    def test_boundary_generation_change_refuses_without_observation_refresh(self):
        def advance(app):
            with self.owner.guard(self.cap, 'profile.save', mode='write',
                                  expected=self.owner.snapshot(self.cap), timeout=0):
                pass
        self.boundary_hook = advance
        with self.assertRaises(ProfileHostStale):
            self.build()
        self.assertFalse(self.bridge.current('approved-profile-values-v1'))
        self.assertEqual(self.f.connection.mutations, [])
        self.assertEqual(len(self.boundary_calls), 1)
        self.assert_unmounted(self.applications[0])

    def test_post_boundary_resource_drift_refuses_before_mount(self):
        original = self.f.factory.health
        self.boundary_hook = lambda app: setattr(self.f.factory, 'health', Registry())
        try:
            with self.assertRaises(ProfileHostUnavailable):
                self.build()
            self.assert_unmounted(self.applications[0])
        finally:
            self.f.factory.health = original

    def test_boundary_control_exception_retains_identity_without_replay(self):
        original = ControlStop()
        def fail(app):
            raise original
        self.boundary_hook = fail
        with self.assertRaises(ControlStop) as caught:
            self.build()
        self.assertIs(caught.exception, original)
        self.assertEqual(len(self.boundary_calls), 1)
        self.assert_unmounted(self.applications[0])

    def test_wrong_application_or_router_surface_is_rejected(self):
        for make in (lambda: object(), lambda: WSGIApplication(object())):
            with self.subTest(surface=make):
                self.application_factory = make
                with self.assertRaises(ValueError):
                    self.build()
        self.assertEqual(len(self.boundary_calls), 2)
        self.assertEqual(self.f.connection.mutations, [])

    def test_collision_leaves_original_route_table_and_authorizer_unchanged(self):
        app = self.make_application()
        app.router.add('POST', RECONCILE_PATH, lambda request, params: json_response(200, {}))
        routes = list(app.router._routes)
        self.application_factory = lambda: app
        with self.assertRaises(ValueError):
            self.build()
        self.assertEqual(app.router._routes, routes)
        self.assertIs(app.router.authorize, self.parent_authorizer)
        self.assertEqual(self.request_calls, [])

    def test_original_parent_auth_request_decision_and_host_reconcile_all_required(self):
        bundle = self.build()
        router = bundle.application.router
        value_calls = list(self.value_calls)
        self.assertEqual(router.dispatch(Request('POST', RECONCILE_PATH, body=b'{}')).status, 401)
        self.assertEqual(self.request_calls, [])
        self.assertEqual(router.dispatch(Request('POST', RECONCILE_PATH,
            {'Authorization': 'Bearer synthetic-parent'}, b'not-json')).status, 403)
        self.assertEqual(self.value_calls, value_calls)
        headers = {'Authorization': 'Bearer synthetic-parent', 'X-Profile-Decision': 'approved'}
        response = router.dispatch(Request('POST', RECONCILE_PATH, headers, b'{}'))
        self.assertEqual(response.status, 200)
        self.assertTrue(response.json()['reconciled'])
        self.assertGreater(len(self.value_calls), len(value_calls))
        count = len(self.request_calls)
        self.assertEqual(router.dispatch(Request('GET', RECONCILE_PATH, headers)).status, 405)
        self.assertEqual(len(self.request_calls), count)
        self.assertIs(router.authorize, self.parent_authorizer)
        self.assertEqual(self.f.connection.mutations, [])

    def test_replaced_public_methods_refuse_before_any_effect(self):
        calls = []
        def replacement(host, *args, **kwargs):
            calls.append(host)
            raise AssertionError('Replaced host method must never be invoked')
        for name in ('save', 'set_active', 'reconcile', 'snapshot', 'status', 'validate_bindings'):
            with self.subTest(method=name):
                setattr(self.host, name, MethodType(replacement, self.host))
                try:
                    with self.assertRaises(ValueError):
                        self.build()
                    self.assert_no_profile_or_application_work()
                    self.assertEqual(calls, [])
                finally:
                    delattr(self.host, name)

    def test_post_boundary_replaced_public_methods_refuse_before_mount(self):
        calls = []
        def replacement(host, *args, **kwargs):
            calls.append(host)
            raise AssertionError('Replaced host method must never be invoked')
        for name in ('save', 'set_active', 'reconcile', 'snapshot', 'status', 'validate_bindings'):
            with self.subTest(method=name):
                def replace_at_boundary(app):
                    setattr(self.host, name, MethodType(replacement, self.host))
                self.boundary_hook = replace_at_boundary
                try:
                    with self.assertRaises(ValueError):
                        self.build()
                    self.assert_unmounted(self.applications[-1])
                    self.assertEqual(calls, [])
                finally:
                    if name in self.host.__dict__:
                        delattr(self.host, name)


if __name__ == '__main__':
    unittest.main()

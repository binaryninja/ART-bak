"""Explicit recorded host decisions over real objects with an injected driver.

The owner below authorizes only named synthetic operations/values/revision on
its original resources. It is a test oracle, not genuine ART/task authority.
No connection, provider request, app startup or task operation is performed.
"""
from copy import deepcopy
from types import MethodType
import sys
import unittest

from artex.providers.committed_profiles import (
    CommittedProfileApplyPending, ProfileMutationBusy,
)
from artex.providers.profile_host import (
    ProfileHost, ProfileHostBusy, ProfileHostDenied, ProfileHostPolicy,
    ProfileHostStale, ProfileHostUnavailable,
)
from artex.server.profiles import ProfileApplyPending
import test_committed_profile_host_contract as fixture_module

_profile = fixture_module._profile


class ControlStop(BaseException):
    pass


class Owner:
    def __init__(self, fixture):
        self.fixture = fixture
        self.revision = 'synthetic-profile-owner-v1'
        self.values = {'reconcile': None, 'set_active': 2,
                       'save': _profile(model='new-model')}
        self.calls = []
        self.depth = 0
        self.fresh = True
        self.decision = True
        self.suppress = False
        self.enter_error = self.exit_error = None
        self.on_current = self.on_authorize = self.on_enter = self.on_exit = None

    def current(self, revision):
        self.calls.append(('current', revision))
        if self.on_current is not None:
            self.on_current()
        return self.fresh if revision == self.revision and self.fixture.store.executor is self.fixture.executor else False

    def authorize(self, operation, value, revision):
        self.calls.append(('authorize', operation, deepcopy(value), revision))
        permitted = (revision == self.revision and operation in self.values
                     and value == self.values[operation]
                     and self.fixture.store.executor is self.fixture.executor)
        if self.on_authorize is not None:
            self.on_authorize(operation, value)
        return self.decision if permitted else False

    def guard(self, operation, revision):
        if revision != self.revision or operation not in self.values:
            raise AssertionError('Unexpected fixture owner domain')
        self.calls.append(('guard', operation, revision))
        owner = self

        class Guard:
            def __enter__(self):
                owner.fixture.connection.events.append('guard.enter')
                if owner.enter_error is not None:
                    raise owner.enter_error
                owner.depth += 1
                if owner.on_enter is not None:
                    owner.on_enter()

            def __exit__(self, kind, error, tb):
                owner.calls.append(('exit', error))
                owner.fixture.connection.events.append('guard.exit')
                owner.depth -= 1
                if owner.on_exit is not None:
                    owner.on_exit()
                if owner.exit_error is not None:
                    raise owner.exit_error
                return owner.suppress

        return Guard()


class ProfileHostTests(unittest.TestCase):
    def make(self, **options):
        fixture = fixture_module.CommittedProfileHostContractTests.make(self, **options)
        owner = Owner(fixture)
        policy = ProfileHostPolicy(revision=owner.revision, current=owner.current,
                                   authorize=owner.authorize, guard=owner.guard)
        host = ProfileHost(fixture.coordinator, policy=policy)
        self.assertEqual(owner.calls, [])
        self.assertEqual(fixture.connection.events, [])
        return fixture, owner, policy, host

    def test_explicit_policy_and_original_resources_required_without_effects(self):
        f, o, p, host = self.make()
        for options in ({}, {'revision': o.revision},
                        dict(revision='', current=o.current, authorize=o.authorize, guard=o.guard),
                        dict(revision=o.revision, current=None, authorize=o.authorize, guard=o.guard)):
            with self.subTest(options=tuple(options)):
                with self.assertRaises((TypeError, ValueError)):
                    ProfileHostPolicy(**options)
        with self.assertRaises(TypeError):
            ProfileHost(f.coordinator, policy=None)
        with self.assertRaises(TypeError):
            ProfileHost(object(), policy=p)
        self.assertIsNone(host.validate_bindings())
        self.assertFalse(host.snapshot()['configured'])
        self.assertEqual(host.status().phase, 'unconfigured')
        self.assertFalse(o.calls)
        self.assertFalse(f.connection.events)

    def test_reconcile_receipt_only_after_guard_exit_with_no_mutation(self):
        f, o, p, host = self.make()
        def at_exit():
            self.assertFalse(host.snapshot()['configured'])
            self.assertEqual(host.status().phase, 'in_progress')
            self.assertTrue(f.coordinator.snapshot()['configured'])
        o.on_exit = at_exit
        receipt = host.reconcile()
        self.assertEqual(receipt.status.operation, 'reconcile')
        self.assertEqual(receipt.status.phase, 'applied')
        self.assertIs(receipt.status.committed, False)
        self.assertIs(receipt.status.runtime_applied, True)
        self.assertTrue(receipt.snapshot['configured'])
        with self.assertRaises(TypeError):
            receipt.snapshot['configured'] = False
        copy = host.snapshot(); copy['model'] = 'caller edit'
        self.assertEqual(host.snapshot()['model'], 'old-model-1')
        self.assertLess(f.connection.events.index('guard.enter'), f.connection.events.index('outer.begin'))
        self.assertLess(f.connection.events.index('outer.commit'), f.connection.events.index('guard.exit'))
        self.assertEqual(f.connection.mutations, [])
        self.assertEqual(sum(c[0] == 'authorize' for c in o.calls), 2)

    def test_replaced_delegate_before_composition_is_not_an_original_baseline(self):
        f, o, p, host = self.make()
        f.coordinator.reload = MethodType(lambda self: None, f.coordinator)
        with self.assertRaises(TypeError):
            ProfileHost(f.coordinator, policy=p)
        self.assertFalse(o.calls)
        self.assertFalse(f.connection.events)
        self.assertFalse(f.transport.models)

    def test_save_and_activation_keep_service_return_contract(self):
        f, o, p, host = self.make()
        self.assertEqual(host.save(o.values['save']), 1)
        self.assertEqual(host.snapshot()['model'], 'new-model')
        self.assertIs(host.status().committed, True)
        self.assertIsNone(host.set_active(2))
        self.assertTrue(f.connection.rows[2].is_default)
        self.assertEqual(host.snapshot()['model'], 'old-model-2')
        self.assertEqual(o.depth, 0)

    def test_literal_authority_refusal_before_profile_work(self):
        for field in ('fresh', 'decision'):
            for value in (False, None, 1, 'yes'):
                with self.subTest(field=field, value=value):
                    f, o, p, host = self.make()
                    setattr(o, field, value)
                    expected = ProfileHostStale if field == 'fresh' else ProfileHostDenied
                    with self.assertRaises(expected):
                        host.reconcile()
                    self.assertFalse(f.connection.events)
                    self.assertFalse(f.transport.models)
                    self.assertIs(host.status().committed, False)

    def test_denial_after_previous_success_does_not_borrow_commit(self):
        f, o, p, host = self.make()
        host.save(o.values['save'])
        before = list(f.connection.events)
        o.decision = False
        with self.assertRaises(ProfileHostDenied):
            host.set_active(2)
        self.assertEqual(before, f.connection.events)
        self.assertFalse(host.snapshot()['configured'])
        self.assertIs(host.status().committed, False)
        self.assertIsNone(host.status().profile_id)

    def test_in_guard_freshness_rechecked_before_work(self):
        f, o, p, host = self.make()
        o.on_enter = lambda: setattr(o, 'fresh', False)
        with self.assertRaises(ProfileHostStale):
            host.reconcile()
        self.assertEqual(f.connection.events, ['guard.enter', 'guard.exit'])
        self.assertFalse(f.transport.models)

    def test_guard_cannot_hide_body_failure_and_exit_error_keeps_context(self):
        for error in (RuntimeError('ordinary control'), ControlStop('control')):
            f, o, p, host = self.make()
            o.on_enter = lambda error=error: setattr(o, 'on_current', lambda: (_ for _ in ()).throw(error))
            o.suppress = True
            with self.assertRaises(type(error)) as caught:
                host.reconcile()
            self.assertIs(caught.exception, error)
            self.assertFalse(f.connection.mutations)
            self.assertFalse(host.snapshot()['configured'])
        f, o, p, host = self.make()
        body, exit_error = RuntimeError('body'), RuntimeError('exit')
        o.on_enter = lambda: setattr(o, 'on_current', lambda: (_ for _ in ()).throw(body))
        o.exit_error = exit_error
        with self.assertRaises(RuntimeError) as caught:
            host.reconcile()
        self.assertIs(caught.exception, exit_error)
        self.assertIs(exit_error.__context__, body)

    def test_paired_control_and_exit_error_precedence_before_and_after_commit(self):
        for after_commit in (False, True):
            for body_type, exit_type in ((ControlStop, RuntimeError),
                                         (RuntimeError, ControlStop),
                                         (RuntimeError, RuntimeError)):
                with self.subTest(after_commit=after_commit, body=body_type, exit=exit_type):
                    f, o, p, host = self.make(pending_error=ProfileApplyPending)
                    body, exit_error = body_type('body outcome'), exit_type('guard exit')
                    def fail_body():
                        if not after_commit or f.connection.committed[1].model == 'new-model':
                            raise body
                    o.on_enter = lambda: setattr(o, 'on_current', fail_body)
                    o.exit_error = exit_error
                    if body_type is ControlStop:
                        expected = ControlStop
                    elif after_commit and exit_type is RuntimeError:
                        expected = ProfileApplyPending
                    else:
                        expected = exit_type
                    with self.assertRaises(expected) as caught:
                        host.save(o.values['save'])
                    if body_type is ControlStop:
                        self.assertIs(caught.exception, body)
                        self.assertIs(body.__cause__, exit_error)
                    elif expected is ProfileApplyPending:
                        self.assertIs(caught.exception.__cause__, exit_error)
                        self.assertIs(exit_error.__context__, body)
                    else:
                        self.assertIs(caught.exception, exit_error)
                        self.assertIs(exit_error.__context__, body)
                    self.assertIs(host.status().committed, after_commit)
                    self.assertFalse(host.snapshot()['configured'])
                    self.assertEqual(len(f.connection.mutations), int(after_commit))
                    self.assertEqual(f.connection.committed[1].model,
                                     'new-model' if after_commit else 'old-model-1')

    def test_guard_entry_failure_has_no_database_or_publication(self):
        f, o, p, host = self.make()
        error = ControlStop('entry')
        o.enter_error = error
        with self.assertRaises(ControlStop) as caught:
            host.reconcile()
        self.assertIs(caught.exception, error)
        self.assertEqual(f.connection.events, ['guard.enter'])
        self.assertFalse(host.snapshot()['configured'])

    def test_guard_exit_and_final_freshness_hide_committed_publication(self):
        for kind in ('exit', 'fresh'):
            f, o, p, host = self.make(pending_error=ProfileApplyPending)
            error = RuntimeError('guard exit')
            if kind == 'exit': o.exit_error = error
            else: o.on_exit = lambda: setattr(o, 'fresh', False)
            with self.assertRaises(ProfileApplyPending) as caught:
                host.save(o.values['save'])
            self.assertIsInstance(caught.exception.__cause__, RuntimeError)
            if kind == 'exit': self.assertIs(caught.exception.__cause__, error)
            self.assertEqual(f.connection.committed[1].model, 'new-model')
            self.assertTrue(f.coordinator.snapshot()['configured'])
            self.assertFalse(host.snapshot()['configured'])
            self.assertEqual(host.status().phase, 'committed_pending')
            self.assertIs(host.status().committed, True)
            self.assertIs(host.status().runtime_applied, False)

    def test_postcommit_baseexception_keeps_identity_and_pending_observation(self):
        f, o, p, host = self.make()
        error = ControlStop('late control')
        o.exit_error = error
        with self.assertRaises(ControlStop) as caught:
            host.set_active(2)
        self.assertIs(caught.exception, error)
        self.assertIs(host.status().committed, True)
        self.assertFalse(host.snapshot()['configured'])

    def test_reconcile_exit_failure_never_claims_persisted_mutation(self):
        f, o, p, host = self.make(pending_error=ProfileApplyPending)
        error = RuntimeError('exit')
        o.exit_error = error
        with self.assertRaises(RuntimeError) as caught:
            host.reconcile()
        self.assertIs(caught.exception, error)
        self.assertIs(host.status().committed, False)
        self.assertFalse(host.snapshot()['configured'])
        self.assertFalse(f.connection.mutations)

    def test_commit_uncertainty_not_replayed_or_translated_as_confirmed(self):
        f, o, p, host = self.make()
        error = RuntimeError('ambiguous commit')
        f.connection.commit_error = error
        f.connection.commit_error_after_mutation = True
        with self.assertRaises(RuntimeError) as caught:
            host.save(o.values['save'])
        self.assertIs(caught.exception, error)
        self.assertIs(host.status().committed, None)
        self.assertEqual(host.status().phase, 'outcome_unknown')
        self.assertEqual(len(f.connection.mutations), 1)
        self.assertEqual(f.connection.committed[1].model, 'new-model')
        self.assertFalse(host.snapshot()['configured'])

    def test_callback_reentry_fails_before_nested_work(self):
        f, o, p, host = self.make()
        o.on_authorize = lambda operation, value: host.reconcile()
        with self.assertRaises(ProfileHostBusy):
            host.reconcile()
        self.assertFalse(f.connection.events)
        self.assertFalse(host.snapshot()['configured'])

    def test_core_busy_does_not_borrow_prior_commit(self):
        f, o, p, host = self.make()
        f.coordinator.save(o.values['save'])
        before = list(f.connection.mutations)
        # Ordinary lock acquisition models another direct coordinator caller;
        # it supplies no host authority or original application provenance.
        f.coordinator._operation_gate.acquire()
        try:
            with self.assertRaises(ProfileMutationBusy):
                host.set_active(2)
        finally:
            f.coordinator._operation_gate.release()
        self.assertEqual(f.connection.mutations, before)
        self.assertIs(host.status().committed, False)
        self.assertIsNone(host.status().profile_id)
        self.assertFalse(host.snapshot()['configured'])

    def test_authorization_copy_mutation_refused_and_caller_edit_isolated(self):
        f, o, p, host = self.make()
        o.on_authorize = lambda operation, value: setattr(value, 'model', 'altered')
        with self.assertRaises(ProfileHostUnavailable):
            host.save(o.values['save'])
        self.assertFalse(f.connection.events)
        f, o, p, host = self.make()
        original = deepcopy(o.values['save'])
        o.on_authorize = lambda operation, value: setattr(original, 'model', 'outside edit')
        host.save(original)
        self.assertEqual(f.connection.committed[1].model, 'new-model')
        self.assertEqual(original.model, 'outside edit')

    def test_policy_resource_and_method_replacement_refused(self):
        for replacement in ('policy', 'resource', 'method'):
            f, o, p, host = self.make()
            if replacement == 'policy':
                o.on_authorize = lambda operation, value: object.__setattr__(p, 'current', lambda revision: False)
            elif replacement == 'resource':
                o.on_authorize = lambda operation, value: setattr(f.store, 'executor', object())
            else:
                o.on_authorize = lambda operation, value: setattr(f.coordinator, 'reload', lambda: None)
            with self.assertRaises(ProfileHostUnavailable):
                host.reconcile()
            self.assertFalse(f.connection.events)
            with self.assertRaises(ProfileHostUnavailable):
                host.validate_bindings()

    def test_postcommit_callback_replacement_uses_pinned_pending_class(self):
        f, o, p, host = self.make(pending_error=ProfileApplyPending)
        original = f.factory._transport_factory
        o.on_exit = lambda: setattr(f.factory, '_transport_factory', lambda config: None)
        with self.assertRaises(ProfileApplyPending) as caught:
            host.save(o.values['save'])
        self.assertIsInstance(caught.exception.__cause__, ProfileHostUnavailable)
        f.factory._transport_factory = original
        self.assertIs(host.status().committed, True)
        self.assertFalse(host.snapshot()['configured'])

    def test_existing_pending_exception_identity_preserved(self):
        f, o, p, host = self.make()
        constructions = []
        def refuse(config):
            constructions.append(config.model)
            return len(constructions) > 1
        f.transport.refuse = refuse
        with self.assertRaises(CommittedProfileApplyPending) as caught:
            host.save(o.values['save'])
        self.assertIsNone(caught.exception.__cause__)
        self.assertIs(host.status().committed, True)
        self.assertFalse(host.snapshot()['configured'])
        self.assertEqual(len(f.connection.mutations), 1)

    def test_observation_control_after_success_retains_identity_and_unknown(self):
        self.observation_control_case(None)

    def test_observation_control_after_delegate_error_retains_primary_context(self):
        for primary in (RuntimeError('commit acknowledgement'), ControlStop('delegate control')):
            with self.subTest(primary=type(primary)):
                self.observation_control_case(primary)

    def observation_control_case(self, primary):
        f, o, p, host = self.make()
        if primary is not None:
            f.connection.commit_error = primary
            f.connection.commit_error_after_mutation = True
        control = ControlStop('status observation interrupted')
        status_code = f.coordinator.status.__func__.__code__
        observe_code = ProfileHost._observe.__code__
        observations = []

        def inject(frame, event, arg):
            if (event == 'call' and frame.f_code is status_code
                    and frame.f_back.f_code is observe_code
                    and frame.f_locals.get('self') is f.coordinator):
                observations.append(f.connection.committed[1].model)
                raise control
            return inject

        # A one-shot synthetic interpreter interruption, not an enforcement
        # monitor. Raising may disable tracing; restore the prior trace in all
        # cases. Original pinned methods, locks and owner guards are unchanged.
        previous = sys.gettrace()
        try:
            sys.settrace(inject)
            with self.assertRaises(ControlStop) as caught:
                host.save(o.values['save'])
        finally:
            sys.settrace(previous)
        self.assertIs(caught.exception, control)
        if primary is not None:
            self.assertIs(control.__context__, primary)
        self.assertEqual(observations, ['new-model'])
        self.assertEqual(f.connection.committed[1].model, 'new-model')
        self.assertEqual(len(f.connection.mutations), 1)
        self.assertFalse(host.snapshot()['configured'])
        self.assertEqual(host.status().phase, 'outcome_unknown')
        self.assertIsNone(host.status().committed)
        self.assertIs(host.status().runtime_applied, False)


if __name__ == '__main__':
    unittest.main()

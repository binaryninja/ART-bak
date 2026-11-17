"""Inert value-object checks; explicit recorded decisions are synthetic fixtures."""
from dataclasses import MISSING, FrozenInstanceError, fields, replace
import unittest

from artex.agent.goals import GoalScope
from artex.agent.operator_local_data_policy import OperatorLocalDataPolicy
from artex.agent.planner import PlannerScope
from artex.agent.runtime import Action, Grant, RuntimeContext
from artex.sidequestion import Model
from artex.tools.graph import GraphScope


def forbidden_clock():
    raise AssertionError('The policy must not invoke runtime checks or clocks')


class RecordedGuard:
    def __init__(self):
        self.events = []

    def __enter__(self):
        self.events.append('enter')
        return self

    def __exit__(self, kind, error, traceback):
        self.events.append(('exit', error))
        return False


class Owner:
    """Unrecorded requests deny; these fixtures establish no real authority."""
    def __init__(self):
        self.calls = []
        self.current_decision = False
        self.guard = RecordedGuard()
        self.actions = []
        self.assets = []
        self.inputs = []

    def current(self, *args):
        self.calls.append(('current', args))
        return self.current_decision

    def lifecycle_guard(self, *args):
        self.calls.append(('guard', args))
        return self.guard

    def authorize(self, context, action):
        self.calls.append(('action', context, action))
        for recorded_context, recorded_action, decision in self.actions:
            if context is recorded_context and action is recorded_action:
                return decision
        return Grant()

    def asset_authorize(self, scope, ids, summary):
        self.calls.append(('assets', scope, ids, summary))
        for old_scope, old_ids, old_summary, decision in self.assets:
            if scope is old_scope and ids is old_ids and summary is old_summary:
                return decision
        return Grant()

    def validate_input(self, scope, goal, description):
        self.calls.append(('input', scope, goal, description))
        for old_scope, old_goal, old_description, decision in self.inputs:
            if scope is old_scope and goal == old_goal and description == old_description:
                return decision
        return False


class OperatorLocalDataPolicyTests(unittest.TestCase):
    def setUp(self):
        self.owner = Owner()
        self.tools = {'goals': frozenset({'set_goals'}),
                      'planner': frozenset({'add_intent', 'list_facts'}),
                      'worker': frozenset({'list_facts', 'record_fact'})}
        self.policy = OperatorLocalDataPolicy(
            task_id=11, exploration_id=22, policy_revision='operator-choice-v1',
            model_roles=frozenset({'goals', 'worker'}), tools_by_role=self.tools,
            allowed_asset_ids=frozenset({3, 5}), approved_goal=' Read local notes ',
            approved_description='Only the explicitly selected local notes.',
            owner_current=self.owner.current, owner_guard=self.owner.lifecycle_guard,
            owner_authorize=self.owner.authorize, owner_asset_authorize=self.owner.asset_authorize,
            owner_validate_input=self.owner.validate_input)
        self.model = Model(profile_id=7, name='synthetic', format='openai', model='fixture')

    def context(self, role='goals', **changes):
        metadata = dict(task_id=11, exploration_id=22, run_id=33,
                        revision='derived-config-digest', role=role,
                        session_id=f'exp22-{role}')
        if role == 'worker':
            metadata.update(intent_id=44, session_id='exp22-worker-i44')
        metadata.update(changes)
        return RuntimeContext(metadata, object(), 100, forbidden_clock)

    def goal_scope(self, **changes):
        return replace(GoalScope(11, 22, 33, 'derived-config-digest', self.model, 'synthetic'), **changes)

    def planner_scope(self, **changes):
        return replace(PlannerScope(11, 22, 33, 'derived-config-digest', self.model, 'synthetic'), **changes)

    def graph_scope(self, **changes):
        return replace(GraphScope(11, 22, 33, 'derived-config-digest', 'worker', 44), **changes)

    def assert_denied(self, grant):
        self.assertIsInstance(grant, Grant)
        self.assertIs(grant.permission, False)
        self.assertIs(grant.in_scope, False)
        self.assertIs(grant.approved, False)

    def test_all_thirteen_inputs_required_no_construction_calls_or_reprs(self):
        declared = fields(OperatorLocalDataPolicy)
        self.assertEqual(len(declared), 13)
        for field in declared:
            self.assertIs(field.default, MISSING)
            self.assertIs(field.default_factory, MISSING)
        self.assertEqual(self.owner.calls, [])
        self.assertNotIn('operator-choice', repr(self.policy))
        self.assertNotIn('Read local notes', repr(self.policy))
        with self.assertRaises(TypeError):
            OperatorLocalDataPolicy()

    def test_selections_are_frozen_and_mapping_is_snapshotted(self):
        self.tools['goals'] = frozenset()
        self.assertEqual(self.policy.tools_by_role['goals'], frozenset({'set_goals'}))
        with self.assertRaises(TypeError):
            self.policy.tools_by_role['goals'] = frozenset()
        with self.assertRaises(FrozenInstanceError):
            self.policy.task_id = 9
        self.assertIs(self.policy.owner_authorize.__self__, self.owner)

    def test_malformed_selections_rejected_without_owner_calls(self):
        invalid = [dict(task_id=v) for v in (True, 0, -1, 2**63, '11')]
        invalid += [dict(exploration_id=True), dict(policy_revision=''), dict(policy_revision=1),
                    dict(model_roles={'goals'}), dict(model_roles=frozenset({'other'})),
                    dict(tools_by_role={'goals': frozenset()}),
                    dict(tools_by_role={**self.tools, 'worker': frozenset({'bash'})}),
                    dict(tools_by_role={**self.tools, 'goals': {'set_goals'}}),
                    dict(allowed_asset_ids=frozenset({True})), dict(allowed_asset_ids={3}),
                    dict(approved_goal=' '), dict(approved_description=None)]
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                replace(self.policy, **values)
        for name in ('owner_current', 'owner_guard', 'owner_authorize', 'owner_asset_authorize', 'owner_validate_input'):
            with self.subTest(name=name), self.assertRaises(TypeError):
                replace(self.policy, **{name: None})
        self.assertEqual(self.owner.calls, [])

    def test_current_exact_host_revision_and_literal_true(self):
        for args in ((True, 22, 'operator-choice-v1'), (11, 23, 'operator-choice-v1'),
                     (11, 22, 'derived-config-digest'), (11, 22, None)):
            self.assertIs(self.policy.current(*args), False)
        self.assertEqual(self.owner.calls, [])
        for decision in (False, 1, 'yes', object(), True):
            self.owner.current_decision = decision
            self.assertIs(self.policy.current(11, 22, 'operator-choice-v1'), decision is True)
        self.assertEqual(len(self.owner.calls), 5)

    def test_guard_returns_original_without_entering_and_never_fabricates(self):
        with self.assertRaises(ValueError):
            self.policy.lifecycle_guard(11, True)
        self.assertEqual(self.owner.calls, [])
        guard = self.policy.lifecycle_guard(11, 22)
        self.assertIs(guard, self.owner.guard)
        self.assertEqual(guard.events, [])
        marker = RuntimeError('synthetic body')
        with self.assertRaises(RuntimeError) as caught:
            with guard:
                raise marker
        self.assertIs(caught.exception, marker)
        self.assertEqual(guard.events, ['enter', ('exit', marker)])
        bad = replace(self.policy, owner_guard=lambda *args: object())
        with self.assertRaises(TypeError):
            bad.lifecycle_guard(11, 22)

    def test_action_original_identity_partial_and_negative_grants_preserved(self):
        context = self.context()
        action = Action('model', 'complete', '{"messages": []}')
        for grant in (Grant(), Grant(True, False, True), Grant(True, True, True)):
            self.owner.actions[:] = [(context, action, grant)]
            self.assertIs(self.policy.authorize(context, action), grant)
        self.assertIs(context['runtime_context'], context)
        self.assertEqual([call[0] for call in self.owner.calls], ['action'] * 3)
        self.assertIs(self.owner.calls[-1][1], context)
        self.assertIs(self.owner.calls[-1][2], action)

    def test_explicit_role_tools_models_empty_choices_and_unknowns(self):
        cases = [('goals', Action('tool', 'set_goals', '{}')),
                 ('planner', Action('tool', 'add_intent', '{}')),
                 ('worker', Action('tool', 'record_fact', '{}'))]
        for role, action in cases:
            context = self.context(role)
            grant = Grant(True, True, True)
            self.owner.actions.append((context, action, grant))
            self.assertIs(self.policy.authorize(context, action), grant)
        self.owner.calls.clear()
        for role, action in [('planner', Action('model', 'complete', '{}')),
                             ('goals', Action('tool', 'record_fact', '{}')),
                             ('worker', Action('tool', 'bash', '{}')),
                             ('worker', Action('model', 'stream', '{}')),
                             ('goals', Action('other', 'set_goals', '{}'))]:
            self.assert_denied(self.policy.authorize(self.context(role), action))
        empty = replace(self.policy, model_roles=frozenset(), tools_by_role={r: frozenset() for r in self.tools})
        self.assert_denied(empty.authorize(self.context(), Action('model', 'complete', '{}')))
        self.assertEqual(self.owner.calls, [])

    def test_malformed_foreign_contexts_and_actions_do_not_delegate(self):
        action = Action('model', 'complete', '{}')
        contexts = [dict(self.context()), None]
        contexts += [self.context(**changes) for changes in (
            {'task_id': True}, {'exploration_id': 23}, {'run_id': 0}, {'run_id': True},
            {'revision': ''}, {'revision': 7}, {'role': 'unknown'}, {'intent_id': True},
            {'session_id': 'exp22-worker-i44'})]
        contexts += [self.context('worker', intent_id=0), self.context('worker', session_id='exp22-worker-i45')]
        for context in contexts:
            self.assert_denied(self.policy.authorize(context, action))
        for raw in ('[]', 'null', '{', '{"x":1,"x":2}', '{"x":NaN}', '{"x":1e9999}', '{"x":"\\ud800"}'):
            with self.subTest(raw=raw):
                self.assert_denied(self.policy.authorize(self.context(), Action('model', 'complete', raw)))
        self.assert_denied(self.policy.authorize(self.context(), Action('model', 'complete', '{}', True)))
        self.assertEqual(self.owner.calls, [])

    def test_blank_ids_and_normalized_repeated_gates_delegate_each_time(self):
        context = self.context('worker')
        actions = [Action('tool', 'list_facts', '{}', 'call-1'),
                   Action('tool', 'list_facts', '{ "limit": 100 }', ''),
                   Action('tool', 'list_facts', '{"limit":100}', '')]
        decisions = [Grant(True, True, True), Grant(True, False, True), Grant()]
        for action, decision in zip(actions, decisions):
            self.owner.actions.append((context, action, decision))
            self.assertIs(self.policy.authorize(context, action), decision)
        self.assertEqual(len(self.owner.calls), 3)

    def test_owner_decides_actual_run_and_configuration_authority(self):
        action = Action('model', 'complete', '{}')
        context = self.context()
        original = Grant(True, True, True)
        self.owner.actions.append((context, action, original))
        self.assertIs(self.policy.authorize(context, action), original)
        # Structurally valid alternate run/config reaches the owner, which denies.
        self.assert_denied(self.policy.authorize(self.context(run_id=34), action))
        self.assert_denied(self.policy.authorize(self.context(revision='other-config'), action))
        self.assertEqual(len(self.owner.calls), 3)

    def test_action_and_context_drift_after_owner_decision_is_denied(self):
        for target in ('context', 'action'):
            context = self.context()
            action = Action('model', 'complete', '{}')
            def drift(ctx, act):
                if target == 'context':
                    ctx['run_id'] = 34
                else:
                    object.__setattr__(act, 'name', 'other')
                return Grant(True, True, True)
            policy = replace(self.policy, owner_authorize=drift)
            self.assert_denied(policy.authorize(context, action))

    def test_input_exact_raw_strings_scope_and_literal_true(self):
        scope = self.goal_scope()
        goal, description = self.policy.approved_goal, self.policy.approved_description
        for decision in (True, 1, False, 'yes'):
            self.owner.inputs[:] = [(scope, goal, description, decision)]
            self.assertIs(self.policy.validate_input(scope, goal, description), decision is True)
        self.owner.calls.clear()
        for candidate, raw_goal, raw_description in (
            (scope, goal.strip(), description), (scope, goal, description + ' '),
            (self.goal_scope(task_id=True), goal, description),
            (self.goal_scope(run_id=0), goal, description), (self.planner_scope(), goal, description)):
            self.assertIs(self.policy.validate_input(candidate, raw_goal, raw_description), False)
        self.assertEqual(self.owner.calls, [])

    def test_assets_original_planner_and_graph_shapes_preserve_decisions(self):
        planner, graph = self.planner_scope(), self.graph_scope()
        planner_graph = self.graph_scope(worker='planner', intent_id=0)
        ids, summary = (3, 5), ' exact planner summary '
        for scope, supplied_summary in ((planner, summary), (graph, None), (planner_graph, None)):
            for grant in (Grant(), Grant(True, False, True), Grant(True, True, True)):
                self.owner.assets[:] = [(scope, ids, supplied_summary, grant)]
                self.assertIs(self.policy.asset_authorize(scope, ids, supplied_summary), grant)
        self.assertEqual(len(self.owner.calls), 9)
        self.assertIs(self.owner.calls[-1][2], ids)

    def test_empty_assets_still_require_original_owner_decision(self):
        policy = replace(self.policy, allowed_asset_ids=frozenset())
        scope, ids = self.graph_scope(), ()
        self.assert_denied(policy.asset_authorize(scope, ids, None))
        grant = Grant(True, True, True)
        self.owner.assets.append((scope, ids, None, grant))
        self.assertIs(policy.asset_authorize(scope, ids, None), grant)
        self.assertEqual(len(self.owner.calls), 2)
        self.assert_denied(policy.asset_authorize(scope, (3,), None))
        self.assertEqual(len(self.owner.calls), 2)

    def test_assets_malformed_unknown_and_foreign_requests_do_not_delegate(self):
        planner, graph = self.planner_scope(), self.graph_scope()
        for scope, ids, summary in (
            (graph, (True,), None), (graph, (0,), None), (graph, (2**63,), None),
            (graph, (3, 3), None), (graph, (9,), None), (graph, [3], None),
            (graph, (3,), 'summary'), (planner, (3,), None), (planner, (3,), ' '),
            (self.goal_scope(), (3,), 'summary'), (self.graph_scope(task_id=12), (3,), None),
            (self.graph_scope(worker='goals'), (3,), None),
            (self.graph_scope(intent_id=True), (3,), None),
            (self.graph_scope(worker='planner', intent_id=44), (3,), None)):
            self.assert_denied(self.policy.asset_authorize(scope, ids, summary))
        self.assertEqual(self.owner.calls, [])

    def test_scope_drift_after_input_and_asset_delegate_is_denied(self):
        goal_scope, asset_scope = self.goal_scope(), self.graph_scope()
        def input_drift(scope, *args):
            object.__setattr__(scope, 'run_id', 34)
            return True
        def asset_drift(scope, *args):
            object.__setattr__(scope, 'intent_id', 45)
            return Grant(True, True, True)
        policy = replace(self.policy, owner_validate_input=input_drift, owner_asset_authorize=asset_drift)
        self.assertIs(policy.validate_input(goal_scope, policy.approved_goal, policy.approved_description), False)
        self.assert_denied(policy.asset_authorize(asset_scope, (3,), None))

    def test_wrong_grant_types_are_errors_without_coercion(self):
        policy = replace(self.policy, owner_authorize=lambda *args: True, owner_asset_authorize=lambda *args: {'approved': True})
        with self.assertRaises(TypeError):
            policy.authorize(self.context(), Action('model', 'complete', '{}'))
        with self.assertRaises(TypeError):
            policy.asset_authorize(self.graph_scope(), (), None)

    def test_all_original_callback_exceptions_propagate_identically(self):
        for exception in (RuntimeError('synthetic owner error'), KeyboardInterrupt('synthetic interruption')):
            def fail(*args):
                raise exception
            policy = replace(self.policy, owner_current=fail, owner_guard=fail, owner_authorize=fail,
                             owner_asset_authorize=fail, owner_validate_input=fail)
            invocations = [lambda: policy.current(11, 22, 'operator-choice-v1'),
                           lambda: policy.lifecycle_guard(11, 22),
                           lambda: policy.authorize(self.context(), Action('model', 'complete', '{}')),
                           lambda: policy.asset_authorize(self.graph_scope(), (), None),
                           lambda: policy.validate_input(self.goal_scope(), policy.approved_goal, policy.approved_description)]
            for invoke in invocations:
                with self.assertRaises(type(exception)) as caught:
                    invoke()
                self.assertIs(caught.exception, exception)


if __name__ == '__main__':
    unittest.main()

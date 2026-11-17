"""Inert integration of explicit local policy with the actual runtime assembly.

These synthetic owner records are test data, not evidence of an existing task's
authority. Resource preparation uses the real constructors and normal resolver
entropy outside observation. Only construction and direct policy callbacks run;
no application, provider selection, task execution or transcript operation runs.
"""
from dataclasses import replace
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
import sys
import tempfile
import unittest

from artex.agent.goals import GoalScope
from artex.agent.operator_local_data_policy import OperatorLocalDataPolicy
from artex.agent.operator_runtime import compose_operator_runtime
from artex.agent.planner import PlannerScope
from artex.agent.runtime import Action, Grant, RuntimeContext
from artex.agent.task_runtime import TaskRuntimeAssembly
from artex.chat_dispatch import Cancellation
from artex.sidequestion import Model
from artex.tools.graph import GraphScope
from test_operator_runtime_inert_acceptance import (
    DenyExecutor, FailOnUse, UnexpectedEffect, make_fixture, observe_construction,
)


TASK, EXPLORATION, RUN, INTENT = 41, 42, 43, 44
HOST_REVISION = 'inert-host-policy-v1'
CONFIG_REVISION = 'inert-derived-configuration-v7'
GOAL, DESCRIPTION = 'Inert recorded goal.', 'Inert recorded description.'


class RecordedGuard:
    """Original caller-owned context manager; entering it performs no work."""
    def __init__(self, owner):
        self.owner = owner
        self.lock = RLock()

    def __enter__(self):
        self.lock.acquire()
        if not self.owner.active:
            self.lock.release()
            raise RuntimeError('Recorded owner revoked')
        self.owner.calls.append(('enter', self))
        return self

    def __exit__(self, kind, value, traceback):
        self.owner.calls.append(('exit', self, kind))
        self.lock.release()
        return False


class RecordedOwner:
    """Approve only registered synthetic objects under one active owner record."""
    def __init__(self):
        self.calls = []
        self.active = True
        self.error = None
        self.actions = []
        self.assets = []
        self.inputs = []
        self.grant = Grant(True, True, True)
        self.guard = RecordedGuard(self)

    def record(self, name, *values):
        self.calls.append((name, *values))
        if self.error is not None:
            raise self.error

    def current(self, task_id, exploration_id, revision):
        self.record('current', task_id, exploration_id, revision)
        return self.active and (task_id, exploration_id, revision) == (TASK, EXPLORATION, HOST_REVISION)

    def lifecycle_guard(self, task_id, exploration_id):
        self.record('guard', task_id, exploration_id)
        if not self.active or (task_id, exploration_id) != (TASK, EXPLORATION):
            raise RuntimeError('No recorded owner')
        return self.guard

    def authorize(self, context, action):
        self.record('authorize', context, action)
        if (self.active and context.get('run_id') == RUN and context.get('revision') == CONFIG_REVISION
                and any(context is c and action is a for c, a in self.actions)):
            return self.grant
        return Grant()

    def asset_authorize(self, scope, ids, summary):
        self.record('assets', scope, ids, summary)
        if (self.active and scope.run_id == RUN and scope.revision == CONFIG_REVISION
                and any(scope is s and ids is i and summary is text for s, i, text in self.assets)):
            return self.grant
        return Grant()

    def validate_input(self, scope, goal, description):
        self.record('input', scope, goal, description)
        return (self.active and scope.run_id == RUN and scope.revision == CONFIG_REVISION
                and any(scope is s and goal == g and description == d for s, g, d in self.inputs))


def make_policy_fixture(root):
    """Prepare actual resources, then wire stable adapter callbacks explicitly."""
    f = make_fixture(root)
    owner = RecordedOwner()
    with observe_construction() as policy_observation:
        policy = OperatorLocalDataPolicy(
            task_id=TASK, exploration_id=EXPLORATION, policy_revision=HOST_REVISION,
            model_roles=frozenset({'goals', 'planner', 'worker'}),
            tools_by_role={'goals': frozenset(), 'planner': frozenset({'list_facts'}),
                           'worker': frozenset({'list_facts'})},
            allowed_asset_ids=frozenset({51, 52}), approved_goal=GOAL,
            approved_description=DESCRIPTION, owner_current=owner.current,
            owner_guard=owner.lifecycle_guard, owner_authorize=owner.authorize,
            owner_asset_authorize=owner.asset_authorize, owner_validate_input=owner.validate_input)
    # Read each bound method once: assembly pinning is by identity, not equality.
    callbacks = {'current': policy.current, 'lifecycle_guard': policy.lifecycle_guard,
                 'authorize': policy.authorize, 'asset_authorize': policy.asset_authorize,
                 'validate_input': policy.validate_input}
    inputs = replace(f.inputs, policy_revision=HOST_REVISION, **callbacks)
    model = Model(profile_id=1, name='Inert model record', format='openai',
                  model='fixture-only', identity='inert-model-identity')
    goal_scope = GoalScope(TASK, EXPLORATION, RUN, CONFIG_REVISION, model, 'Inert goals label.')
    planner_scope = PlannerScope(TASK, EXPLORATION, RUN, CONFIG_REVISION, model, 'Inert planner label.')
    graph_scope = GraphScope(TASK, EXPLORATION, RUN, CONFIG_REVISION, 'worker', INTENT)
    contexts = {}
    for role in ('goals', 'planner', 'worker'):
        suffix = role if role != 'worker' else f'worker-i{INTENT}'
        metadata = dict(task_id=TASK, exploration_id=EXPLORATION, run_id=RUN,
                        revision=CONFIG_REVISION, role=role, session_id=f'exp{EXPLORATION}-{suffix}')
        if role == 'worker':
            metadata['intent_id'] = INTENT
        contexts[role] = RuntimeContext(metadata, Cancellation(), 100,
                                       FailOnUse('runtime.clock', f.calls))
    return SimpleNamespace(resources=f, owner=owner, policy=policy, inputs=inputs,
        callbacks=callbacks, contexts=contexts, goal_scope=goal_scope,
        planner_scope=planner_scope, graph_scope=graph_scope, policy_observation=policy_observation)


class OperatorLocalDataPolicyIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='artex-inert-policy-')
        self.addCleanup(self.temp.cleanup)
        self.f = make_policy_fixture(Path(self.temp.name))
        self.assertEqual(self.f.resources.calls, [])
        self.assertEqual(self.f.owner.calls, [])
        self.assert_no_effects(self.f.policy_observation, 0)

    def assert_no_effects(self, observation, constructions):
        self.assertEqual(observation, {'assembly_constructions': constructions,
                                      'effects': [], 'forbidden_calls': []})
        f = self.f.resources
        self.assertEqual(f.calls, [])
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])
        self.assertFalse(f.store._runtime_started)
        self.assertIsNone(f.store._runtime_lease)
        self.assertEqual(f.factory._cache, {})

    def compose(self, inputs=None):
        before = sys.getprofile()
        with observe_construction() as observation:
            assembly = compose_operator_runtime(self.f.inputs if inputs is None else inputs)
            self.assertIs(type(assembly), TaskRuntimeAssembly)
            self.assertTrue(assembly.validate_bindings())
        self.assertIs(sys.getprofile(), before)
        self.assert_no_effects(observation, 1)
        self.assertEqual(self.f.owner.calls, [])
        return assembly

    def test_actual_resources_and_stable_adapter_callbacks_compose_once(self):
        f, resources = self.f, self.f.resources
        assembly = self.compose()
        for attribute, original in [('executor', resources.executor), ('providers', resources.providers),
                                    ('transcript', resources.transcript), ('_limits', resources.limits)]:
            self.assertIs(getattr(assembly, attribute), original)
        self.assertEqual(assembly._pins, (resources.executor, resources.providers, resources.transcript,
            resources.tasks, resources.agents, resources.profiles, resources.factory))
        for attribute, name in [('_current', 'current'), ('_guard', 'lifecycle_guard'),
                                ('_authorize', 'authorize'), ('_assets', 'asset_authorize'),
                                ('_input', 'validate_input')]:
            self.assertIs(getattr(assembly, attribute), f.callbacks[name])
            self.assertIs(getattr(assembly, attribute).__self__, f.policy)
        self.assertEqual(assembly._revision, HOST_REVISION)
        self.assertIs(assembly.coordinator.guard, f.callbacks['lifecycle_guard'])
        self.assertIs(assembly.coordinator.journal.executor, resources.executor)
        self.assertNotEqual(HOST_REVISION, CONFIG_REVISION)
        self.assertEqual(assembly._configurations, {})
        self.assertIsNone(assembly._timing_policy)
        self.assertIsNone(assembly._process_policy)
        self.assertIsNone(assembly._activity_authorize)
        self.assertIsNone(assembly._cleanup_authorize)

    def test_composed_model_and_tool_callbacks_keep_original_objects(self):
        f, assembly = self.f, self.compose()
        with observe_construction() as observation:
            for role in ('goals', 'planner', 'worker'):
                action = Action('model', 'complete', '{"fixture":true}')
                f.owner.actions.append((f.contexts[role], action))
                self.assertIs(assembly._authorize(f.contexts[role], action), f.owner.grant)
                self.assertIs(f.owner.calls[-1][1], f.contexts[role])
                self.assertIs(f.owner.calls[-1][2], action)
            for role in ('planner', 'worker'):
                action = Action('tool', 'list_facts', '{}', 'fixture-call')
                f.owner.actions.append((f.contexts[role], action))
                self.assertIs(assembly._authorize(f.contexts[role], action), f.owner.grant)
            self.assertTrue(assembly.validate_bindings())
        self.assertEqual(len(f.owner.calls), 5)
        self.assert_no_effects(observation, 0)

    def test_composed_asset_and_input_callbacks_keep_original_scopes(self):
        f, assembly = self.f, self.compose()
        ids, summary = (51, 52), 'Inert recorded asset summary.'
        f.owner.assets.extend([(f.planner_scope, ids, summary), (f.graph_scope, ids, None)])
        f.owner.inputs.append((f.goal_scope, GOAL, DESCRIPTION))
        with observe_construction() as observation:
            self.assertIs(assembly._assets(f.planner_scope, ids, summary), f.owner.grant)
            self.assertIs(assembly._assets(f.graph_scope, ids, None), f.owner.grant)
            self.assertIs(assembly._input(f.goal_scope, GOAL, DESCRIPTION), True)
        self.assertEqual([call[0] for call in f.owner.calls], ['assets', 'assets', 'input'])
        self.assertIs(f.owner.calls[0][1], f.planner_scope)
        self.assertIs(f.owner.calls[0][2], ids)
        self.assertIs(f.owner.calls[0][3], summary)
        self.assertIs(f.owner.calls[1][1], f.graph_scope)
        self.assertIs(f.owner.calls[2][1], f.goal_scope)
        self.assert_no_effects(observation, 0)

    def test_composed_foreign_ids_tools_assets_and_input_decline_before_owner(self):
        f, assembly = self.f, self.compose()
        action = Action('tool', 'list_facts', '{}')
        with observe_construction() as observation:
            self.assertIs(assembly._current(TASK + 1, EXPLORATION, HOST_REVISION), False)
            with self.assertRaises(ValueError):
                assembly._guard(TASK, EXPLORATION + 1)
            f.contexts['planner']['task_id'] = TASK + 1
            self.assertEqual(assembly._authorize(f.contexts['planner'], action), Grant())
            f.contexts['planner']['task_id'] = TASK
            self.assertEqual(assembly._authorize(f.contexts['planner'], Action('tool', 'OtherTool', '{}')), Grant())
            self.assertEqual(assembly._assets(f.graph_scope, (99,), None), Grant())
            self.assertIs(assembly._input(f.goal_scope, GOAL + ' changed', DESCRIPTION), False)
        self.assertEqual(f.owner.calls, [])
        self.assert_no_effects(observation, 0)

    def test_original_current_and_guard_are_wired_without_wrapping(self):
        f, assembly = self.f, self.compose()
        with observe_construction() as observation:
            self.assertIs(assembly._current(TASK, EXPLORATION, HOST_REVISION), True)
            guard = assembly._guard(TASK, EXPLORATION)
            self.assertIs(guard, f.owner.guard)
            with guard as entered:
                self.assertIs(entered, guard)
            f.owner.active = False
            self.assertIs(assembly._current(TASK, EXPLORATION, HOST_REVISION), False)
        self.assertEqual([call[0] for call in f.owner.calls], ['current', 'guard', 'enter', 'exit', 'current'])
        self.assert_no_effects(observation, 0)

    def test_revoked_owner_is_not_replaced_by_allowlisted_action(self):
        f, assembly = self.f, self.compose()
        action = Action('model', 'complete', '{}')
        f.owner.actions.append((f.contexts['planner'], action))
        f.owner.active = False
        with observe_construction() as observation:
            self.assertEqual(assembly._authorize(f.contexts['planner'], action), Grant())
        self.assertEqual(len(f.owner.calls), 1)
        self.assert_no_effects(observation, 0)

    def test_foreign_run_and_configuration_are_still_decided_by_recorded_owner(self):
        f, assembly = self.f, self.compose()
        context, action = f.contexts['planner'], Action('model', 'complete', '{}')
        f.owner.actions.append((context, action))
        with observe_construction() as observation:
            context['run_id'] = RUN + 1
            self.assertEqual(assembly._authorize(context, action), Grant())
            context['run_id'] = RUN
            context['revision'] = 'other-valid-configuration'
            self.assertEqual(assembly._authorize(context, action), Grant())
            context['revision'] = CONFIG_REVISION
            self.assertIs(assembly._authorize(context, action), f.owner.grant)
        self.assertEqual(len(f.owner.calls), 3)
        self.assert_no_effects(observation, 0)

    def test_original_partial_grant_and_owner_exception_survive_composition(self):
        f, assembly = self.f, self.compose()
        context, action = f.contexts['worker'], Action('tool', 'list_facts', '{}')
        f.owner.actions.append((context, action))
        partial = Grant(True, False, True)
        f.owner.grant = partial
        error = RuntimeError('Inert owner failure')
        with observe_construction() as observation:
            self.assertIs(assembly._authorize(context, action), partial)
            f.owner.error = error
            with self.assertRaises(RuntimeError) as raised:
                assembly._authorize(context, action)
            self.assertIs(raised.exception, error)
        self.assert_no_effects(observation, 0)

    def test_optional_policies_remain_explicit_uncalled_original_callbacks(self):
        f = self.f
        activity, cleanup = f.resources.callbacks['activity_authorize'], f.resources.callbacks['cleanup_authorize']
        assembly = self.compose(replace(f.inputs, activity_authorize=activity, cleanup_authorize=cleanup))
        self.assertIs(assembly._activity_authorize, activity)
        self.assertIs(assembly._cleanup_authorize, cleanup)

    def test_composed_metadata_copy_and_resource_invalidation_preserve_original_checks(self):
        f, assembly = self.f, self.compose()
        f.resources.prompts['goals'] = 'Mutated caller label.'
        f.resources.catalog['planner'].clear()
        with observe_construction() as observation:
            self.assertEqual(assembly._prompts['goals'], 'Inert goals fixture.')
            self.assertEqual(len(assembly._catalog['planner']), 1)
            self.assertTrue(assembly.validate_bindings())
            f.resources.agents.executor = DenyExecutor(f.resources.calls)
            with self.assertRaises((RuntimeError, ValueError)):
                assembly.validate_bindings()
        self.assertEqual(f.owner.calls, [])
        self.assert_no_effects(observation, 0)

    def test_adapter_composition_rejects_foreign_executor_before_any_policy_or_io(self):
        f = self.f
        with observe_construction() as observation:
            with self.assertRaises((RuntimeError, ValueError)):
                compose_operator_runtime(replace(f.inputs, executor=DenyExecutor(f.resources.calls)))
        self.assertEqual(f.owner.calls, [])
        self.assert_no_effects(observation, 1)

    def test_equivalent_fresh_bound_method_cannot_replace_original_policy_pin(self):
        f, assembly = self.f, self.compose()
        replacement = f.policy.authorize
        self.assertEqual(replacement, f.callbacks['authorize'])
        self.assertIsNot(replacement, f.callbacks['authorize'])
        with observe_construction() as observation:
            assembly._authorize = replacement
            with self.assertRaises(RuntimeError):
                assembly.validate_bindings()
        self.assertEqual(f.owner.calls, [])
        self.assert_no_effects(observation, 0)

    def test_effect_observer_has_positive_control_and_restores_profile(self):
        previous = sys.getprofile()
        with self.assertRaises(UnexpectedEffect):
            with observe_construction() as observation:
                # An inert event, not an attempted file open or guard bypass.
                sys.audit('open', str(Path(self.temp.name, 'never-opened')), 'r', 0)
        self.assertEqual(observation['effects'], ['open'])
        self.assertIs(sys.getprofile(), previous)
        self.assertEqual(self.f.resources.calls, [])
        self.assertEqual(self.f.owner.calls, [])
        self.assertFalse(Path(self.temp.name, 'never-opened').exists())


if __name__ == '__main__':
    unittest.main()

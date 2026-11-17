"""Single-task narrowing of explicit operator policy, never a source of grants.

Selections describe available native/local graph actions, not their safety or
truth. Every allowed action, asset request (including an empty tuple) and exact
approved input still needs its original operator decision. The original owner
must establish the full run/configuration/intent identity, lifecycle, constraints,
evidence and model authority. Runtime revisions are derived configuration values;
they are deliberately not compared to the operator policy revision.

This adapter constructs no runtime, lock, guard, provider, store or transcript.
Its two-ID guard returns the operator's original context manager. That guard must
share the real lifecycle writers and support existing nested use; it is not the
resolver's snapshot guard or the engine owner's scope/token guard.

Configuration selections are frozen, but callbacks retain their own mutable
authority. This is not a sandbox against hostile Python callbacks or concurrent
mutation. Narrow scalar context/scope and Action fields are rechecked after a
decision without copying the self-referential RuntimeContext or caching grants.
No catalog schema, prompt meaning, evidence truth or action argument policy is
inferred. No activity, cleanup, timing or process policy is supplied here.
Authorization does not call current() or context.check(): original runtime gates
and owner callbacks retain currentness, cancellation and generation checks.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
import json
from types import MappingProxyType

from .goals import GoalScope
from .planner import PlannerScope
from .runtime import Action, Grant, RuntimeContext
from ..tools.graph import GraphScope


_ROLES = frozenset(('goals', 'planner', 'worker'))
_GRAPH = frozenset(('record_fact', 'list_facts', 'list_goals', 'prove_goal'))
_TOOLS = MappingProxyType({'goals': frozenset(('set_goals',)),
                          'planner': _GRAPH | {'add_intent'}, 'worker': _GRAPH})


def _positive(value):
    return type(value) is int and 0 < value < 2**63


def _runtime_revision(value):
    return type(value) is str and 1 <= len(value) <= 256


def _json_object(raw):
    """Check unambiguous JSON-object syntax only; do not rewrite or authorize it."""
    if type(raw) is not str:
        return False
    def pairs(items):
        result = {}
        for name, value in items:
            if name in result:
                raise ValueError('Duplicate action member')
            result[name] = value
        return result
    def constant(value):
        raise ValueError('Nonfinite action number')
    try:
        raw.encode('utf-8')
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
        if type(value) is not dict:
            return False
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return False
    return True


@dataclass(frozen=True, kw_only=True, repr=False, eq=False)
class OperatorLocalDataPolicy:
    """All thirteen inputs are explicit; empty selections deny corresponding work.

    Pass the five bound methods into OperatorRuntimeInputs. Keep those method
    objects stable after composition. The caller retains all original resources
    and chooses optional assembly policies separately.
    """

    task_id: int
    exploration_id: int
    policy_revision: str
    model_roles: frozenset[str]
    tools_by_role: Mapping[str, frozenset[str]]
    allowed_asset_ids: frozenset[int]
    approved_goal: str
    approved_description: str
    owner_current: Callable[[int, int, str], bool]
    owner_guard: Callable[[int, int], AbstractContextManager[object]]
    owner_authorize: Callable[[RuntimeContext, Action], Grant]
    owner_asset_authorize: Callable[[PlannerScope | GraphScope, tuple[int, ...], str | None], Grant]
    owner_validate_input: Callable[[GoalScope, str, str], bool]

    def __post_init__(self):
        if not _positive(self.task_id) or not _positive(self.exploration_id):
            raise ValueError('Exact positive task and exploration IDs required')
        if type(self.policy_revision) is not str or not 1 <= len(self.policy_revision) <= 128:
            raise ValueError('Explicit bounded operator policy revision required')
        if (type(self.model_roles) is not frozenset or any(type(role) is not str for role in self.model_roles)
                or not self.model_roles <= _ROLES):
            raise ValueError('Explicit immutable model-role selection required')
        if (not isinstance(self.tools_by_role, Mapping) or any(type(role) is not str for role in self.tools_by_role)
                or set(self.tools_by_role) != _ROLES):
            raise ValueError('Explicit tool selection for each execution role required')
        tools = dict(self.tools_by_role)
        if any(type(names) is not frozenset or any(type(name) is not str for name in names)
               or not names <= _TOOLS[role] for role, names in tools.items()):
            raise ValueError('Immutable subsets of native local tool names required')
        if type(self.allowed_asset_ids) is not frozenset or any(not _positive(v) for v in self.allowed_asset_ids):
            raise ValueError('Explicit immutable positive asset-ID selection required')
        if type(self.approved_goal) is not str or not self.approved_goal.strip() or type(self.approved_description) is not str:
            raise ValueError('Exact approved goal and description strings required')
        if any(not callable(getattr(self, name)) for name in (
            'owner_current', 'owner_guard', 'owner_authorize', 'owner_asset_authorize', 'owner_validate_input'
        )):
            raise TypeError('All original operator policy callbacks required')
        object.__setattr__(self, 'tools_by_role', MappingProxyType(tools))

    def _selected(self, task_id, exploration_id):
        return (_positive(task_id) and _positive(exploration_id)
                and task_id == self.task_id and exploration_id == self.exploration_id)

    def current(self, task_id: int, exploration_id: int, policy_revision: str) -> bool:
        if (not self._selected(task_id, exploration_id) or type(policy_revision) is not str
                or policy_revision != self.policy_revision):
            return False
        return self.owner_current(task_id, exploration_id, policy_revision) is True

    def lifecycle_guard(self, task_id: int, exploration_id: int) -> AbstractContextManager[object]:
        if not self._selected(task_id, exploration_id):
            raise ValueError('Foreign or malformed operator task scope')
        guard = self.owner_guard(task_id, exploration_id)
        if not callable(getattr(guard, '__enter__', None)) or not callable(getattr(guard, '__exit__', None)):
            raise TypeError('Original operator context manager required')
        return guard

    def _scope_identity(self, scope):
        task, exploration = getattr(scope, 'task_id', None), getattr(scope, 'exploration_id', None)
        run, revision = getattr(scope, 'run_id', None), getattr(scope, 'revision', None)
        if not self._selected(task, exploration) or not _positive(run) or not _runtime_revision(revision):
            return None
        return task, exploration, run, revision

    def _context_identity(self, context):
        if not isinstance(context, RuntimeContext):
            return None
        task, exploration = context.get('task_id'), context.get('exploration_id')
        run, revision, role = context.get('run_id'), context.get('revision'), context.get('role')
        if (not self._selected(task, exploration) or not _positive(run) or not _runtime_revision(revision)
                or type(role) is not str or role not in _ROLES):
            return None
        intent = context.get('intent_id', 0)
        if role == 'worker':
            if not _positive(intent):
                return None
            session = f'exp{exploration}-worker-i{intent}'
        else:
            if type(intent) is not int or intent != 0:
                return None
            session = f'exp{exploration}-{role}'
        if type(context.get('session_id')) is not str or context['session_id'] != session:
            return None
        return task, exploration, run, revision, role, intent, session

    @staticmethod
    def _action_identity(action):
        if (not isinstance(action, Action) or type(action.kind) is not str
                or type(action.name) is not str or type(action.call_id) is not str
                or not _json_object(action.input_json)):
            return None
        return action.kind, action.name, action.input_json, action.call_id

    def authorize(self, context: RuntimeContext, action: Action) -> Grant:
        identity, operation = self._context_identity(context), self._action_identity(action)
        if identity is None or operation is None:
            return Grant()
        role = identity[4]
        if operation[0] == 'model':
            allowed = operation[1] == 'complete' and role in self.model_roles
        else:
            allowed = operation[0] == 'tool' and operation[1] in self.tools_by_role[role]
        if not allowed:
            return Grant()
        grant = self.owner_authorize(context, action)
        if self._context_identity(context) != identity or self._action_identity(action) != operation:
            return Grant()
        if not isinstance(grant, Grant):
            raise TypeError('Operator action callback must return a Grant')
        return grant

    def _asset_identity(self, scope, summary):
        if type(scope) not in (PlannerScope, GraphScope):
            return None
        identity = self._scope_identity(scope)
        if identity is None:
            return None
        if type(scope) is PlannerScope:
            if type(summary) is not str or not summary.strip():
                return None
            return identity + ('planner', 0, summary)
        if type(scope) is GraphScope and summary is None and type(scope.worker) is str:
            if scope.worker == 'planner' and type(scope.intent_id) is int and scope.intent_id == 0:
                return identity + ('planner', 0, None)
            if scope.worker == 'worker' and _positive(scope.intent_id):
                return identity + ('worker', scope.intent_id, None)
        return None

    def asset_authorize(self, scope: PlannerScope | GraphScope, asset_ids: tuple[int, ...], summary: str | None) -> Grant:
        identity = self._asset_identity(scope, summary)
        if (identity is None or type(asset_ids) is not tuple or any(not _positive(v) for v in asset_ids)
                or len(set(asset_ids)) != len(asset_ids) or not set(asset_ids) <= self.allowed_asset_ids):
            return Grant()
        grant = self.owner_asset_authorize(scope, asset_ids, summary)
        if self._asset_identity(scope, summary) != identity:
            return Grant()
        if not isinstance(grant, Grant):
            raise TypeError('Operator asset callback must return a Grant')
        return grant

    def validate_input(self, scope: GoalScope, goal: str, description: str) -> bool:
        identity = self._scope_identity(scope) if type(scope) is GoalScope else None
        if (identity is None or type(goal) is not str or type(description) is not str
                or goal != self.approved_goal or description != self.approved_description):
            return False
        approved = self.owner_validate_input(scope, goal, description)
        return self._scope_identity(scope) == identity and approved is True

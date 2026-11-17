# Narrowing an existing operator's local-data policy

This policy adapter restricts decisions made by an **already-authoritative
operator**. It does not create that operator, acquire resources or make a task
ready to run. Original host ownership and its working policy delegates remain
prerequisites after the adapter has been constructed.

The reference construction candidate is
`0a73eb4603ca6c188504d62aa6bd3f75958a760e`, tree
`2910d992ef4703031c0c45e3691f83c47d0c68cb`, on published
`083400b1e84f8667caadd1a994f76130a693ce6d`. The new policy adapter and the
cleanup type correction are separate changes on that candidate; this document
does not establish their publication or installation. See
[operator runtime composition](OPERATOR_RUNTIME_COMPOSITION.md) for the fourteen
explicit resource/policy inputs and the held application-builder boundary.

## What is supplied, and what is delegated

`artex.agent.operator_local_data_policy.OperatorLocalDataPolicy` is a frozen,
keyword-only record with thirteen required fields. The operator supplies the
task/exploration identity, host policy revision,
allowed model roles, allowed tools per role, allowed asset IDs, and the exact
approved goal and description. The original host supplies all five delegates.

| Fields | Owner and meaning |
| --- | --- |
| `task_id`, `exploration_id` | The existing identities selected and owned by the host. Their presence in a record does not prove an active run or grant task ownership. |
| `policy_revision` | The host policy revision for these decisions. The owner must keep it consistent with actual policy changes. |
| `model_roles` | An explicit `frozenset` of model-execution roles permitted by this filter. The parent owns role/model configuration and the provider probe. |
| `tools_by_role` | Explicit role-keyed `frozenset` tool-name sets for all three execution roles, including deliberately empty sets. This is a permission filter, not a catalog, registration mechanism or grant. |
| `allowed_asset_ids` | The explicit asset-ID set permitted by this filter. Membership alone is not the owner's scope approval or evidence validation. |
| `approved_goal`, `approved_description` | The actual approved local-data input strings. The fixed-prompt/goal-to-intent track owns their content and interpretation; this guide supplies no example text, template or default. |
| `owner_current`, `owner_guard` | The original host's currentness and shared lifecycle-barrier callbacks. The adapter does not implement their ownership or synchronization. |
| `owner_authorize`, `owner_asset_authorize`, `owner_validate_input` | The original host's action, asset and input decisions. Narrowing these callbacks cannot replace their substantive checks. |

The public methods `current`, `lifecycle_guard`, `authorize`, `asset_authorize`
and `validate_input` match the corresponding composition-helper inputs. Local
task/action/asset/input checks may refuse an operation before delegating. A match
only permits consultation of the original owner; it is not a positive grant.
The filter does not turn a partial or negative owner decision into approval.
For an accepted lifecycle request it returns the owner's context manager; it
does not substitute a no-op guard or acquire independent ownership.

For action filtering, the adapter expects the existing `RuntimeContext` and
`Action` forms, a positive run identity, the role's matching session identity,
and JSON-object action arguments. The model action is named `complete`; tool
actions must belong to the role's explicitly selected native local-tool set.
These are structural checks, not evidence that the run is current or that the
arguments describe a permitted or true operation. Asset filtering requires a
matching planner/graph scope and a tuple of distinct allowed IDs; even an empty
asset tuple still needs its original owner's decision. Approved goal and
description matching is exact, without trimming or rewriting the input.

Locally rejected action/asset requests return a denied `Grant`; rejected input
or currentness checks return `False`. A foreign lifecycle request is rejected
rather than given a substitute guard. When identity rechecks pass, invalid owner
action/asset return types raise; owner callback exceptions propagate without repair, fallback or
grant caching. Rechecking narrow scalar identities after a decision does not
replace the owner's full run/configuration/intent checks.

The adapter has no activity, cleanup, timing or process policy. Those existing
host choices are separate from these five methods. It does not add providers,
tools, grants, schema, prompt readers, a configuration file or a secret reader.
Its frozen record/read-only tool-set copy does not freeze the state captured by
the owner's callbacks or make trusted Python objects a sandbox boundary.

## Wire only into an already-owned input record

This fragment requires an existing exact `OperatorRuntimeInputs` record named
`host_inputs`, with its original authoritative delegates and resources, plus
the actual approved selection/input values. It provides no values or defaults
for them and is not a runnable setup example. Do not replace an absent owner
with constant callbacks or use fixture grants as operator policy.

```python
from dataclasses import replace
from artex.agent.operator_local_data_policy import OperatorLocalDataPolicy
from artex.agent.operator_runtime import compose_operator_runtime

policy = OperatorLocalDataPolicy(
    task_id=approved_task_id,
    exploration_id=approved_exploration_id,
    policy_revision=host_inputs.policy_revision,
    model_roles=approved_model_roles,
    tools_by_role=approved_tools_by_role,
    allowed_asset_ids=approved_asset_ids,
    approved_goal=approved_goal,
    approved_description=approved_description,
    owner_current=host_inputs.current,
    owner_guard=host_inputs.lifecycle_guard,
    owner_authorize=host_inputs.authorize,
    owner_asset_authorize=host_inputs.asset_authorize,
    owner_validate_input=host_inputs.validate_input,
)
narrowed_inputs = replace(
    host_inputs,
    current=policy.current,
    lifecycle_guard=policy.lifecycle_guard,
    authorize=policy.authorize,
    asset_authorize=policy.asset_authorize,
    validate_input=policy.validate_input,
)
assembly = compose_operator_runtime(narrowed_inputs)
```

The replacement retains every other supplied input, including executor,
resolver, transcript, prompts, catalog, limits, policy revision and the explicit
activity/cleanup choices. This filter does not approve or narrow those optional
choices. The owning host must resolve them under its separately approved scope.
Keep the five bound method objects stored in the composed inputs stable; do not
swap callbacks or resources after assembly. No application is built here.

## Callback contracts are distinct

These are the existing consumer contracts, not proposed default implementations:

| Callback | Existing call/return boundary |
| --- | --- |
| Runtime `current` | Receives `(task_id, exploration_id, policy_revision)` and must return literal `True` for the current policy. The installed generation's separate checks still apply. |
| Runtime `lifecycle_guard` | Receives `(task_id, exploration_id)` and returns the owner's context manager. Lower goals/planner/worker scope guards are derived by the existing composition; they are not interchangeable callback signatures. |
| `authorize` | Receives `(context, action)` and returns the owner's `Grant`. Runtime action checks require its `permission`, `in_scope` and `approved` flags each to be literal `True`. Allowed role/tool names alone cannot supply this grant. |
| `asset_authorize` | Receives `(scope, asset_ids, summary)`; planner intent checks supply the summary and native graph checks forward `None`. The existing consumer requires a fully affirmative `Grant`. |
| `validate_input` | Receives `(goal_scope, goal, description)` and must return literal `True`. Matching approved text does not establish that constraints, asset scope and policy prerequisites have actually been handled; the owner still confirms them. |

The provider resolver has its own configuration-read callbacks:
`current(snapshot)`, `lifecycle_guard(snapshot)`,
`authorize_task(snapshot, role)` and
`authorize_profile(snapshot, role, profile_id)`. Its boolean decisions require
literal `True`. This policy adapter does not replace those callbacks or the
resolver/factory. Do not pass the runtime two-ID guard where a snapshot guard is
required merely because both are named `lifecycle_guard`.

Optional activity and cleanup also differ. Activity authorization receives a
projection proposal and returns `Grant`; cleanup authorization receives a
cleanup proposal and must return literal `True`. The corrected
`OperatorRuntimeInputs.cleanup_authorize` annotation is
`Callable[..., bool] | None`. A `Grant` or other truthy object is not accepted by
the cleanup consumer. Neither callback is added or approved by this adapter.

## Policy revision is not configuration revision

`OperatorRuntimeInputs.policy_revision` is the host policy revision. The
existing runtime derives a separate configuration digest from selected role
models, prompts, catalog, limits and policy configuration; later scopes carry
that digest as their `revision`. They are different identities.

This filter checks the structural form of the runtime configuration revision;
it does not compare that digest with `policy_revision` or certify a run merely
because the digest is a nonempty string. The original owner must still reject
stale/replaced runs, changed policy or profile selection, foreign resources and
invalid lifecycle state. Do not invent a digest, copy the policy string into a
scope revision, or invoke configuration selection/application building to fill
that gap outside its separately authorized boundary.

The owner must define how its currentness decisions and lifecycle guard share
the actual task/exploration, run, policy and configuration change authority.
It must fence relevant writers, invalidate stale decisions and retain admitted
work/resources through any required reconciliation. Stable IDs, a frozen
record, a returned `Grant` or a successful `validate_bindings()` call do not
establish that synchronization. The adapter supplies no restart adoption,
automatic retry, resource close, cleanup permission or replacement owner.

In particular, the original guard must share the writer barrier, support the
existing nested call pattern and avoid suppressing exceptions from the guarded
operation. Currentness must be a fresh literal-`True` decision, not a cached
permission receipt. These are obligations of the real owner, not properties
established by a callable check or by returning its context manager. Existing
provider checks before/after dispatch remain cooperative; they are not an atomic
network-send fence or proof that cancellation drained an admitted callback.

## Remaining handoffs and evidence

The dedicated DB/profile track supplies the original executor, stores,
resolver/factory and its separate schema/profile evidence. The parent owns
fixed prompts, role catalogs and provider configuration/probe results. The
host policy owner must supply the five authoritative delegates and the
transcript/resource owner must supply and retain the scoped transcript adapter,
store and recovery/storage policy. None is created by this filter.

Explicit model/graph permissions permit only the decisions actually granted by
that owner. They do not make model output true, make a graph fact real-world
evidence, establish containment, or prove a task objective. Graph-only work is
not automatically tool-free; native goals, planning and evidence tools still
have their existing behavior and authority checks.

Inert construction or direct predicate checks can establish the tested filter
and delegation behavior. They cannot establish live database/profile/transcript
readiness, working host lifecycle authority or real-model acceptance. Actual
selected-task output, persisted goals/evidence and fresh settlement of the
same original generation remain pending. Cleanup/drain observations and
target-facing authority remain separate.

The next application/bootstrap builder loads inventory and remains outside
this change's held boundary. No launcher, task creation/admission, startup,
driver call, provider call or alternate bootstrap is provided here. The
adapter is a narrower delegate to an existing owner, not the missing owner or
an execution-readiness receipt.

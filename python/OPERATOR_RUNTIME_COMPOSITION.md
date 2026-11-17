# Operator-owned runtime composition

This guide describes a construction-only handoff to the existing
`TaskRuntimeAssembly`. It does not launch an application or run a task.

The published reference is commit
`083400b1e84f8667caadd1a994f76130a693ce6d`, tree
`92c112001b9f628b8e62a3746a327809d8bcc875`. That revision contains
[`TaskRuntimeAssembly`](artex/agent/task_runtime.py), persisted-role provider
selection and the existing-task driver. The new operator helper described below
is a separate candidate on that base; its availability requires the associated
helper change. This document alone does not add the helper or establish its
publication, installation or review status.

## What the operator must already own

The helper receives resources and authorities supplied by the host. A path,
profile identifier, JSON object or callable with the right name is not a
substitute for those original objects and their policy contract.

| Input | Required host-owned value and responsibility |
| --- | --- |
| Executor | The exact existing executor shared by the task, agent-role and profile stores. The host retains its connection/transaction lifetime. This handoff opens no connection and supplies no DSN. |
| Provider resolver | An existing exact `TaskProviderResolver`, with its actual `TaskStore`, `AgentStore`, `ProfileStore` and `ProfileFactory`. Their executor must be the same object. Its currentness, lifecycle guard, task-read and profile-read authorities remain the resolver's existing authorities. |
| Transcript adapter | An existing exact `ChatTranscriptAdapter` backed by the exact `TranscriptStore`, with the host's scoped namespace, agent identity, recovery guard and storage ownership. This handoff does not create a directory, read a transcript or acquire namespace ownership. |
| Role prompts | Explicit nonempty prompts for `goals`, `planner` and `worker`. The host supplies the intended contents and records their revision; no prompt is discovered or selected from files automatically. |
| Graph catalog | Explicit catalog records for the `planner` and `worker` roles. Use the dedicated local-catalog contract and its exact reviewed output. Enabled records and agent bindings are metadata, not permission to execute a tool. |
| Policy revision | The host's explicit bounded revision identifying the supplied policies. It must track the real authority changes; an arbitrary constant does not make a policy current. |
| Runtime authorities | The actual `current`, `lifecycle_guard`, `authorize`, `asset_authorize` and `validate_input` callbacks used by the host. Retain their signatures, identity and authority domains from the existing runtime contract; do not substitute always-allow lambdas or inert test guards. |
| Activity and cleanup authorities | Explicit `activity_authorize` and `cleanup_authorize` values: each is the host's actual callback or an explicit `None` choice. Activity authorization returns `Grant`; cleanup authorization returns a boolean and its existing consumer requires literal `True`. Both fields are required; the helper supplies no policy default. `None` is not a cleanup permission or a promise that later work will drain. |
| Limits | An explicitly supplied `Limits` object carrying the host's bounded turn, token, time, tool-call, output-byte and request-byte choices. These values are not fresh execution authority or a hard wall-clock/invoice guarantee. |

The resolver's configuration-read authority and the runtime's execution authority
are separate. Supplying a resolver does not grant tools, approve a target or
replace runtime action/input checks. Stored role/profile precedence already
exists; the helper does not implement a second selector or enable global fallback.

## Compose only from existing host objects

The candidate module is `artex.agent.operator_runtime`. Its public API is a
frozen, keyword-only `OperatorRuntimeInputs` record and
`compose_operator_runtime(inputs) -> TaskRuntimeAssembly`. All fourteen fields
shown below are required. There are no timing/process options or application
builder options on this API.

The module also provides the typing protocol `OperatorExecutor`. The helper
checks that the supplied executor has callable `fetch_all`, `fetch_one`,
`execute` and `transaction` methods without invoking them. Their presence does
not prove transaction behavior, connection health or schema compatibility.

This is an integration fragment for the owning host, **not a runnable setup
sample**. Every name beginning with `host_` must already refer to the actual
resource, metadata or reviewed policy supplied by that host. The fragment does
not show how to construct those objects, obtain credentials, grant permissions
or start an application. The two optional authorities are explicit host choices,
even when their value is `None`.

```python
from artex.agent.operator_runtime import (
    OperatorRuntimeInputs,
    compose_operator_runtime,
)

inputs = OperatorRuntimeInputs(
    executor=host_executor,
    providers=host_provider_resolver,
    transcript=host_transcript_adapter,
    prompts=host_role_prompts,
    graph_catalog=host_role_catalogs,
    limits=host_limits,
    policy_revision=host_policy_revision,
    current=host_current,
    lifecycle_guard=host_lifecycle_guard,
    authorize=host_authorize,
    asset_authorize=host_asset_authorize,
    validate_input=host_validate_input,
    activity_authorize=host_activity_authorize,
    cleanup_authorize=host_cleanup_authorize,
)
assembly = compose_operator_runtime(inputs)
```

The returned object is the existing `TaskRuntimeAssembly`, not a wrapper,
readiness report or ownership receipt. The helper validates its supplied
structure/resource wiring, delegates to that constructor and checks the
assembly's retained bindings. The host remains responsible for the supplied
objects; the helper neither takes ownership nor closes them. Their original
owner must retain and reconcile them after any separately authorized future
operation, including uncertain outcomes. Validation errors propagate without
fallback resource creation or cleanup.

The optional callbacks have different return contracts. The existing activity
projector requires a `Grant` whose `permission`, `in_scope` and `approved` flags
are all literal `True`. The existing cleanup owner calls
`cleanup_authorize(proposal)` and requires the return value itself to be literal
`True`; a `Grant` object or another truthy value is rejected. The helper forwards
these callbacks unchanged and does not invoke them during construction. Its
cleanup annotation is `Callable[..., bool] | None`; this correction does not
change cleanup behavior, ownership or permission. Supplying the callback is not
permission to invoke the separate cleanup lifecycle.

## Construction boundary

For trusted explicit host inputs, the existing assembly constructor retains
resource, callback and limits identities, copies the supplied prompts/catalogs,
and derives configuration identity. It prepares references to the existing
bootstrap/engine components; preparing those objects is not bootstrap execution.

The helper's return value is a `TaskRuntimeAssembly`, not a
`TaskRuntimeApplication`. It does not read task inventory, select a role model,
construct a transport, call a provider, invoke a policy callback, write a
transcript, create/admit a task, tick a pump or start a listener/thread.

This is an in-process construction contract for trusted host objects, not a
sandbox for arbitrary user-defined objects or overridden methods. Type
annotations and frozen input records do not freeze the referenced executor,
resolver, callbacks, transcript store or nested catalog data. Keep those original
resources under their existing host ownership and mutation policy.

`assembly.validate_bindings()` checks retained in-process identities and
configuration consistency. A successful result is not a database ping, schema
check, credential check, catalog execution check or task-readiness certificate.
It does not prove that a policy would authorize a later action. A binding failure
must be reconciled by the host; it does not justify replacing pointers, resetting
ownership or rebuilding an initialized task in a fresh application.

In particular, construction does not prove that the stored profile or schema is
valid, that a transcript root exists and is usable, that catalog entries match
native handlers, or that the caller owns an executable task. An empty supplied
catalog does not establish a tool-free runtime: the existing assembly has native
goal/graph behavior. The dedicated catalog and execution policy must define any
later tool-free acceptance mode.

## Inputs supplied by the other integration tracks

These tracks supply evidence and original objects; this guide does not recreate
their setup commands or accept a report as a substitute for a resource.

| Existing track | Handoff into composition | Still not established by construction |
| --- | --- | --- |
| Database/profile setup | Original compatible executor and stores, existing resolver/factory, and evidence for the separately managed schema, Python bootstrap journal and intended persisted role/profile state. | The helper performs no schema installation, profile provisioning, inventory query or credential fetch. It cannot infer that an existing task is eligible. |
| Local catalog | The exact role-keyed catalog values approved by that track, together with the matching prompts, policy revision and host execution authorities. | Catalog copying/configuration identity is not full catalog validation, native-handler compatibility, current grants or permission to invoke a tool. |
| Provider probe/configuration | The host's already-owned resolver/factory and a separately recorded, correctly scoped probe/configuration result where applicable. Keys, origins and transport policy remain inside their existing host mechanisms. | The helper neither constructs nor invokes the probe/provider. A successful response or valid credential does not prove goal fulfillment, target authority or readiness of every selected role. |

There is no new config-file schema, credential-file reader, environment discovery,
default grant or provider-origin policy in this handoff. A configuration or probe
artifact should be matched to the actual profile/resource and policy revision by
the owning track. Do not feed redacted public DTOs to a private credential slot
or copy secrets into examples, argv, source files or review evidence.

The separately reviewed package-data, request serialization, SSE, pool-estimation,
lab-runbook and UI candidates remain separate changes. Their presence or test
results must not be inferred from this published base or from successful helper
construction.

## Stop before the application boundary

The next boundary is
[`build_task_runtime_application`](artex/server/task_runtime_setup.py). Its
published implementation composes the bootstrap application and performs an
explicit task-inventory load. It is not the same inert operation as constructing
`TaskRuntimeAssembly`.

Application/bootstrap building, inventory loading and subsequent lifecycle
operations are outside this construction-only change and remain subject to their
separate held integration boundary. This guide provides no invocation or
workaround for them. It does not replace that boundary with a new manager,
bootstrap, fake-provider demo, task-creation script or permissive host factory.

The published
[`drive_existing_task`](artex/agent/task_step_driver.py) consumes an already
assembled exact application and an eligible existing zero-timeout task under
exclusive manual ownership. It cannot consume this assembly return value
directly. Its host-wide tick budget is not a hard time limit, and successful
construction supplies neither a runnable handle nor selected-task settlement.
See the published [driver status](TASK_STEP_DRIVER_STATUS.md) for the API's
scope; historical candidate-status paragraphs do not change this guide's base.

## Evidence required beyond construction

An inert construction test can show that explicit objects are connected as
specified, input copies and identity pins behave as intended, and tested
construction paths avoid their guarded side effects. It cannot demonstrate live
storage, provider connectivity, an authorized execution grant or a completed
task. Record exact test/source identities and observed outcomes; do not turn
constructor success into a readiness boolean for an operator.

Real-model local-data acceptance remains a separate, unperformed
step. It requires actual selected-task output, persisted goals with supporting
evidence, and fresh settlement of that same original task generation with no
retained unresolved execution. A correct-looking answer, synthetic fixture,
preexisting `done` row or another task's settlement does not fill those fields.
Cleanup/drain observations remain separate as well.

Target-facing work needs its own containment/evidence policy and explicit host
authority. Neither this assembly, a catalog record nor a successful provider
probe supplies them. No real-task or target-facing acceptance is claimed here.

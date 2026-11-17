# Runtime application coordinator association

This change retains the bootstrap coordinator on an application bundle and
checks that it belongs to the supplied runtime assembly. It establishes an
in-process structural association. It does not issue an authenticated
application, supply the operator's lifecycle authority or make a task ready.

The reference base is local candidate
`1ed05817c5592ad7648f6f9921b900e6f824cbe7`, tree
`76684b55f085e6a3a10c360c41de88e97b457c50`. The coordinator association described
here is an additional change on that base. This document does not establish
publication, installation or live integration status.

## The relationship now checked

[`TaskRuntimeAssembly`](artex/agent/task_runtime.py) constructs and retains its
own coordinator. Separate assemblies can share an executor while retaining
different coordinators and policy callbacks. Sharing the executor object cannot
identify which assembly was supplied to an application's bootstrap builder.

The change adds a required `coordinator` field to
[`BootstrapApplication`](artex/agent/bootstrap_setup.py). The existing builder
returns the same coordinator object it received in that field; it does not
create a replacement coordinator. Direct construction of this record must now
supply the coordinator as well as its hosted application and admission
controller. There is no inferred coordinator or default authority.

[`TaskRuntimeApplication._check()`](artex/server/task_runtime_setup.py) checks
the retained bootstrap coordinator against `runtime.coordinator` by object
identity. Combining another assembly with the existing bootstrap bundle is
rejected when their coordinators differ, even if both assemblies use the same
executor and pass their own binding validation. Matching task IDs, equivalent
configuration or equal-looking metadata does not replace this identity check.

The existing task application builder supplies `runtime.coordinator` to the
bootstrap builder. The new retained field makes that association available to
later application checks. It does not change the builder's inventory-loading
boundary or replace its existing host, manager, runner, admission and executor
checks.

## What this does not prove

These Python records remain publicly constructible. The association is not an
unforgeable issuance receipt, a complete proof that every callback came from the
intended builder, or protection against arbitrary trusted-process mutation.
Frozen records do not make their referenced objects immutable. A matching
coordinator reference cannot certify the provenance of every other supplied
resource or callback.

The coordinator comparison does not invoke the operator's currentness, action, asset or input
policies. It does not prove that their decisions are fresh or affirmative, that
the lifecycle guard shares the real writer barrier, or that a task generation
is live. The original owners must retain and reconcile their resources through
any separately authorized operation and uncertain outcome.

This change creates no new owner, synchronization primitive, grant, provider,
transcript store or resource-closing policy. It does not grant admission,
execution, cancellation, cleanup, recovery or target-facing authority. The
existing driver and application operations retain their separate requirements.

## Remaining operator handoff

The explicit inputs described in
[operator runtime composition](OPERATOR_RUNTIME_COMPOSITION.md) and
[local-data policy narrowing](OPERATOR_LOCAL_DATA_POLICY.md) remain necessary:

| Owning track | Required handoff still outside this check |
| --- | --- |
| Dedicated database/profile track | The actual compatible executor, stores, resolver/factory and separate schema, journal and persisted-role/profile evidence. Their original resource identities and lifetimes must remain intact. |
| Original lifecycle and policy owner | Fresh currentness, the shared nested non-suppressing writer guard, full run/configuration/intent checks and the action, asset/evidence and exact-input decisions. The narrowing policy cannot create these authorities. |
| Parent prompt/catalog/provider track | Approved input, fixed prompts, role catalogs, model/profile selection, credential/origin policy and the separately scoped provider probe evidence. No prompt, catalog or provider default is added here. |
| Transcript and resource owner | The actual scoped transcript adapter/store, storage and recovery ownership, explicit limits and revision, and explicit activity/cleanup callback-or-None choices. Matching coordinator identity supplies none of these. |

The host policy revision and runtime configuration identity remain distinct.
The provider resolver's configuration-read callbacks remain distinct from
runtime execution authority. Existing resource-owner or manual-action tracking
objects do not substitute for the actual policy/lifecycle owner.

## Integration and acceptance limits

The next application/bootstrap builder performs inventory reads and remains
outside this change's held integration boundary. This status document supplies
no invocation, launch command, task-creation path or alternate bootstrap. The
coordinator field must not be used to reconstruct an application around missing
original owners or to rebind an already-owned task.

Source checks can verify field retention and the identity comparison. Inert
fixtures can establish acceptance and rejection for their tested object
relationships. Neither is evidence of live database, provider, transcript or
task readiness, and neither establishes authentic application issuance.

Real-task acceptance still requires actual selected-task output, persisted
goals and supporting evidence, and fresh settlement of that same original task
generation. Cleanup/drain observations and target-facing containment remain
separate. No real-task acceptance follows from a successful association check.

# Owner/writer reference protocol

This design describes an isolated, test-only owner/writer protocol. Its owner
has authority only over its explicitly supplied in-memory state and registered
fixture routes. It is not the missing production lifecycle owner, a database
barrier or a source of live execution grants. Do not pass its state, callbacks
or guards into application startup as fabricated operator authority.

The immutable source reference is
`85d5e908f8d129de5797b2ccd6bcd8d814b92268`, tree
`f3c5af6c39bb52e0d970d19b734e0100ac030529`. It includes the explicit operator
composition helper, narrowing policy and coordinator association described in
[operator runtime composition](OPERATOR_RUNTIME_COMPOSITION.md),
[local-data policy](OPERATOR_LOCAL_DATA_POLICY.md) and
[host binding status](OPERATOR_HOST_BINDING_STATUS.md). This guide and the
reference fixture are separate changes; their presence is not evidence of
publication, a working lab owner or task readiness.

The reference already includes a constructor compatibility change:
`BootstrapApplication` requires its `coordinator` field, and the original
builder forwards that same argument into the returned record. This forwarding
was checked through source/AST without invoking the builder.
`TaskRuntimeApplication._check()` rejects a retained bootstrap/runtime
coordinator mismatch. Matching relabeling still passes that structural check;
the required argument is not authenticated issuance or an actual-owner receipt.

## Real interfaces that still need an owner

The runtime requires original `current`, `lifecycle_guard`, `authorize`,
`asset_authorize` and `validate_input` delegates. The provider resolver separately
requires snapshot-shaped `current`, `lifecycle_guard`, `authorize_task` and
`authorize_profile` delegates. Their different signatures and decisions remain
distinct even if their real owner coordinates the same writers. A method with
the right name or a callable check cannot supply their authority.

Runtime currentness uses the host policy revision. Runtime scopes also carry a
derived configuration digest and original task/exploration/run identity; engine
checks retain the original manager token. None may be invented from an approved
task ID. Policy revision, configuration identity, fixture revision and database
transaction ownership are different domains.

The real DB/profile owner must identify the exact original executor object and
concrete type, original stores/resolver/factory, physical connection ownership,
transaction-bound object, nesting/thread behavior and lifetime. The helper
requires the same retained executor object across its stores; it does not verify
connection or transaction semantics. `PostgresExecutor` serializes one
exclusively used connection; `ExecutorPool` pins underlying executors per thread.
Sharing a pool object does not put different threads in one transaction.

The original transcript/resource owner, fixed prompts, role catalogs, approved
raw input, constraint/asset records, private provider policy and scoped probe
evidence remain required handoffs. Role/tool/asset membership and exact input
matching can narrow decisions; they do not establish substantive approval,
truth, evidence quality or containment.

## Participant matrix and uncovered paths

Participation means a route actually enters the reference protocol before its
underlying recorded mutation. A wrapper's existence does not make all callers
participants. No complete production-writer coverage is claimed.

| Route or authority | Reference treatment | Boundary |
| --- | --- | --- |
| `ProfileStore.set_active` | Execute the original method through the registered fixture route and inert transaction/recording executor; observe its actual `Query` mutations. | Changes global default-profile selection state. The resolver consults that fallback only when `allow_global` permits it; explicit role bindings/task chains retain their own precedence. This is not profile provisioning or a provider call. |
| Profile service/coordinator/cache publication | Source mapping only; deliberately excluded from the fixture. | Coordinator lock spans preflight, store mutation and apply/reentrant reload/cache publication. A store-only wrapper does not cover those surrounding phases. The default flag also enters full selected-profile identity; unchanged precedence does not guarantee unchanged identity. |
| `AgentStore.update_metadata` | Execute the original method through the registered fixture route; observe the recorded mutation. | Updates a custom agent's name/description only. It does not exercise `enabled`, `llm_profile_id`, role binding, prompt selection or runtime registration. |
| Direct calls to those stores | Retain a bypass counterexample outside the wrapper. | Demonstrates that direct-store access can evade the fixture barrier/revision; it is evidence against a full-coverage claim. |
| Other profile, role, prompt, catalog or in-memory policy writers | Require explicit future enumeration and owner wiring. | Not covered merely because related stores or fields exist. No synthetic all-writer certificate is issued. |
| Task metadata update | Source mapping only; not executed by this fixture. | `TaskManager.update_metadata` currently holds its manager lock while calling `repository.update`. The reference ordering must not be presented as a property already satisfied by this production path. |
| Idle task profile-chain replacement | Source mapping only; not executed or wired to the fixture. | Its separate binding, mutation authorization, guard, transaction/CAS and postcommit coordination contract is not covered by global profile activation. |
| Runtime/resolver currentness and authorization | Document the original interface; use only explicit fixture state for reference decisions. | No original manager generation, substantive live grant or real lifecycle owner is created. |
| Bound execution provider | Source mapping only. | Cooperative currentness/resource checks do not constitute an atomic network-send fence. No provider is called. |
| Callback-captured state and configuration/cache changes | Source mapping only. | A callable can retain identity while its captured state changes. Reference pins and copied metadata are not proof of shared locking or revocation of already-returned provider objects. |
| Cancellation signaling | Exercise only the reference's own independent event; existing runtime token interface is source-only. | Signal without acquiring the writer barrier. No live token, pause, stop or original generation is fabricated. |
| Other connections, processes and direct SQL | Uncovered. | A process-local reference barrier does not exclude external writers. |
| Application/bootstrap inventory, startup, task create/admit/tick, recovery/retention and traffic paths | Outside this fixture; specific review holds remain. | Startup-retention, task-create and traffic review bodies remain unassessed. Other listed calls are excluded here; existing published capabilities are unchanged. No invocation, replacement bootstrap, launcher or alternate demo is provided. |

The two executed store methods have different transaction/error contracts.
`ProfileStore.set_active` validates a positive signed-64-bit ID, clears existing
defaults and marks the selected profile through its yielded transaction
executor. A missing target must roll back the earlier clear. It returns `None`
on success; its storage wrapper preserves its domain errors but redacts other
ordinary exceptions. `AgentStore.update_metadata` validates the custom-agent key,
strips the name only, preserves the description and requires one affected row.
The description is preserved as the SQL parameter; `NULLIF` stores an empty
description as `NULL` (represented by `None` in the recording row).
It has no internal transaction or storage-error redaction; any outer transaction
grouping must be explicit. The single-statement route is not proof of batch
atomicity. Neither method consumes an owner
barrier or revision itself. An outer commit error after a store returns also
lies outside that store's error wrapper.

## Ordering requirements

The real owner must supply a participating-writer inventory and consistent lock
order. Existing goal/planner paths acquire a lifecycle guard, enter a
transaction and repeat freshness checks that may reach resolver guards and
configuration reads. The reference must exercise its declared nested order;
that observation is not a proof about unregistered production routes.

There is a specific additional integration boundary in `ProfileCoordinator`.
Source inspection shows its lock, called C here, held across preflight, store
mutation and apply/reentrant reload/cache publication. `ProfileFactory` uses its
own lock F during build/invalidation, including an injected transport-factory
callback during build. A normal store return precedes runtime apply; an apply
failure after a normal store return is persisted-but-apply-pending, not rollback
of that store operation. A store commit error can skip apply entirely; that
uncertain outcome is separate from apply failure after normal return.

Wrapping only the store would introduce C → reference-owner barrier → database
ordering while leaving preflight and apply/cache phases outside that wrapper.
An owner-held reader that calls `coordinator.snapshot` would instead introduce
owner → C ordering. That is a potential inversion inferred from source, not an
observed deadlock or a proposed production wrapper. The actual participants must
resolve the complete ordering before integration. The fixture exercises only
the stores and does not call the coordinator or factory. `ProfileFactory.pool`
constructs an LLM pool; it is not the database `ExecutorPool` discussed above.

Do not hold a real manager lock across SQL or blocking work in this reference.
Do not hold the fixture barrier while waiting for a different thread that needs
it, or hold a bounded pool lease while waiting for a writer that needs the same
resource. An owner's barrier and a transaction are separate responsibilities:
entering a transaction on a different connection does not redirect pinned stores
to it, and a size-one pool does not establish authority or safe ordering.

Cancellation signaling must not acquire the barrier held by the operation it is
trying to cancel. It is a cooperative stop signal; it cannot interrupt arbitrary
driver or callback work or prove that an admitted effect drained. Cancellation
and stale-revision checks must bracket the reference mutation boundaries.
A pre-entry refusal, a cancellation after admission and an already-in-flight
uncertain mutation are distinct outcomes. A late signal is not evidence that
an admitted mutation was automatically rolled back.

The original owner guard must propagate guarded exceptions. A database
transaction context is not automatically that guard: the real driver may have
deliberately suppressed rollback sentinels and its own nested transaction rules.
Injected transaction tests establish only the behavior of their selected
recording executor, not all driver exception or isolation semantics.

## Test-only reference state and operations

The support module is `python/tests/owner_writer_reference.py`. Its public names
are `ReferenceOwner`, `ReferenceSnapshot`, `AcquisitionProbe`,
`RecordingExecutor` and the `ReferenceStale`, `ReferenceCancelled`,
`ReferenceUncertain` and `ReferenceTimeout` errors. This is test support, not a
new `artex` runtime API or an installed operator implementation.

The companion support/core candidate is
`724c3369f2b475168ffaed2237f2b6c8b0801d9f`, tree
`c5cef0405b5ce94fdc136afa62af14ff5362855b`. Independent adversarial and combined
tree results are separate evidence, not implied by this source reference.

`ReferenceOwner` exposes `snapshot`, `current`, `guard`, `write`, `set_active`,
`update_metadata` and `cancel`. Its snapshot/revision identify only the fixture's
own state. They are not runtime scopes, provider identities, manager tokens or
host policy revisions. The reference methods must not be substituted for the
production callback signatures described above.

`ReferenceSnapshot` carries an opaque per-owner `owner_identity` and integer
`revision`; the identity is hidden from its representation. `current(snapshot)`
returns a boolean and is false while a writer is active. `guard(snapshot, *,
timeout=1.0, probe=None)` checks before the body and on normal exit. Neither
the token nor these checks prove an original production owner exists.
`snapshot()` exposes public revision metadata, not authenticated issuance or a
currentness receipt. Its inspectable fields can be copied. A snapshot captured
during a writer has `current=False` then may become current after a successful
outer exit; capture itself does not establish authority.

Reentrant lock acquisition does not override those state checks: a read guard
refuses while a writer is active. A successful write inside a read guard makes
the read guard's captured revision stale at its normal-exit check.

An admitted `write(snapshot, *, timeout=1, probe=None)` advances the reference
revision before the original store mutation and yields the new revision
snapshot. Old snapshots become stale; a nested writer must use the fresh
snapshot. A successful nested writer invalidates the outer yielded revision;
the latest revision becomes current only after the outer writer exits.
Metadata updates also invalidate the reference revision as a chosen
conservative fixture policy, not because name/description changes prove a role
binding change.

Pre-admission stale/foreign snapshots, cancellation or timeout leave the
revision unchanged and do not newly poison the owner. There is no reset for
cancellation or uncertainty.

A failure after writer admission permanently poisons the reference owner, even
if the inert transaction records rollback. The generation is not restored to
make old authority current; catching the admitted failure does not clear it.
An exception from a read-guard body instead propagates and releases the guard
without itself poisoning the owner. Sticky cancellation is separately signaled without
the barrier; late cancellation refuses success without claiming that prior
mutation was undone. These states prevent fixture reuse after uncertainty;
they are not production reconciliation, recovery or resource-close policies.

The `set_active` and `update_metadata` routes call the original store methods
with the explicitly supplied recording executor. `write` supplies the barrier
and revision protocol, not a database transaction. Profile activation enters its
original transaction; metadata remains a single direct statement. Tests may
explicitly group operations in `executor.transaction()` inside `write`.
The executor
recognizes only the selected profile-default updates and custom-agent metadata
update, using in-memory rows and recording actual `Query` execution. It is not
a SQL engine, database adapter, profile provisioner or live-resource replacement.
Each nested transaction snapshots its own frame and rolls back that frame on a
body error. An injected commit error records uncertainty and retains possibly
changed rows; the fixture does not fabricate rollback certainty.

Cancellation can race the checks or writer admission. There is no atomic
guarantee that no `Query` occurs after the signal; it is an independently
observable cooperative event, not a transaction fence.

`AcquisitionProbe` records the attempted lock acquisition and nonblocking
contention result. Timeouts bound reference acquisition waits; they are not a
hard bound on arbitrary admitted callbacks, driver operations or cancellation
drain. Tests use bounded synchronization and report a worker that does not join
as unresolved; they do not turn elapsed time alone into an exclusion proof.

## Evidence and progression

Tests must observe the actual inert writer mutation point, not only entry into
a wrapper. Mutual exclusion evidence needs explicit acquisition attempts and a
failed nonblocking contention observation, plus bounded events/joins. Elapsed
time without an observed attempt is not evidence that a writer was excluded.

Reference evidence must separately report normal writes, failed or uncertain
writes, stale/revoked authority before and after mutation, same-thread nesting,
exception propagation, cooperative cancellation and the direct-store bypass.
Only a recorded pass on a frozen combined tree is executed evidence; source
expectations and unrun cases must be labelled separately.

Once the actual production owner and resources are supplied, the next bounded
milestone is an isolated conformance fixture for that owner's declared writer
routes, followed by inert original-resource composition. The test-only owner
defined here cannot be substituted for that handoff. Application construction
and inventory/startup calls remain outside this fixture, alongside the specific
review holds above.

Real-task acceptance still requires actual selected-task output, persisted
goals with supporting evidence and fresh settlement of that same original
generation. Resource-contract tests, matching coordinator identity, profile
selection or a successful model response establish none of those outcomes.

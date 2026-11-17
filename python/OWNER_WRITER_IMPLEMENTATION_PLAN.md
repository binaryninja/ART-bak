# Genuine owner/writer implementation plan

This is a proposed engineering sequence, not an implementation of production
authority. The reference fixture owns only its explicit test state. Neither it,
an advisory lock, a transaction nor the operator-composition helper can supply
the real owner or certify task readiness.

The source base is `46d9d7553c68c25ecc9f940b55ab9b00dd53a9c4`, tree
`bcaf54cbf5a3c192e1f3ede2fbbc499e26215aaf`. No production change is part of this
plan. The separate disposable PostgreSQL work is resource-contract testing,
not dedicated-session provisioning or acceptance of an original ART task.

## Source ancestry and interface handoff

| Local source layer | What it establishes |
| --- | --- |
| `083400b1e84f8667caadd1a994f76130a693ce6d` | Reference for the original host-policy change inventory; no current remote status is implied here. |
| `0a73eb4603ca6c188504d62aa6bd3f75958a760e` | Explicit inert operator inputs/composition and tests/docs. |
| `1ed05817c5592ad7648f6f9921b900e6f824cbe7` | Local-data callback narrowing, corrected boolean cleanup annotation and tests/docs. |
| `85d5e908f8d129de5797b2ccd6bcd8d814b92268` | Required retained bootstrap coordinator and structural application association; 13 changed paths cumulatively against 083. |
| `46d9d7553c68c25ecc9f940b55ab9b00dd53a9c4` | Four additional test/documentation paths for the isolated owner/writer reference; production unchanged from 85d5. |

The [composition API](artex/agent/operator_runtime.py) still requires all fourteen
inputs. The dedicated DB/profile session supplies actual objects and their
ownership contract, not only paths, identifiers or a successful probe.

| Input | Required original owner and evidence |
| --- | --- |
| `executor` | Dedicated DB owner: exact original object/concrete type, exclusive connection or pool-factory ownership, transaction-bound object, thread/nesting/lifetime contract. Method presence is insufficient. |
| `providers` | Exact resolver with exact task/agent/profile stores sharing that executor, original factory and configuration-read authorities; intended persisted role/profile evidence. |
| `transcript` | Original scoped adapter/store, namespace/storage/recovery owner and retention obligations. This plan does not acquire or reset them. |
| `prompts` | Parent fixed-prompt track: actual goals/planner/worker prompts and their version. No default or file-discovery mechanism is proposed. |
| `graph_catalog` | Parent catalog track: matching planner/worker catalog records and native bindings. Metadata is not permission or evidence truth. |
| `limits` | Explicit operator limits; retain the object and define cancellation/resource-retention consequences. A limit is not guaranteed preemption. |
| `policy_revision` | Original authority's revision for substantive policy changes; distinct from configuration digest, DB revision and run/token identity. |
| `current` | Fresh `current(task_id, exploration_id, policy_revision) is True`; actual owner decisions, not cached permission. |
| `lifecycle_guard` | Original task/exploration shared-writer context manager with declared nesting, exception and cross-process participation. |
| `authorize` | Substantive action decision on `(context, action)`, returning a `Grant`; all three flags must be literal True to allow. |
| `asset_authorize` | Substantive `(scope, asset_ids, summary)` decision; planner summary and graph None remain different calls. |
| `validate_input` | Literal True on `(goal_scope, goal, description)` only when actual constraints/asset prerequisites are handled. Exact text matching alone is insufficient. |
| `activity_authorize` | Explicit original projection `Grant` callback or None, with its separate authority. |
| `cleanup_authorize` | Explicit original cleanup callback returning literal True, or None. This field grants no permission to invoke cleanup. |

Resolver `current(snapshot)`, `lifecycle_guard(snapshot)`,
`authorize_task(snapshot, role)` and
`authorize_profile(snapshot, role, profile_id)` remain separate read-policy
interfaces. Their boolean decisions require literal True. The runtime retains
original scope/run/token and derived-configuration checks in addition to host
policy currentness. Bootstrap coordinator identity is structural association,
not authenticated application issuance; matching public records can be relabeled.

## Writer inventory: enroll, restrict or explicitly exclude

Every deployment writer must be placed in the same inventory before claiming
coverage. The table names identified surfaces and unresolved categories. It is
not proof that all writers in an actual deployment have been discovered.

| Writer or publication route | Required treatment and present boundary |
| --- | --- |
| `ProfileService` → `ProfileCoordinator.save` | Migrate the whole build/preflight → save → runtime-apply path, including service authority and secret-preserving update semantics. A store wrapper alone is insufficient. |
| Coordinator/store `set_active` | Enroll both surrounding coordinator phases and the two-update default switch. Global fallback depends on `allow_global`; default flags also enter selected-profile identity. |
| Direct `ProfileStore.save` / `set_active` | Restrict access or make them participating internal operations under the same transaction/revision protocol. Direct calls must remain a negative control until enforced. |
| `ProfileStore.delete` | Currently unsupported; do not treat the stub as covered deletion or implement reference repair incidentally. |
| `ProfileCoordinator.reload`, `_apply`, runtime/snapshot readers | Version cache/publication and readers, not just durable rows. Reload/cache clearing and returned provider references require explicit ownership. |
| `ProfileFactory.build`, `invalidate`, `pool` | Include cache writers and readers. Build mutates shared cache; pool constructs an LLM pool, not a database ExecutorPool. |
| Agent service/store metadata | `AgentStore.update_metadata` changes custom name/description only. Its own method has no transaction. This cannot stand in for enabled/role/profile-binding coverage. |
| Agent creation/provisioning | `AgentStore.create_metadata` / `create` initialize role/enabled and, for create, prompt records. Elsewhere-owned provisioning; not executed by this fixture. |
| Agent role/profile binding, prompt and capability writers | Identify/enroll every editor, original-process/config/admin route and direct SQL writer of fields consumed by selection. No complete role-binding editor inventory is established by the metadata tests. |
| Idle task profile-chain service/store | Preserve its distinct authorization, task-before-profile locking, revision/CAS and postcommit coordination. Global default activation does not cover task chains or active cursors. |
| Conversation profile binding | Python `ConversationStore.update_profile` and its service route are separate persisted bindings. Assign their lifetime/version domain explicitly; a task-owner test does not cover conversations. |
| Task metadata/cache update | Source-observed manager lock across repository update is a migration issue. Do not assert a safe global ordering or exercise this path through the fixture. |
| Task creation/initial bindings, archive-restoration initialization, startup/retention or traffic-related writers | Specific held review bodies remain unassessed. Name them as rollout exclusions, not verified participants; do not inspect or reroute them here. |
| In-memory policy, prompt/catalog/limits and callback-captured state | Callable identity can remain stable while its captured decisions change. Enroll those mutations with versioning and rejection of stale decisions. Parent tracks retain content ownership. |
| Other processes, original Go deployment, provisioning/import/migration/admin scripts, direct connections and SQL | Inventory identities and privileges with the dedicated owner. Either enroll through an enforced interface or deny the operation for covered data. Unverified clients remain explicit coverage gaps. |

The checked-in Go declaration inventory includes `SaveProfile`,
`DeleteProfile` / `DeleteProfileContext`, `SetActiveProfile`, `SetAgentLLMProfile` in
`db/config.go` and their management routes; `ReplaceTaskLLMProfiles` and
`MarkTaskLLMProfileQuotaExhausted` / `MarkTaskLLMProfileQuotaExhaustedAtRevision`
in `db/task_context.go`;
`UpdateConversationProfile` in `db/conversation.go`; and `SetLLMRetryPolicy` in
`db/llmretry.go`. These original-process writers must participate or be excluded
through deployment/privileges. Declaration discovery is not a review of held
initialization bodies, nor proof of cross-language protocol compatibility.
Retry-policy consumers also need explicit mapping; the Python factory does not
automatically read global retry settings.

Advisory locks are cooperative: clients that omit the matching lock can still
write. A same-process lock or a successful two-process test cannot certify an
external writer's participation. Enforced access may require dedicated database
roles/privileges or another reviewed write boundary; administrative bypasses
must remain documented and trigger maintenance/reconciliation rules. This wave
does not change those privileges. [PostgreSQL 17 locking](https://www.postgresql.org/docs/17/explicit-locking.html)

## Phase 1: define genuine authority and version domains

Specify an original-owner interface tied to the real lifetime and approved
records: task/exploration/run authority, full policy/configuration decisions,
writer registration, invalidation and resource retention through uncertainty.
The test snapshot is public copyable metadata and cannot become this receipt.

Start the engineering design with a coarse participating provider-configuration
domain covering defaults, profile content, role bindings and task-chain
selection dependencies. Include conversation/profile-binding dependencies in
the inventory, while keeping conversation lifetime and task/generation
authority distinct from shared configuration.
Use an explicitly assigned, collision-free key registry with database/namespace,
scope and version ownership; do not derive keys with a process-random hash or
assume matching integers in another database coordinate anything. Decide
granularity and split domains only after dependency and ordering tests justify it.

Define separately: host policy revision; durable configuration revision;
selected-model/configuration digest; original run/token identity; committed
operation receipt; and applied-publication revision. Revisions describe an
outcome domain, not generic permission. A rolled-back attempted write need not
advance the durable revision, while its local admission must still be revoked;
the fixture's permanent poison is evidence about the fixture, not a mandated
production recovery policy. Ambiguous commit must leave authority unavailable
until the original operation receipt can be reconciled.

**Exit evidence:** owner/interface and writer inventory review, exact approved
records, complete version meanings and a refusal policy for missing/stale/
unenrolled writers. No production guard is claimed at this phase.

## Phase 2: make lock ordering implementable

The present source has coordinator C held through preflight, store mutation and
apply/reentrant reload; factory F is held during build/invalidation, including
an injected transport-factory callback. A store-only barrier introduces
C → owner → database, while an owner-held reader invoking coordinator snapshot
introduces owner → C. This potential inversion is source-inferred, not an
observed deadlock. Manager metadata also holds its manager lock across SQL.

The proposed target is a phase-separated order, requiring engineering changes:

1. Name the short owner/admission metadata mutex `O_meta` separately from the
   original runtime lifecycle guard `O_guard`. Take only `O_meta` to obtain an immutable
   operation identity and expected revisions; release it before pool waits,
   SQL, callbacks or transport preparation. Never hold the real manager lock
   across those waits. Cancellation signaling uses its own independent channel.
2. Acquire an executor/connection, open the explicit outer transaction, acquire
   declared database domains in one total order, then lock/revalidate rows in a
   consistent order (preserving task-before-profile rules where applicable).
   No C/F/manager publication lock may be held across this phase. Do not upgrade
   a preexisting lower-order lock opportunistically; reject incompatible nesting.
3. Commit and release database locks before short publication locks. Prepare
   candidates outside shared cache state, then use version-checked publication.
   Readers may not hold C/F while consulting owner or DB state. Cross-process
   publication needs the same revision protocol, not just an in-process lock.

This order is a proposal, not a fact about current call sites. Existing runtime
guard → transaction → repeated freshness/resolver calls must be audited and
migrated without weakening their original owner checks. Define how nested
calls join the same transaction and identity; different executor wrappers,
threads or connections cannot silently join it. ExecutorPool's per-thread lease
is not a cross-thread transaction. Never wait for work needing a resource held
by the caller.

Releasing `O_meta` does not implement `O_guard` and does not grant execution.
The genuine owner must define the admission point, original generation/token
validation, writer invalidation of new entries and retention of outstanding
operation receipts. Specify how `O_guard` acquires or joins the declared durable
domains at protected decisions/mutations and how nested original guards prove
the same owner, operation and transaction context. Recheck currentness at the
protected boundary after waits; do not let the copied entry revision stand in
for the owner's later decision. These interfaces and their revocation rules
must be reviewed before migrating the existing guard call sites.

Transaction-scoped advisory locks release at transaction end; session locks
survive rollback. Prefer a reviewed transaction-scoped protocol for these short
mutations, with explicit connection/statement/lock/process deadlines. A
standalone lock query outside an enclosing transaction does not retain its
protection for later store calls. Advisory acquisition alone also does not
refresh an older transaction snapshot: a fresh READ COMMITTED query after
acquisition and a pre-established Repeatable Read snapshot are different.
[Lock lifetime](https://www.postgresql.org/docs/17/explicit-locking.html),
[snapshot semantics](https://www.postgresql.org/docs/17/transaction-iso.html).

**Exit evidence:** instrumented owner/coordinator/factory/manager/executor/DB
lock graph, bounded contention/nesting tests and enrolled-route audit. Do not
replace the original owner guard blindly with `executor.transaction()`; driver
rollback sentinels may be intentionally suppressed and transaction semantics
are not the required non-suppressing authority boundary.

## Phase 3: separate preparation, durable mutation and publication

`ProfileCoordinator.save` currently performs build → store save → runtime apply.
`ProfileFactory.build` already inserts into shared `_cache`; it is not isolated
staging merely because the transport constructor promises no network I/O.
Enclosing the existing coordinator method in another database transaction can
publish uncommitted state before that outer transaction rolls back. It does
**not** make cache/runtime publication rollback-atomic.

Refactor toward an explicit, reviewed sequence:

1. Validate and prepare an unpublished candidate with no insertion into shared
   factory/cache/runtime state. Reuse neither a live provider nor a prior grant
   as proof that the expected configuration remains current.
2. Under the ordered transaction, revalidate expected durable versions and
   substantive permission; perform the selected mutation, bump its durable
   revision and record an operation/publication-pending receipt atomically.
   Preserve original writer validation and error semantics. This requires new
   schema/protocol work owned with the dedicated DB session, not this fixture.
3. After confirmed commit, publish only the committed revision using an
   idempotent, version-checked operation. If a newer revision wins, discard or
   reconcile the stale prepared candidate; never overwrite the newer snapshot.
   Record applied status through a separate short transaction if needed, with
   explicit crash windows and a reconciliation policy. Acknowledgements must
   identify the process/consumer instance: one cache's applied revision does
   not make another process's old cache current. Readers must validate their
   own committed/applied identity, not trust a notification as authority.
4. On apply failure, keep durable state as committed-but-publication-pending.
   Reject new use that cannot demonstrate the required applied revision;
   acknowledge the pending outcome without claiming rollback or old-state
   success. Existing returned providers/resources cannot be forcibly revoked by
   cache invalidation; the original owner retains/drains or quarantines them.

The actual admission/revocation interface needs design alongside these records.
Do not hold a DB lock across a model request to manufacture atomic network
fencing. Current provider checks are cooperative. Missing or uncertain
publication/ownership evidence must not be repaired by a guessed revision,
default grant, replacement manager or blind repeat of a mutation.

An actual coordinator-save/publication counterexample is separate from the
two-store primitive tests. It requires explicitly expanded scope for
`get`/`save`/`active`/`pool`, the complete private-profile row projection and
explicitly inert transport/LLM-pool construction. The minimal primitive fixture
must not be expanded silently. Report any separately authorized counterexample
under its own frozen source, isolation and outcome receipt; no pass is implied here.
Its result establishes only a negative publication/rollback counterexample,
not genuine owner integration or a corrected publication implementation.
A narrower `ProfileCoordinator.set_active` counterexample avoids save
but still needs expanded `get`/`active`/`pool` projection and explicitly inert
construction scope. It also requires separate coordination before execution.

## Phase 4: distinguish cancellation and outcome evidence

| Observed state | Required result and next evidence |
| --- | --- |
| Rejected before admission | No operation receipt treated as committed; verify no writer mutation. Cancellation/timeout is not permission to retry under another owner. |
| Body failure with confirmed rollback | Record rollback of the transaction and original error, release its participating locks; distinguish local invalidation from durable revision. |
| Commit acknowledgement lost/ambiguous | Preserve operation identity and uncertain outcome; independently read the authoritative receipt/rows before another mutation or publication. |
| Commit confirmed, publication fails | Keep committed revision and pending apply state. No claim of DB rollback; test restart/reconciliation separately before enabling it. |
| Cancellation while blocked/in flight | Signal independently of the held barrier, observe participant error/transaction termination and committed rows; a late signal may follow a successful commit. |
| Cleanup wait expires | Report exact unresolved process/backend/resource ownership. Do not claim forced termination, released foreign locks or safe resource closure. |

`pg_cancel_backend` returning True means a signal was sent. It is not a receipt
for rollback, transaction settlement or completed cleanup. Only known fixture
participant backends may be targeted in this wave; real-owner cancellation
requires its separate authority. [PostgreSQL 17 signaling](https://www.postgresql.org/docs/17/functions-admin.html)

## Phase 5: evidence ladder and bounded rollout

The earlier in-memory suite proves only its selected recording semantics and
deliberately includes direct-store bypasses. The isolated PostgreSQL 17 fixture
may add real-driver transaction, advisory-lock, fresh-read and cancellation
observations for permitted original `ProfileStore.set_active` and
`AgentStore.update_metadata` calls. New processes must open their own connections;
observe distinct Python/backend PIDs, positive lock contention, original Query
effects, commit/rollback and cleanup. Keep direct nonparticipating writes as a
counterexample, not an unexpected claim of advisory enforcement.

Only root-attested disposable configuration may be used by the assigned test
workers. No ambient DSN discovery, existing DB, full repository schema, task
table, real task/run/grant, provider or startup operation belongs in that
fixture. Absent/mismatched attestation must refuse before mutation. Do not infer
a live result from test source; bind each actual result to its frozen tree,
fixture identity, command, counts and outcome/cleanup report outside this plan.

After these resource contracts, implement and independently test one genuine
owner domain with all declared routes and enforced exclusions. Add coordinator
preparation/commit/publication fault injection and separate-process readers,
then migrate remaining eligible writers. Inert original-resource composition is
a later integration check. Application calls are outside this plan's tests;
specific review holds are not silently lifted. Existing published capabilities
are not globally revoked by these fixture exclusions.

## What needs engineering, operator input or a separate boundary

| Category | Exact missing input/work |
| --- | --- |
| Engineering | Original-owner implementation, writer registration/enforcement, version/receipt schema, isolated factory preparation, coordinator publication protocol, manager-lock migration, lock-order tests and uncertain-outcome reconciliation. These are not missing config switches. |
| Dedicated DB/profile owner | Actual executor/type/connection and store identities; schema/journal/profile evidence; all DB roles, direct/original-process/admin writers; deployable privilege and domain-key ownership; migration window and transaction contracts. No secrets are requested in public evidence. |
| Human/operator policy choices | Named authoritative owner; approved goal/description, constraints/assets/actions/evidence and participating principals; intended role/profile/global-fallback choice; acceptable revocation/drain and uncertain-outcome handling. Engineering may propose mechanisms but cannot invent these approvals. |
| Parent-owned content/provider work | Fixed prompts/catalogs, intended model/origin/credential policy and scoped probe evidence. This plan does not replace those outputs or require another selector. |
| Specific restrictions | Startup-retention, task-create and traffic review bodies remain held/unassessed, as do prior publication/Library/browser/lease restrictions. Identify dependence and stop at that boundary; do not route equivalent work through a fixture. |
| Later real-task acceptance | Actual selected-task output, persisted goals/evidence and fresh same-generation settlement under the original owner. DB exclusion or a successful model response establishes none of these. |

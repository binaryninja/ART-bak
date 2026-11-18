# Opt-in committed profile mutation

This component separates profile mutation from publication by one explicitly
constructed coordinator. It does not implement a task lifecycle owner, migrate
every profile writer, register a server route, provision a database or launch an
application. The original `ProfileCoordinator` remains available with its prior
behavior. Wrapping that original coordinator in an outer database transaction
does not make its Python cache publication rollback-atomic.

The starting source is local `8ce24bb6b6a56029562ccfb2168d8b8b64ef4bf7`, tree
`e38c899e0df669fea5b73fd557979478a879a6b5`, equivalent to published
`4ab902d44d6953ab9487adea88154a0a2dc5271b`. This note describes the separately
implemented opt-in path; it does not assert a current remote publication status.
The participating-writer table below records this component's original scope.
Later host and shared-configuration layers add explicit opt-in enforcement;
their current builder choices are called out below and their coverage is
documented in [shared configuration ownership](SHARED_CONFIGURATION_OWNERSHIP.md).

## Resource and publication boundaries

`ProfileFactory.prepare(profile)` and `prepare_pool(profiles)` construct ordinary
configured providers and an ordinary LLM pool without reading or writing the
factory cache. They copy and validate supplied profile metadata and share the
factory's existing health registry. The supplied transport factory must be
construction-only. A prepared provider is not a commit receipt or permission to
call a model. Preparation does not install a disposer contract or revoke a
previously returned provider. Existing `build`, `pool` and original-coordinator
behavior remain separate.

`PostgresExecutor.committed_transaction()` owns a new outer transaction on the
original adapter. It rejects an existing adapter or ambient driver transaction;
it does not promote a caller-owned scope. Normal return requires the driver
transaction to finish successfully with an idle, open connection. A nested
ordinary transaction failure remains sticky inside this opt-in boundary, even
if a driver suppresses its rollback sentinel or caller code catches the error.
That prevents normal outer completion from falsely reporting success. Ordinary
transaction/savepoint behavior outside this boundary stays compatible.

`ExecutorPool.committed_transaction()` pins one exact `PostgresExecutor` for the
scope, rejects an existing same-thread lease and unsupported executor types, and
retires an unsuccessful lease. It yields the original pool, so its store keeps
the same executor identity. Driver errors and uncertain commit outcomes are not
automatically retried. Neither a pool lease nor a transaction context supplies
the original non-suppressing task lifecycle guard.

`CommittedProfileCoordinator(store, factory, *,
pending_error=CommittedProfileApplyPending)` requires exact original
`ProfileStore` and `ProfileFactory` objects with a supported original executor.
It offers `save(profile) -> int`, `set_active(id) -> None`, explicit
`reload() -> None`, `runtime() -> (pool, public_snapshot)`, `snapshot()` and
`status()`. Construction does not load profiles or publish a runtime.

A mutation captures its prior profile under one owned read transaction, prepares
outside that transaction, then rechecks and mutates under a second owned outer
transaction. The first contains only reads; this is not an SQL READ ONLY claim.
After the mutation transaction returns normally, captured profiles are prepared
outside the database transaction and published. There is no postcommit database
reread in that operation. Separate transactions and comparisons are not a
cross-process writer fence or durable version protocol.

The instance rejects busy/reentrant operations immediately. Its publication is
unavailable from operation entry through commit and apply; a short publication
lock is separate from database work and transport construction. This bounds the
instance's lock interactions, not the lock order of every existing host writer.
Readers holding earlier pool/provider objects retain them.

`ProfilePublicationStatus(operation, phase, profile_id, committed,
runtime_applied)` is frozen outcome metadata, not an authority receipt. Its phases
are `unconfigured`, `in_progress`, `not_committed`, `outcome_unknown`,
`committed_pending` and `applied`. `committed` describes a mutation, not an
explicit reload; `None` preserves mutation uncertainty. After a confirmed
mutation commit, an ordinary apply error raises the selected `pending_error`.
The default `CommittedProfileApplyPending` also carries outcome metadata.
`BaseException` control outcomes retain their identity while status records a
confirmed-but-unapplied mutation when applicable. A caller must not interpret
`committed=False` as proof of physical rollback, interpret every exception as
rollback, or repeat an ambiguous mutation.

## Existing consumer compatibility

| Consumer | Explicit integration contract |
| --- | --- |
| `ProfileService(store, coordinator=..., runtime_snapshot=...)` | Its mutation protocol uses `save` and `set_active` without an exact coordinator type check; `runtime_snapshot=coordinator.snapshot` supplies the expected public DTO. To preserve the existing confirmed-pending HTTP response, construct this opt-in coordinator with `pending_error=server.profiles.ProfileApplyPending`. The default `CommittedProfileApplyPending` is a distinct exception, so plain injection does not by itself obtain that service response. No authenticated host or route is created by this wiring. |
| Existing profile runtime builder | At this component's starting baseline the builder constructed only the original provider `ProfileCoordinator`. That remains its default with no host options. Later explicit host branches use `ProfileHost`, reflected in the bundle's `ProfileCoordinator \| ProfileHost` annotation: `profile_host_policy=` constructs a host on new resources, while `profile_host=` reuses a matching, already enrolled host. Both require the separate `profile_reconcile_authorize=` callback; the two host options cannot be combined. These are profile-host composition choices, not task issuance or proof of application startup. See [profile host reconciliation](PROFILE_HOST_RECONCILIATION.md) and [shared configuration ownership](SHARED_CONFIGURATION_OWNERSHIP.md). |
| Conversation runtime | The interface requires an actual `RuntimeBundle` and the same executor in its profile store. It uses coordinator snapshot metadata but also calls the original factory's `build`. Matching the snapshot method alone does not enroll conversation selection/publication or prove its authority. No conversation builder is executed here. |
| Stats/task-read runtime snapshots | `runtime_snapshot(selector)` supplies `RuntimeStats` for a selected authorized task. It is not the zero-argument public profile `snapshot()` dictionary. Do not substitute one for the other. |
| Task provider resolver | Requires exact original task/agent/profile stores and `ProfileFactory`, a shared executor and four real read-policy callbacks. It consumes neither provider coordinator as an ownership authority. |

The provider `ProfileCoordinator` and the agent `BootstrapCoordinator` are
different objects and protocols. The latter is retained by `TaskRuntimeAssembly`
and the bootstrap/application association checks. This new profile coordinator
does not replace that agent coordinator or produce a valid task application.

## Participating writers and exclusions

| Surface | Coverage and owner obligation |
| --- | --- |
| Explicit opt-in coordinator profile save/default switch | Participates only when the host deliberately selects this instance and its exact store/executor/factory. This is the implemented profile-publication boundary, not authentication or task authority. |
| Original `ProfileCoordinator.save`, `set_active`, `reload` | Unchanged, nonparticipating path. Existing behavior must not be described as migrated by merely adding the new class. |
| `ProfileService` | Manual injection can opt into the methods and the explicit pending-error contract above. No default registration or application wiring changes here. The surrounding authenticated host remains responsible for access. |
| Direct `ProfileStore.save` / `set_active` | Can bypass the new coordinator and leave its runtime snapshot stale. A negative fixture must retain this distinction; the new API does not prevent direct SQL. |
| Profile deletion | Still unsupported by the original store. No reference-lock or failover repair is added. |
| `ProfileFactory.build`, `pool`, `invalidate` and old returned providers | Remain independent cache/publication surfaces. Clearing a cache does not revoke objects held elsewhere. One coordinator does not synchronize other coordinators or processes. |
| `AgentStore.update_metadata` | Only custom-agent name/description, not enabled/role/profile binding. This is not evidence of selection-writer enrollment. |
| Agent role/profile/prompt writers and provisioning | Outside this profile path. Dedicated owners must inventory actual writer identities and shared configuration effects. |
| Idle task profile-chain edits | Keep their separate authorization, task-before-profile locking, revision/CAS and explicit `coordinate` contract. Global profile/default writes do not cover task-chain selection. |
| Conversation profile binding | `ConversationStore.update_profile` and its service are a separate persisted binding and lifetime domain. |
| Original Go processes, admin scripts, migrations and direct SQL | Unenrolled unless the deployment explicitly migrates or restricts them. Neither an in-process gate nor the new transaction method enforces cross-process participation. |
| In-memory policy, callbacks, prompts/catalogs/limits and existing task readers | Their original guards, versions and retained references still require owner coordination. Callable identity alone does not freeze captured state. |
| Startup/retention, task-create and traffic review bodies | Specific review holds remain unassessed. Other application/driver operations are outside this acceptance; published capabilities are not revoked by that exclusion. |

## Exact missing authority handoff

The DB/profile owner must supply the exact original `ProfileStore` and executor,
the concrete adapter or pool ownership contract, exclusive connection/factory
lifetime, intended profiles/default semantics and the identities of permitted
writers. No connection, schema, credential discovery, role assignment or
provisioning helper is supplied here. Hosts must keep uncertain operations and
their resources owned until they have a real reconciliation outcome.

The transport owner supplies a construction-only factory with approved origins
and credentials handled by its existing private mechanism. Parent-owned prompt,
catalog and provider-probe tracks remain separate. Successful construction,
valid profile fields and a passed probe do not grant model execution.

The runtime still requires the original five callbacks: fresh
`current(task_id, exploration_id, policy_revision)`, the shared-writer
`lifecycle_guard`, action `authorize`, `asset_authorize` and `validate_input`.
Its explicit optional activity and cleanup callbacks keep their own contracts.
The resolver separately requires `current(snapshot)`, `lifecycle_guard(snapshot)`,
`authorize_task(snapshot, role)` and
`authorize_profile(snapshot, role, profile_id)` on actual stores sharing one
executor. None is generated, replaced or automatically invoked by profile
mutation. Host policy revision, configuration identity and transaction outcome
are different domains.

The unresolved hook is enrollment with that real shared lifecycle/configuration
owner: who may admit a change, how all relevant writers invalidate readers, how
nested guards acquire or join the same domains, and how old provider operations
are retained or drained before revocation. Profile publication alone cannot
answer those decisions. Missing owner decisions cannot be filled by a test
snapshot, a matching coordinator field or an always-allow callback.

## Acceptance limits and next integration

`tests/test_committed_profile_host_contract.py` composes actual profile, factory
and executor objects with a bounded in-memory driver and a transport request
tripwire. It exercises original store query construction and publication
sequencing, plus the original transport-independent `ProfileService` handler's
DTO/error projection with synthetic requests. The handler cases do not construct
a router, inject an allow callback, test authentication or start a listener.
They distinguish success, preflight refusal, ambiguous acknowledgement and the
explicit confirmed-pending error mapping. No PostgreSQL, provider, application,
task, startup path or runtime cleanup is executed. Injected status and transaction
outcomes are contract evidence, not real-driver certification.

The next acceptance requires separately coordinated real-driver tests of the
reviewed path, then explicit migration/enforcement of the actual participating
writer set and original owner lock order. Confirmed SQL commit and Python
publication still need durable, versioned per-consumer reconciliation for
crashes, other processes and lost acknowledgements. Actual output, persisted
goals and selected-task settlement remain separate real-task evidence.

See [operator composition](OPERATOR_RUNTIME_COMPOSITION.md) for the fourteen
original inputs and [the implementation plan](OWNER_WRITER_IMPLEMENTATION_PLAN.md)
for the larger owner/writer migration. No launcher or alternative bootstrap is
introduced by this status note.

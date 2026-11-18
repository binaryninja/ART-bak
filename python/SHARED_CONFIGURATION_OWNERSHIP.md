# Shared configuration ownership

This component supplies a stateful, process-local configuration owner. Explicitly
configured participants receive capabilities; admitted operations share a work
barrier, generation state and revocation/drain accounting. Selected existing
store and factory entry points check that ownership is already held. It governs
configuration access within one process. Original task/run authority and
database-wide enforcement still require separate integration.

The starting source is local `95039baed63fadc69d0f8eac7e51ba230161f791`, tree
`bc25dccaa9ef079ee9a07add2d38ce9616084aa9`. This guide describes the added
configuration-owner path and does not assert remote publication, application
startup or a completed real task. See [profile host reconciliation](PROFILE_HOST_RECONCILIATION.md)
for the existing host/request-policy and committed-publication boundaries.

## Capabilities and generation observations

`ConfigurationOwner(domain, *, participants=...)` requires an explicit mapping
of participant names to `ConfigurationPermissions(read=..., write=...)`.
Both permission fields are required frozensets of operation names. No wildcard,
unknown participant or omitted permission set supplies a grant. The owner copies
the declared policy; a domain string is not proof that two processes share a
lock, database or authority.

`issue(participant)` returns an owner-issued `ConfigurationCapability`.
It is a stable grant within that owner's process-local lifetime, not a durable
credential or a generation snapshot. `snapshot(capability)` returns a
`ConfigurationSnapshot` with `capability` and `generation` fields. It is an
owner-checked observation of the current generation. Copying public fields does
not create an independently issued capability or observation. The trusted host
retains the owner and decides who receives issued handles; issuing a configured
participant name does not authenticate an HTTP principal.

`current(capability)` checks the live grant; `current(snapshot)` additionally
checks its generation. `permits(capability, operation, *, mode=...)` checks the
explicit operation permission. Neither method enters the work barrier, grants
a task/model action or proves that a supplied profile value is approved.
Another thread's active writer makes its generation observation unavailable;
`snapshot` can refuse and `current(snapshot)` can be false while that writer
remains admitted, even though the stable capability itself has not been revoked.

`guard(capability, operation, *, mode, expected, timeout)` requires every keyword
argument. It yields a `ConfigurationLease` with `snapshot`, `capability`,
`operation` and `mode` fields. The expected observation belongs to the same owner
and capability. Stale, foreign, revoked or otherwise invalid inputs refuse
admission rather than silently refreshing themselves.

An outer writer admission advances the generation before its work. Validation
failure, an exception or an attempt that performs no SQL does not restore the
previous generation. This invalidates older reader observations; it is not
evidence of SQL commit, rollback or successful publication. An acquisition or
pre-admission refusal is distinct from an admitted write attempt. Compatible
nesting does not manufacture a fresh outer admission. Failure does not by itself
revoke the stable capability. A fresh observation after failed work is not
permission to blindly repeat an uncertain mutation.

The named store, factory and bridge integrations use these operation names:

| Mode | Operation names |
| --- | --- |
| Write | `profile.save`, `profile.set_active`, `task_profiles.replace_idle`, `conversations.update_profile`, `factory.invalidate` |
| Read | `profile.reconcile`, `factory.build`, `factory.pool`, `factory.prepare`, `factory.prepare_pool` |

These are configuration-access modes, not SQL READ ONLY promises. For example,
factory construction can allocate transport objects, and `build` can populate
a private cache. A write hook must match the active admitted write operation;
having another write name somewhere in a capability is not sufficient. Factory
reads need their explicitly configured read rights as well.
`factory.pool` also invokes member `factory.build` access; `factory.prepare_pool`
invokes member `factory.prepare` access. Permission for the pool operation alone
does not implicitly grant those member operations.
Owner scope failure does not undo cache entries or transport allocations made
before that failure. Ordinary pool construction can leave earlier cache entries;
`prepare_pool` retains its separate cache-free construction behavior.

## Lock order, revocation and lifetime

Ownership precedes the registered executor/pool transaction and factory work.
All outer admissions serialize on one work barrier; read mode describes access
rights, not concurrent reader-lock semantics.
The synchronous guard tracks leases by thread, not by coroutine or async task.
Callers must not suspend into unrelated asynchronous work while holding it;
same-thread nesting does not establish isolation between such tasks.
An outer guard checks the current thread's registered-executor state before and
after acquiring its barrier. It must refuse acquisition from an unowned ambient
transaction or lease rather than create a database-to-owner lock inversion.
This checks registered original resources; it does not inspect every raw
connection or other executor in the process.

Store/factory hooks validate an already-active matching capability and operation;
they do not acquire the owner work barrier from inside SQL or factory locks.
Each of the four store mutation methods checks before entering its original
transaction and again inside that transaction, before its first business Query.
The second check covers a caller revoked while waiting for the executor lock.
It does not promise that the driver has issued no transaction-control SQL.
`ProfileStore` keeps its existing storage-error redaction, so an ordinary late
ownership denial inside its storage wrapper can surface as `ProfileStorageError`.
Nesting must stay with the same owner and exact capability. Read-to-write
upgrades and cross-owner nesting are refused. Nested read scope does not become
write access merely because an enclosing capability has write permissions.
The operator must compose this order with original task/resolver guards; the
configuration owner does not repair every existing manager or database lock
order automatically.

The owner guard is non-suppressing. Revocation must remain signalable while
another thread holds the work barrier. `revoke(capability)` refuses further use
of that issued capability, not every capability with the same participant name;
`close()` refuses further owner admissions/access. Entered
work retains its lock/resource responsibility until it exits. Later access
checks and clean exit detect revocation, while an existing body error remains
an error. Signaling does not preempt a driver call, terminate a thread or prove
that an external operation stopped.
Revocation can race work after an ownership check. Refusal at guard exit does not
prove that SQL was never issued, that a change rolled back or that a returned
provider has stopped.

`drain(timeout=...)` returns `ConfigurationDrain(drained, active, generation,
closed)`. It observes the owner's tracked work, not all provider requests,
database clients or tasks. An active thread cannot wait for itself to drain.
Drain does not close admission: revoke the relevant handles or close the owner
before relying on a quiescence observation.
A timeout is an unresolved drain observation, not permission to unlock another
thread, close its resources or force cleanup. The migrated persisted resolver
path releases its configuration guard before provider transport. The owner
context manager cannot prevent a privileged caller from invoking a previously
returned provider while holding a guard; that direct use remains outside this
enforcement.

`bind_executor(resource, owner)` and `bind_factory(resource, owner)` enroll exact
supported original objects. Enrollment requires quiescent composition before
serving consumers. Registration cannot retroactively fence an operation whose
entry check already ran while its resource was unbound, nor does it drain such
legacy work. The host must establish that quiescence before enrollment.
Subsequent eligible calls from existing and future store instances using that
same registered executor are subject to the hooks. Another executor connected
to the same database is not automatically the same enrolled resource.

Bindings retain a fail-closed lifetime marker. `is_bound(resource)` remains true
when the registered owner has disappeared or closed; `bound_owner` refuses an
unusable binding rather than restoring legacy access at checked entry points.
There is no unregister or
rebind escape that turns an enrolled resource back into an unowned one. The
owner must remain retained by its actual host. These checks are not a sandbox
against arbitrary privileged Python mutation, raw cursor access or other
processes.

## Exact participating entry points

| Entry point | What becomes checked; what remains separate |
| --- | --- |
| `ProfileStore.save` | Already-held `profile.save` write access at entry, repeated inside the original transaction before its first business Query. Existing and newly constructed stores on the exact enrolled executor must obey it. Profile content approval is additional to an operation permission. |
| `ProfileStore.set_active` | Already-held `profile.set_active` write access before and inside its original two-update default-switch transaction. Default uniqueness remains schema-owned. |
| `TaskStore.replace_llm_profiles_idle` | Already-held `task_profiles.replace_idle` write access before and inside the original transaction for an existing eligible task. Original task/exploration identity, idle-state checks, task-before-profile locks and chain-revision CAS remain; cache/audit reconciliation is still caller-owned. |
| `ConversationStore.update_profile` | Already-held `conversations.update_profile` write access before and inside the original transaction for an existing conversation binding. Original conversation/profile checks remain; this does not initialize a conversation or issue task authority. |
| `ProfileFactory.build`, `pool`, `prepare`, `prepare_pool` | Enrolled factory read access, including cache-hit and empty-pool paths. The supplied transport factory must remain construction-only. Prepared providers and constructed transports are not model-execution grants. |
| `ProfileFactory.invalidate` | Enrolled factory write access. Clearing cache entries does not revoke provider objects already held elsewhere. |
| Legacy `ProfileCoordinator.save`, `set_active`, `reload` | Refusal for an enrolled store executor or factory, even from an active capability, because its old cache/preflight/apply ordering is not migrated into the committed path. Unbound legacy behavior remains separate. |

The four named store mutation methods are the store coverage boundary. Existing
HTTP services do not automatically acquire a configuration capability merely
because their eventual store method now checks one. Explicit adapters must
enter the correct owner operation before reaching the hook; otherwise the
protected call must refuse before business queries. Existing route authentication, record
authorization and lifecycle checks remain necessary.
This also does not turn every store read into a guarded reader API: the original
resolver's read/currentness policies and other consumers' access controls remain
separate from these mutation hooks.

An already-existing task can have status `created`; allowing its eligible chain
edit is not a task-creation API. Agent name/description metadata is also not
agent role/enabled/profile/prompt binding. Neither case broadens these four
hooks into initialization or all-selection-writer coverage.

## Connecting committed profile publication

`OwnedProfilePolicy(owner, capability, *, snapshot, executor, factory, revision,
authorize_value, timeout)` connects this real configuration owner to the reviewed
`ProfileHostPolicy`, exposed as `.policy`. Every input is explicit; the executor
and factory must already be bound to the same owner. Construction does not
silently enroll them. `authorize_value(operation, value, revision)` must return
literal `True` and supply substantive profile-value approval
in addition to the capability's operation permissions. An operation name alone
does not approve a model, endpoint, credential change, record or active-profile
choice.

The bridge supplies `ProfileHostResources(executor, factory)` through the
optional `ProfileHostPolicy.resources` field. This frozen record associates the
policy with exact original resource objects. `ProfileHost` rejects a coordinator
using different resources during construction, before policy callbacks or
profile work, and pins the association for later checks. Matching fields are a
resource-consistency check, not task authority or authenticated application
issuance. The four original host-policy decision fields remain required.

An explicitly assembled `ProfileHost` uses those same enrolled executor/factory
objects. The profile runtime builder can reuse it through the explicit
`profile_host=` option, together with `profile_reconcile_authorize=`. This option
excludes `profile_host_policy=` and requires the exact executor, factory,
construction-only transport factory and health registry, a matching
`ProfileHostResources` association, the server's pending-error contract, and one
existing live configuration owner for both executor and factory. It neither
constructs an owner nor enrolls or substitutes resources.

The supplied-host branch performs guarded initial reconciliation and checks
resource/public-method bindings, currentness and the returned protected router
before exposing the bundle. HTTP authentication and the explicit request
authorizer remain additional to the host policy. Failure does not undo earlier
reads or transport construction, close caller-owned resources or justify a blind
reconciliation retry. Its persisted application boundary remains separate;
inert tests that mock that boundary do not prove startup or task authority.

With no host options, the existing default still constructs the legacy profile
coordinator. The earlier `profile_host_policy=` branch creates new resources and
still refuses a policy associated with a different pre-existing factory.
Supplying an existing host is a separate opt-in resource-reuse path, not automatic
migration of those defaults or an integrated task launcher.

The bridge retains its observation per thread and adopts only the fresh
observation issued by that thread's own admitted guard. Another thread cannot
refresh the first thread's post-exit currentness through the same bridge.
It must not automatically adopt a generation created by another writer. After
an external enrolled write, the bridge remains stale until its owner deliberately
obtains a new observation and reconstructs the bridge. A stable capability can
remain live while that particular bridge observation is stale.

The existing host's passive `snapshot()` and `status()` check identity bindings;
they do not call `owner.current` or acquire this configuration guard. An external
enrolled write stales the bridge's next admitted operation, but does not
asynchronously clear another host's already published passive snapshot or revoke
old providers. Such a snapshot is an observation of prior publication, not proof
of current configuration authority. Consumers needing fresh publication must
retain an explicit currentness/reconciliation boundary.

Generation invalidation, committed mutation and host publication are three
different facts. The existing committed coordinator still owns its database
transactions; the configuration guard must not wrap it in another database
transaction. A confirmed mutation with pending host publication must remain
distinguishable from an uncertain write. Reconciliation reloads current stored
configuration; it never repeats an ambiguous mutation or retroactively proves
what committed. See [committed profile mutation](COMMITTED_PROFILE_MUTATION_STATUS.md).

Every persisted selection on an enrolled factory, including public `select`
and execution `check` paths, retains the resolver's original supplied
configuration guard and its current, task-authorization and profile-authorization
checks. Before selection, its exact pinned executor shared by the task, agent
and profile stores must already be bound to the factory's same owner. Inside
the original guard, the association is checked again and active `factory.build`
read access is required before the original grants or business queries. An
association alone is insufficient: the supplied guard must actually admit
compatible owner access. These checks neither enroll resources nor acquire a
configuration capability or guard automatically.

This explicit read right is required even when selection does not construct a
provider. Model equality and provider construction on the bound execution path
remain inside the original configuration guard; transport executes after release.
A configuration grant does not replace the separate task/profile/execution
decisions. Existing unbound behavior and provisional/creation paths are not
migrated by this addition.

## Database-role and global-writer handoff

The deployment owner must identify the actual database/schema, retained
executor/connection/pool/factory identities, transaction and close owners, and
every principal/process allowed to affect the same configuration records.
This includes Python and Go applications, administration, migrations,
provisioning and integration clients. Each supported writer needs explicit
enrollment; every other writer needs an enforceable operational exclusion or a
separately implemented migration. Unknown writers prevent an all-writer claim.

Database permissions constrain principals and database objects. They do not
force a principal already allowed to write through one particular Python
method. Direct SQL through the enrolled executor, raw connections, another
executor identity and other processes remain outside the method checks. A
domain string and an in-process generation cannot supply cross-process fencing,
durable ownership after restart or global publication reconciliation.

The exclusions must remain explicit:

- Profile deletion remains unsupported; its reference locks and failover repair
  are not supplied by this owner.
- Task/conversation initialization, agent role/profile/prompt provisioning,
  quota transitions, other task lifecycle writers and external cache consumers
  are outside the four store hooks.
- Go profile/default/agent-binding, task-chain/quota, conversation-binding and
  global retry-policy writers remain separate processes/interfaces. Declaration
  inventory is not proof of their participation.
- Previously returned providers/pools, mutable policy captures, prompts,
  catalogs and limits keep their original semantic and resource owners.
- Task-create, startup-retention/restoration and raw-traffic review bodies remain
  unassessed under the existing holds. Other scope-excluded operations are not
  globally revoked by this guide.

The original task owner still supplies actual lifecycle currentness, the shared
task writer guard, action/asset/input decisions and resolver policies. The
dedicated DB/profile and prompt/catalog tracks still supply approved records,
content and resources. This component does not manufacture those decisions or
prove that a real task produced output, persisted its goals and settled its
selected runtime. No launcher, bootstrap instructions or permissive callback
example is supplied here.

## Evidence limits

Focused acceptance exercises the production owner and named hooks with bounded
injected resources. It can establish local admission, capability confusion,
generation, nesting, revocation, lifetime and refusal before business queries for those
paths. Synthetic participants and drivers are not real deployment principals or
database-wide enforcement. Exact frozen-source tests and independent reviews
are recorded separately; no future final-tree pass is asserted by this guide.

No real database, provider request, application startup or task operation is
required to validate the bounded local mechanism. Existing inventory-refresh,
publication and held-review dependencies remain separate. This guide introduces
no alternate artifact transfer, deployment action or claim of production
readiness.

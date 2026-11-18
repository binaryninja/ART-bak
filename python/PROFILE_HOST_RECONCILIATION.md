# Explicit profile host and reconciliation

This opt-in path connects committed profile mutation to explicitly supplied host
authority and adds an authorized reconciliation action. It governs calls made
through one participating host. It does not create a task owner, enroll other
writers or provide a real-task launcher.

The starting source is local `2f0352d09159101cbe8d4810bfce2e54ee56f553`, tree
`329c38f3bacb0b80dc665e119a6f31c7aac18c1f`. This guide describes the added host,
service and explicit builder branch; it does not assert remote publication or
successful application startup. The underlying commit/publication distinction
is described in [committed profile mutation](COMMITTED_PROFILE_MUTATION_STATUS.md).

## Two distinct policies

`ProfileHostPolicy` is a frozen, keyword-only object with four required fields:
`revision`, `current`, `authorize` and `guard`. A revision is an explicit bounded
string, not a database commit number or runtime configuration digest. Freezing
the object does not freeze state captured by its callbacks or supply authority.

| Supplied host callback | Required contract |
| --- | --- |
| `current(revision)` | Fresh decision returning literal `True` only when this host policy remains current. No cached approval is substituted. |
| `authorize(operation, value, revision)` | Substantive decision returning literal `True` for the specific `save`, `set_active` or `reconcile` operation. The value is a private profile copy, a positive profile ID or `None`, respectively. Approval must not modify the supplied decision value. |
| `guard(operation, revision)` | The actual owner's non-suppressing shared-writer context manager. It must coordinate the relevant participating writers, support the declared nested/reentry order and remain valid through the operation and publication. It must not open an outer database transaction around the coordinator. |

The separate `ProfileReconciliationService(host, *, authorize)` requires
`authorize(original_request)` to return literal `True`. This is the host's
request-principal decision after the original router's authentication. Neither
JWT authentication nor empty request JSON supplies the host operation policy.
No request field becomes a grant or a policy revision. The request decision does
not replace `ProfileHostPolicy.current`, `authorize` or `guard`.

Hosts must supply these decisions from their actual ownership and access model.
This guide deliberately supplies no permissive callbacks, generated credentials,
policy-file format or substitute lifecycle guard.

## Programmatic surface and resource ownership

`ProfileHost(coordinator, *, policy)` accepts the exact original
`CommittedProfileCoordinator` and exact `ProfileHostPolicy`. The coordinator's
original `ProfileStore`, `ProfileFactory` and `PostgresExecutor` or `ExecutorPool`
must retain their shared executor and pinned bindings. Construction and
`validate_bindings()` check structure and identity; they perform no profile load
and do not prove that the supplied authority is genuine or current.

The host provides `save(profile)`, `set_active(identifier)`, explicit
`reconcile()`, `snapshot()`, `status()` and `validate_bindings()`. Save returns the
profile ID and activation returns `None`; reconciliation returns a
`ProfileHostReceipt(status, snapshot)`. The receipt is a local observation of
publication, not a durable operation record or execution grant. Passive snapshot
and status reads neither authorize a task nor reconcile configuration. The host
does not expose the original coordinator's raw `runtime()` provider pool.

Its nonblocking gate rejects overlapping/reentrant host operations. Currentness
and authorization are checked before and inside the supplied guard; freshness
and bindings are checked again after guard exit. This is cooperative checking,
not bounded database cancellation or an atomic external-send fence. The owner
must define a lock order compatible with the coordinator's owned transactions,
factory work and every participating writer. A database transaction cannot stand
in for the required non-suppressing owner guard.

The host withholds its own publication from admitted operation entry through
failure or successful completion of the guard and final checks. A coordinator
that has applied internally is not by itself host-visible success. Body failures
cannot become success because a supplied guard suppresses them. A body
non-Exception `BaseException` keeps its identity if guard exit raises a distinct
ordinary exception; that exit error is retained as its explicit cause. When the
body and exit errors are both ordinary, or an ordinary body error is followed by
an exit control exception, the exit error remains exposed with the body context.
Outcome evidence belongs to the current delegated attempt; a pre-delegate refusal or busy
coordinator must not borrow a previous or concurrent operation's commit status.

The original resource owner retains database connections/leases, health,
transports and outstanding provider references. This adapter supplies no close,
drain or revocation protocol. It detects specified binding replacement, not all
mutation of callback captures or all direct access to the pinned objects.

## Explicit existing-builder choice

`build_profile_runtime_application` retains its original arguments and adds
`profile_host_policy=None` and `profile_reconcile_authorize=None`. Supply both
together to select the new path. An exact host policy and callable request
authorizer are required; missing/invalid paired inputs are rejected before
profile work. With `None`/`None`, the original `ProfileCoordinator` path remains.

The opt-in branch constructs the original store/factory and a
`CommittedProfileCoordinator` with
`pending_error=server.profiles.ProfileApplyPending`, then the host and service.
It stages the protected route on a deny-only router and performs an initial
guarded `host.reconcile()` before calling the existing persisted-application
builder. This initial call is programmatic; the separate request policy applies
to HTTP requests. It supplies that builder with the same executor, the host as
`profile_coordinator` and `host.snapshot` as `runtime_snapshot`. A successful
protected mount uses the returned original parent router's authorizer.

`RuntimeBundle` retains its first five positional fields and appends optional
`profile_host=None`. On the explicit path, `profile_coordinator` and
`profile_host` refer to the same host. Consumers that need the old coordinator's
raw runtime pool must not treat this as an identical interface.

The profile builder is an operational composition function: it reads stored
profiles, constructs transports under the supplied construction-only contract
and reaches the persisted-application boundary. It is not an inert constructor
or a safe substitute for the outstanding application review. This wave's
acceptance patches that boundary; it does not execute its startup/inventory body.
Exact `WSGIApplication`/`Router` types and callable parent authorization are
structural checks, not proof of original application issuance or full callback
provenance. A later assembly failure does not roll back earlier reads,
construction or caller-owned allocations; no retry or automatic cleanup is
implied.

## POST-only reconciliation

The service registers protected `POST /api/llm/profiles/reconcile`. Original
router authentication rejects an unauthenticated request with 401 before the
explicit request decision, which precedes
body processing and host work. The host then performs its separate policy and
shared-writer checks. An absent host is unavailable, not permission to fall back
to an unguarded coordinator.

The body is absent or at most 256 bytes containing a strict UTF-8 empty JSON
object. Whitespace around the empty object is permitted; fields, other JSON
values, malformed input, query components and fragments are rejected. Oversized
bodies receive 413; other invalid request bodies receive 400. There is no GET
reconciliation route. Refreshing a browser or reading an ordinary profile DTO
does not perform this POST or call `reload()`.

| Reconciliation outcome | Public response |
| --- | --- |
| Request or host operation denied | 403 |
| Host authority stale or operation busy | 409 |
| Missing/replaced host binding or unavailable/invalid publication receipt | 503 |
| Other ordinary host failure | Safe 500 without raw exception details |
| Successful current receipt | 200 with `reconciled: true`, `runtime_applied: true` and the existing whitelisted/redacted public runtime DTO |

The successful receipt must have `operation='reconcile'` and `phase='applied'`,
with no profile ID, `committed=False` and `runtime_applied=True`. The underlying
coordinator's `reload` operation remains internal. The service projects
that returned receipt, rather than reading later mutable host status to infer
the earlier result. Its response has no `persisted` mutation claim. Non-Exception
`BaseException` control outcomes propagate; ordinary request-decision exceptions
deny access. These are handler contracts, not guarantees about every enclosing
server error boundary.

## Pending publication and uncertain mutation are different

For `save` or `set_active`, a confirmed current-attempt mutation commit followed
by an ordinary apply, guard-exit, freshness or host-publication failure remains
persisted but unavailable through the host. The host raises the coordinator's
exact pinned `pending_error`, retaining the original failure as its cause when
mapping it; an existing pending error can pass through. The builder selects
`server.profiles.ProfileApplyPending` so the unchanged `ProfileService` can retain
its existing confirmed-pending response. That response does not promise runtime
activation or authorize a repeat of the mutation.

Precommit refusal, busy operations and uncertain commit outcomes must not be
mislabelled persisted. `committed=None` preserves uncertainty; `committed=False`
is not general proof of physical rollback. A preserved non-Exception
`BaseException` is not translated into an ordinary pending error, even after a
confirmed mutation. Ordinary exit errors that remain exposed under the
precedence rule above follow the confirmed-pending mapping when applicable,
retaining the body error in their context. The host keeps its publication
unavailable on failure rather than exposing an earlier or underlying
applied snapshot as success.

Reconciliation explicitly reloads the currently stored configuration. It never
replays `save`/`set_active`, proves the fate of an earlier ambiguous mutation or
claims that reconciliation itself persisted a mutation. The host owner must
decide when reconciliation is permitted and investigate an uncertain write
through the separately owned database/evidence process. No automatic mutation
retry is supplied.

## Participation and the remaining operator handoff

Participating writes are the profile service/programmatic operations deliberately
routed through this exact host and its supplied shared-writer owner. The default
original coordinator, direct store/SQL calls, another coordinator instance,
other processes, Go/admin/migration writers, conversation bindings and task
profile chains are not automatically enrolled. Existing factory-cache consumers
and retained providers remain outside this host's publication gate. A successful
reconciliation does not synchronize or revoke them.

The DB/profile owner must provide actual original resources, usable schema and
records, connection/transaction ownership, writer identities and the policy for
uncertain writes. The owner must implement the shared guard and revision domain,
including invalidation by every participating writer and a compatible lock
order. The request owner must provide a substantive principal decision and the
original router's authentication. Transport origin/credential/lifetime policy,
fixed prompts, catalogs and provider-probe evidence remain separately supplied.

The provider coordinator is not the agent `BootstrapCoordinator`. None of these
profile APIs supplies the original five task-runtime callbacks or four provider
resolver callbacks, authenticated task ownership or application issuance. See
[operator composition](OPERATOR_RUNTIME_COMPOSITION.md) and the
[owner/writer implementation plan](OWNER_WRITER_IMPLEMENTATION_PLAN.md). Actual
output, persisted goals and selected-task settlement remain distinct real-task
acceptance requirements; no runnable real lab task is claimed here.

## Acceptance boundaries and inventory dependency

Focused acceptance must keep original resources with injected drivers and
synthetic requests, exercise refusal/pending/uncertain outcomes and resource
identity, and prove that GET does not reconcile. Builder acceptance must patch
the persisted-application boundary. Such tests establish local contract and
wiring behavior, not genuine callback authority, all-writer participation,
real-provider operation or successful startup. Exact frozen-source test results
are recorded separately; this guide does not promote fixture observations into
production readiness.

`runtime_setup.py` has a source hash in `ROUTE_PARITY.json`. This source change
therefore requires a later authorized inventory refresh before a freshness gate
can be claimed complete. The held inventory is intentionally not regenerated by
this change. Startup-retention, task-create and raw-traffic review holds remain
unassessed. Other lifecycle operations are outside this acceptance, not globally
revoked. No alternate bootstrap, launcher or publication action is introduced.

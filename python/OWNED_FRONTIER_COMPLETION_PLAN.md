# Tracked frontier completion integration: proposed next interfaces

Design only. Unstamped48 is frozen/PG105 pending; engine/bootstrap/runtime remain
owned by the process lane. No source or positive admission gates change here.

## Smallest new component

New `agent/frontier_completion.py`:

```
FrontierCompletionPolicy(revision, grant, authorize)  # frozen, exact scoped grant
OwnedFrontierCompletion(host, handle, deadline_store, policy, manual_owner)
component.run_once() -> FrontierCompletionResult(phase, receipt=None)
component.cancel() -> bool
```

Constructor is inert and accepts actual AgentHost, BootstrapHandle,
TaskDeadlineStore and same-host ManualActionOwner only. Policy grant is the
original EarlyCompletionGrant bound to the retained prepared bootstrap receipt;
authorize returns that identical permit. It is not inferred from a timeout,
timing policy or source goal state. Pin all resources, original callbacks and
policy revision. No arbitrary completion callback/store implementation is
accepted. Handle must already be installed and prepared; no constructor model,
SQL, stamp, candidate read or automatic registration.

Explicit registry registration and runner binding occur before first invocation.
`run_once` requires the exact host settlement capability plus current exact runner
inventory binding. External direct calls cannot invent a settlement frame or
bypass a sealed manual registry. An accepted call enters manual tracking before
any read and retains it through the outermost finally.

## Candidate classification is read-only and retryable

Add a code-owned bounded candidate reader, not a host-supplied ready boolean:
exact task/prepared bootstrap/configuration and source local frontier under the
original guard and parent-row lock, with scoped read grant before/after reads and
pure scoped permit validation after the final SQL boundary. Full frontier is
bounded before payload transfer; only id/kind/state, no asset/proof data.

- Existing original installed lease in attached state: exact clock/config must
  match SQL, task unpaused/running, DB deadline still future. Select the existing
  `EarlyCompletionStore` path. No inferred or foreign lease.
- No retained/reserved clock and both SQL clock columns NULL: exact prepared
  positive configured timeout selects `UnstampedCompletionStore`.
- Active/open frontier returns `waiting`; no manager terminal reservation and no
  one-shot store consumption. Busy actual handle likewise returns `busy`, without
  calling an active BootstrapHandle completion frame.
- Expired attached clock returns `expiry_required`. It does not freeze, assess,
  settle timeout, invoke a model, or take over an observer.
- Partial NULL clocks, SQL-stamped without original lease, canceled/suspended/
  frozen/uncertain owner or identity mismatch fail closed. They do not silently
  transition to the other completion path.

Candidate output is never write authority. On ready, create the exact code-owned
selected store with the original permit and callbacks and call its one-shot
settle. The selected store repeats authoritative SQL and cap checks. A concurrent
first stamp/expiry/control mutation may invalidate the candidate and leave a
reserved attempt quarantined. No automatic retry or fallback to the other store
is allowed after reservation. Before reservation, a later explicit poll may
observe a legitimately newly attached clock and select that branch.

Source predicate is no open goals and no open/running intents. Empty/met/abandoned
goals and paused-only intents can complete; paused rows are not resumed or
rewritten. This timed candidate does not call engine._snapshot, whose unchanged
zero-timeout path has stronger nonempty/paused checks. Actual active/unresolved
execution still blocks drain regardless of persisted paused rows.

## Runner hook and exact removal

Add explicit `EngineRunner.bind_frontier_completion(handle, component)` and an
internal exact receipt acknowledgement method. No default factory or automatic
selection. Binding validates same original host/manager/executor/token/prepared
receipt and registry registration; no callbacks under runner lock, no rebind or
adoption. A runner inventory map keeps one exact component per handle.

`settle_completed` branches to a bound component before invoking
`handle.settle_completed`; the outer/engine active flags remain untouched. The
old zero-timeout path is unchanged. Missing positive component retains the owner
and cannot fall back to ordinary manager.complete.

The component calls the runner's code-owned acknowledgement only after confirmed
store completion, on the same invocation thread, using its private exact retained
result. Runner compares the original inventory handle and component before
removing either. This method is not a callback parameter accepting arbitrary
success. Component validation happens outside runner lock; the compare/remove
uses pinned identities under lock. The component's tracking receipt remains
active through cancellation tail, runner removal and final bookkeeping. Store
retirement alone is not resource-drain evidence. Removal failure is sticky
uncertainty, never a retry of the SQL/model path.

## Host settlement-slot prerequisite

Current `AgentHost.poll` holds its condition lock while invoking arbitrary runner
settlement callbacks. Before integrating, reserve an internal settlement slot
under the condition, then release the lock for all runner/SQL/policy/handle calls.
Capture an exact private `_SettlementInvocation` (host, runner, thread) for the
component rather than an arbitrary callable claiming a host frame. Clear the slot
and notify in finally. Admission/owned pump/competing poll/drain must consult the
slot; no provider or SQL executes under host/runner/manager/registry locks.

Pause remains a control operation, but must not concurrently retire a task whose
terminal cap is held; existing manager cap checks remain authoritative. If pause
wins before reservation, candidate/settlement stops. A host mutation reservation
must never be allowed to race from 'no mutation' into a live settlement unnoticed.
Host inventory also needs to recognize `early_settling` as pending ownership;
unstamped slots already surface as reserved/quarantined. Neither is clean drain.

## Registry, shutdown and cancellation

Extend ManualActionOwner with exact `register_frontier_completion` and finite
`run_once` operation. No generic registration. Seal prevents new direct/runner
entry, including nested entry; existing entered invocations stay counted.
`cancel_active` calls component.cancel outside registry lock. Cancel sets a local
latch, not the manager token. Before terminal reservation, it prevents work.
After reservation, it revokes the scoped permit and preserves uncertainty; it
cannot roll back a committed task. Permit acknowledgement linearizes revoke
against retirement. Cancellation after acknowledged retirement cannot undo it,
but the active invocation still owns its cleanup/removal tail until return.

A failed retained store cap makes manual inventory unresolved even if a SQL
terminal row exists. Existing listener seals/cancels/waits the same owner and
must not close executor/resources while active or unresolved. No shutdown safety
claim extends to unregistered standalone stores or controllers.

## Exclusive implementation split after approval

1. Architecture: new frontier_completion module + candidate reader (new helper,
   not edits to frozen completion stores) and tests, exact typed policy/result.
2. Host/runner owner: settlement invocation slot, explicit component binding,
   pre-frame branch/removal, pending inventory support, concurrency tests.
3. Registry/listener owner: exact third action type, cancellation/retained
   bookkeeping, actual post-retirement tail shutdown test; no new defaults.
4. Process lane retains engine/bootstrap/task_runtime. Later assembly construction
   can explicitly supply a component policy only after those files are released.
   This integration stage does not require editing their active-frame methods.
5. UI/foundation independent reviews; QA uses real original manager/handle and
   real PG, bounded inert provider only. No fabricated active flag.

Acceptance: both clock branches; empty/abandoned/paused frontier; busy provider
normal wait; first-stamp race; candidate changes before authoritative CAS; final
permit revoke/SQL drift; repeated ready cannot replay; pause/expiry contention;
seal during entry; post-retirement blocked token cancellation/removal keeps pool
open; no callbacks under lifecycle locks; zero behavior unchanged.

Immediate all-met GoalMet completion with active workers still needs its own
completion-driven child cancellation/drain contract. No proof-only strengthening,
forced pause/resume, fabricated expiry or final-assessment inference replaces it.
Natural positive HTTP admission, automatic observation/finalization and production
recovery remain closed/unimplemented in this stage.

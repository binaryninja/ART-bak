> Design record: the narrow tracked-owner composition is implemented as
> described in TRACKED_FINALIZATION_STATUS.md. Public timed admission remains
> disabled; broader recovery and automatic execution are not implemented.

# Tracked manual finalization ownership (proposal only)

No code or gates changed. The existing listener checks `host.drain().all_settled`
before closing resources. That covers retained deadline slots, but is not a join
receipt for the Python invocation that just confirmed settlement: manager ownership
can disappear while the coordinator is still returning/unwinding. A blocked manual
observer can likewise still use the pool independently of the pump.

## Minimal exact registry

New `agent/manual_action_owner.py`, `ManualActionOwner(exact AgentHost)`:

- Constructor inert; pin exact host/manager/runner/repository/executor. No task
  selection, grants, SQL, schema or threads.
- `register_observer(exact OwnedExpiryObserver)` checks original host/resources
  and binds that observer once. `register_finalizer(exact OwnedFinalization)`
  requires its original observer already bound to this same owner. No copied
  generation, arbitrary callback or resource-close function can be registered.
- Bounded retained inventory, unique identity, condition-protected admission latch
  and active invocation entries. Registry lock never spans policy, SQL, model,
  cancellation, finalizer lock, listener lock or callback.
- Private typed `_enter(component, operation)` and `_leave(token, outcome)` called
  by the actual code-owned methods, not generic user-provided execution callbacks.
  Entry captures exact instance/operation/invoking thread; copied/reused tokens
  cannot release another invocation. Shutdown seals before waiting; entry and seal
  share the same lock, eliminating check-then-start races.
- `stop_new_actions()` is irreversible for this owner. `cancel_active()` snapshots
  finalizers under lock, then calls their existing cooperative cancel outside it.
  `wait(timeout)` waits on actual invocation exit, not only a result field or DB
  status. Returns an immutable snapshot distinguishing active versus unresolved.
  No retry, recovery, model action or manager-token cancellation is authorized.

## Necessary connected hooks

`OwnedExpiryObserver` gains a one-time exact owner binding and surrounds public
`tick()` plus `claim_finalization()` invocation boundaries with registry entries.
The handoff's internal tick must not register as independent new work: refactor a
private internal tick body or allow a typed same-thread nested child entry owned
by the existing invocation. Explicitly do not use a reentrant loophole that lets
an arbitrary policy callback start another finalizer after shutdown.

`OwnedFinalization` gains a one-time exact owner binding before any invocation.
Its original observer's bound owner must match, so creating a new untracked
coordinator over a tracked observer cannot bypass the latch. `run_once()` enters
before any readiness/SQL/grant, and leaves only in the outermost finally after
all cleanup. Actual failure after allocation remains unresolved even if Python
invocation stopped; host ownership and registry evidence both remain visible.
`cancel()` stays callable after sealing to stop existing work; it cannot start
work. Binding changes during an invocation fail closed but cannot silently remove
its original registry entry.

Standalone existing components can remain usable in explicit component tests,
but cannot be advertised as owned by the application/listener until registered.
The registry must be claimed by only one runtime application; no automatic adoption
of components that already executed, have active work or belong to another host.

## Application and listener composition

Add an optional exact `manual_actions` field to TaskRuntimeApplication and an
explicit factory option that constructs/binds ONE ManualActionOwner for its already
created host. No second host/manager, no observer inference or default grants.
Expose methods to register explicitly constructed observers/finalizers and inspect
or stop their owned invocations. Do not enumerate ambient tasks or reconstruct
capabilities from task IDs. Factory validates options before startup reads.

RuntimeListener pins this exact optional owner. Stop order:

1. Irreversibly seal manual action admission before arbitrary stop_admissions code.
2. Stop ordinary admission/HTTP and pump through existing paths.
3. Request cancellation of registered active finalizers outside registry locks;
   invoke existing task cancellation policy. No forced interruption promise.
4. Require manual invocations exited within the shared remaining budget AND
   `host.drain(...).all_settled`. An unresolved registry entry is not a clean drain.
5. Only then invoke close_resources and pool.close. Recheck pure original bindings
   and sealed/no-active invariant immediately before closure; no intervening policy
   callback may reopen registry admission. Preserve existing callback uncertainty
   reporting and retained resources on timeout/error.

No waiting/cancellation should hold a lock that a finalizer's finally needs. The
current listener holds its own RLock through stop; registry finalizers must not
need that listener lock to report exit. Listener callbacks remain host-trusted and
can themselves block; returned timeout is not hard interruption of arbitrary code.

## File ownership split

- Architecture: new manual_action_owner.py and dedicated tests; narrow observer
  and finalization binding/invocation hooks after their frozen QA checkpoint.
- UI/application owner: task_runtime_setup.py exact optional owner composition
  plus runtime_listener.py shutdown ordering and dedicated tests.
- Independent reviewer: seal versus start/register races, original resource/token
  identity, nested handoff boundaries and retained postcommit uncertainty.
- QA: exact shared pool, actual finalizer fixture with inert provider, controlled
  threads. No listener socket or external provider required for lifecycle tests.

## Acceptance cases

Block a real restricted final provider in another thread; shutdown must not close
its pool. Seal then reject direct finalizer.run_once and observer.tick even if a
caller bypasses application convenience methods. Cancel/allow invocation to unwind;
uncertain final remains retained. On confirmed settlement, force a barrier after
manager retirement but before run_once finally: host all_settled alone must NOT
permit pool close. Verify registry active count prevents it. Competing registration
or entry during seal loses deterministically. Copied completion tokens and foreign
same-ID host reject. Zero/default app preserves previous behavior. No new model,
observer cadence, positive admission, retries or terminal permission follows from
registration or shutdown.

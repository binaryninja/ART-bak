# Minimal frontier runner hook after isolated controller review

Design only. The controller/manual registry implementation is frozen. The reviewed
host settlement slot and process ownership ledger now exist; do not rewrite them.
No positive task admission or default completion policy is enabled by this plan.

## Existing seams verified

AgentHost.poll now sets a thread-owned `_settling` slot under its condition and
releases that lock across runner callbacks, inventory and waits; finally clears
and notifies. Other polls return busy, same-thread reentry is rejected. Admission,
owned pump work and drain consult this slot. Reuse it rather than introduce a
second ownership flag or call completion while holding the condition.

BootstrapHandle.wait delegates to its actual child after its own activity drains.
TaskEngineHandle.wait/unresolved include PreparedWorkerProcess.drained; the ledger
requires zero active launches and no unresolved process effects. Consequently the
controller and both stores' existing exact handle wait/unresolved checks already
cover process cleanup. Do not substitute token cancellation, canonical transcript
completion, paused rows or a synthetic idle flag for these drain checks.

## Small exact API changes

1. AgentHost adds a read-only code-owned `owns_settlement(runner)` check: exact
   original runner/manager and currently reserved settlement slot on the calling
   thread. It takes/releases the condition only to inspect state; no callbacks.
   This is not a user-supplied current callback. Bind the actual original method
   identity for the new route. Do not change zero-path completion signatures.

2. EngineRunner adds `bind_frontier_completion(handle, component)` accepting only
   exact OwnedFrontierCompletion registered with its same-host ManualActionOwner.
   Require original installed inventory key/handle/token, prepared receipt,
   executor and positive configured timeout; validate outside runner lock, then
   compare/publish under lock. One component per exact generation, no adoption,
   rebinding or new policy calls during binding. Constructor/start stays inert.

3. In `settle_completed`, snapshot handles and bindings under runner lock. For a
   bound timed entry, first verify the actual host's current settlement slot,
   then call component.run_once outside locks and **before** any call to
   BootstrapHandle.settle_completed. Unbound zero entries use the unchanged path.
   An unsupported positive entry must not fall back to ordinary manager.complete.
   Waiting/busy/expiry_required retains the handle and causes no terminal action.

4. For awaiting_runner_removal, validate the component's exact private committed
   result (store's retained result object, consumed private evidence, original
   manager terminal metadata and token) via a new code-owned pure accessor. A
   writable public component.result alone is not retirement authority. Verify
   actual handle.wait(0) and unresolved=False again outside runner lock. Then
   compare-and-remove only the exact original inventory handle + binding under
   lock, with no callbacks. No global `settle_completed` call from the component.

5. Call the existing read-only component.confirm_runner_removal after removal.
   Its ManualActionOwner pending_removal record already covers the interval
   between run_once return and this continuation. If sealed during that interval,
   the exact already-retired continuation remains permitted. A mismatch or failed
   continuation retains pending/uncertain ownership and cannot replay store SQL.
   Pending removal is not mislabeled irreversible quarantine; unresolved effects
   remain distinct. The host settlement slot spans this entire tail as well.

6. Host inventory recognizes stamped `early_settling` as pending. Unstamped
   reservations already expose reserved/quarantined. No retained cap/lease or
   process uncertainty can produce all_settled=True. Preserve the original
   manager retirement acknowledgements for ordinary canceled handles.

## Binding and cancellation policy

This stage exposes only explicit bind_frontier_completion. It does not change
TaskRuntimeAssembly, BootstrapHandle preparation, provider construction or public
create defaults. An application host may bind an explicitly constructed scoped
permit/controller only after the actual prepared receipt exists. Automatic
per-task policy construction can be a later separately reviewed assembly step.
Existing positive gates still make natural positive admission unavailable.

Cancellation during an active controller invocation revokes its actual scoped
permit, not the manager token. Before reservation it prevents writes; after an
attempt it can retain uncertainty. Permit acknowledgement defines whether revoke
wins. Store cancellation of the retired token remains outside permit/manager
locks. Manual tracking remains active through that tail; pending_removal continues
coverage after return. Shutdown sealing never reopens execution or permission.

The source frontier predicate for this explicit timed branch accepts empty,
abandoned/met goals and paused intents; it never resumes paused work. The zero
engine's existing more conservative readiness remains unchanged, so this stage
must not claim universal source parity. Immediate all-met GoalMet with active
workers needs its own cancellation/drain path and remains unsupported.

## Ownership split and acceptance

- Host/runner owner: owns_settlement validation, exact map/bind/branch/remove,
  pending inventory state and concurrency tests. No engine/process files.
- Architecture: tiny controller private-retirement-result accessor plus its
  tests, only after frozen controller source is released; no store changes.
- Registry/lifecycle reviewer: verify existing pending continuation covers
  post-removal interval, seal races and listener pool retention; no new defaults.
- Process owner: read-only confirmation of wait/unresolved ledger contract;
  tests may use its actual inert process fixtures but do not change frozen code.
- Independent review/PG: exact prepared synthetic-positive original owner,
  stamped/unstamped paths, first-stamp race, normal open-frontier waits, paused
  rows unchanged, direct runner call without host slot denied, simultaneous
  pump/poll/pause/drain, busy/uncertain process blocks terminal write, unrelated
  tasks untouched, foreign result cannot remove handle, and blocked cancellation
  or removal continuation cannot close resources. Zero tests unchanged.

No natural positive HTTP admission, automatic observer/finalizer, proof-only
completion, clock fabrication or production recovery claim follows from this hook.

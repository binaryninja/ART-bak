# Exact timed engine branch (design only)

Positive admission is still disabled. This proposal follows the reviewed final
provider adapter; it does not weaken the existing SQL timeout gate in isolation.

## One existing owner, not a second execution authority

Add an internal branch only when the exact TaskEngineHandle has its original
bound PreparedTaskTiming with a positive constructor-owned timeout AND its outer
BootstrapHandle has completed original same-host PreparedTimedLifecycle
registration. The runner passes a code-owned registration reference after the
existing post-prepare seam. The engine must not accept a generic current callback,
a lease or a caller-provided bool as timed-readiness evidence.

Suggested seam: `TaskEngineHandle.bind_timed_execution(registration)` called by
the existing runner after successful `register_prepared`. Registration is the
original retained PreparedTimedActions plus its owning binder, not a copied DTO.
Binder validates original entry/handle/receipt/timing/host/runner/manual registry,
no failed attempt and complete explicitly supplied final plan. Construction only
pins these references and does not stamp/query/observe/finalize. Invocation checks
must not require owns_work while in the separate settlement slot; execution uses
the existing original host work slot, settlement retains its own exact contract.
This seam does not certify future positive admission: schema compatibility and
explicit enabled configuration must be verified separately before removing gates.

## Snapshot and preclaim boundary

Leave the zero path and its existing query unchanged. A separate timed branch
queries the exact task/exploration, running/unpaused/unqueued/live status and the
positive prepared timeout, plus original prepared bootstrap identity/config/run.
Both clock fields NULL is valid before first real planner/worker dispatch. Once
stamped, require the exact retained original kernel/lease clock; partial NULL or
unknown stamped clock is a refusal, never inferred/adopted ownership.

`PreparedTaskTiming.execution_status()` (proposed narrow internal method) checks
original bindings and returns only `unstamped`, `live`, `busy` or `expired`. It delegates
to existing shared kernel observation; it never stamps, allocates a lease or
returns finalization permission. Keep configuration and read authorization checks
at the original boundaries, not a new default grant. No distinct monotonic clock
origins are compared.

Before planner.prepare, worker claim or new canonical turn, an already-expired
status returns `EngineProgress('expiry_wait')`. Do not consume pending events,
create work, call a provider, stamp or set unresolved merely for this normal
state. A naturally unstamped empty/abandoned frontier remains the existing
standalone/host frontier completion path: no fake first_run/deadline is created.
Paused intents stay excluded from source frontier activity, not auto-resumed.

## Expiry racing an admitted call

`TaskBudgetExpired` already carries `task_deadline_expired`. Keep that distinct
from ordinary per-turn `timeout`, cancellation and configuration revocation.
Do not broadly catch DispatchClockUnavailable or arbitrary RunStopped and label
it task expiry. An admitted model context still uses the existing shorten-only
shared clock and HTTP duration budget. No background final model is introduced.

A concurrent kernel stamp (`observe() is None`) returns normal `busy`, not an
unstamped assumption or permission to claim.

If the exact typed expiry arrives after canonical CREATED/worker claim, the
existing InflightCleanupOwner must close only that original turn/job, persist its
terminal usage/activity, and finish the capability. Worker mapping needs an
explicit task_deadline_expired terminal case rather than the current fallthrough
to blocked. Source `server/engine.go:1131-1145` maps successful timeout termination to
`exhausted` (error takes precedence as blocked), not goal truth. The explicit
`task_deadline_expired` case therefore maps to exhausted only for the durable
nonstale terminal outcome, and must be added to BOTH worker.terminal_state and
InflightCleanupOwner.worker_settle canonical-reason allowlists. Real freeze
cancellation follows its separate existing cause mapping; never relabel generic
configuration/DB errors as timeout. A successful model return racing an actual
freeze must remain cancellation-only, never a new done/proof permission.

Only a canonical persisted, nonstale outcome and confirmed exact cleanup finish
permit `expiry_wait`/clean drain. Pending transcript, failed CAS, unknown activity
commit, missing cleanup or process ledger uncertainty still retain/quarantine the
original owner. The current blanket `effects=True` exception handler must not be
replaced with a broad expiry exception escape that hides those effects.

The observer remains independently/manual ticked, including while the pump is
blocked. Monotonic expiry can freeze early, but DB clock not yet expired is a
normal wait. Only existing fresh observer handoff, drained exact ownership and
final journal authority permit final assessment. This engine branch never calls
those APIs or writes terminal task state.

## Exclusive implementation split after approval

- Architecture: engine.py exact typed snapshot/expiry-wait branch; narrow
  task_timing.py status API and original registration reference validation.
- Cleanup worker: worker.py distinct terminal reason mapping/cleanup invariants;
  engine_runner.py post-registration seam only after architecture API agreed.
- Foundation: separate read-only database/schema compatibility proposal.
- Independent reviewer: actual owned preclaim race and admitted-call cleanup,
  no stamp/model on expired preflight, forged registration/copy, paused/empty
  semantics, partial clock, mutable config and process-drain uncertainty.
- QA: actual genuine positive prepared digest created from the start, never
  mutation of a real zero configuration to force a positive test through.

Do not enable positive HTTP/runner/BootstrapHandle gates until that exact
end-to-end fixture proves durable config → lazy first stamp → independent
freeze/drain → restricted final invocation → confirmed settlement, plus early
unstamped/stamped completion and shutdown-held invocation tails. Resume/recovery
and automatic observer cadence remain explicit separate decisions.

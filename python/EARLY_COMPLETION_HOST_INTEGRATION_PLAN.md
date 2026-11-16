# Tracked ordinary frontier completion: next integration proposal

Read-only proposal. The standalone early store is implemented, but no hook or
positive-timeout gate is changed by this document.

## Do not reuse the active completion frame

`AgentHost.poll` calls `EngineRunner.settle_completed`; that invokes
`BootstrapHandle.settle_completed`, which marks the outer active, then marks the
engine active and invokes the manager callback under its lifetime guard.
`EarlyCompletionStore` instead reserves an attached owner and calls actual
`handle.wait(0)`. Invoking it from that callback would wait for its own frame and
quarantine a valid task. The timed route must branch in the runner **before**
calling `handle.settle_completed`. No synthetic active flag or replacement wait
method is permissible.

## Concrete component and interfaces

New `agent/early_completion.py`:

- `OwnedEarlyCompletion(host, exact_handle, exact_EarlyCompletionStore,
  manual_owner)` is inert. It pins the original manager, runner, executor,
  receipt, clock, token, permit and same-host `ManualActionOwner`.
- `run_once()` is private-to-runner via an exact host settlement invocation
  capability, not callable through arbitrary boolean callbacks. It first enters
  a tracked invocation. A bounded read-only candidate check under the original
  guard checks attached/unexpired task, exact bootstrap and source frontier.
  Active frontier/unexpired ownership not yet ready returns `waiting`, without
  reserving a cap or consuming the one-shot store. Expired returns
  `expiry_required`, never performs final assessment. No SQL mutation/provider.
- Candidate readiness is advisory only. Actual `store.settle()` repeats every
  authoritative check and atomically reserves the manager capability before
  terminal writes. A race after candidate read may conservatively quarantine;
  no retry or automatic execution restoration is claimed.
- Tracking spans candidate check, store acknowledgement, cancellation tail,
  exact runner removal and final binding checks. The outermost finally releases
  it. Uncertainty with a retained reservation marks manual inventory unresolved.

Extend `ManualActionOwner` with exact `register_early_completion(component)` and
its finite operation `run_once`. No generic component/callback registration.
Tracked methods cannot bypass seal. `cancel_active` latches cancellation on the
component without cancelling the manager token: before reservation it prevents
writes; after reservation it refuses acknowledgement/retains uncertainty. This
must be an explicit state machine, not a promise to interrupt blocked SQL.

Extend `EngineRunner` with `bind_early_completion(handle, exact_component)`:
exact installed inventory handle/token/manager/executor and registered same-host
owner required; no rebinding/adoption or callbacks under runner lock. The runner
settlement pass snapshots entries, and routes a bound timed entry to its component
outside runner lock, without entering outer/child active frames. Literal confirmed
receipt permits exact compare-and-remove, included in the tracked invocation.
A missing component leaves attached positive ownership retained; it cannot fall
back to manager.complete. Zero-timeout entries retain their existing path.

The original permit is scoped to the prepared receipt; assembly needs an explicit
host-supplied typed policy that creates/authorizes that exact permit after prepare.
A stored timeout, goal text or timing opt-in is not permission to issue it. Do not
use a generic factory returning arbitrary terminal-success callbacks. Validate
exact code-owned component/store/permit on binding. Construction must not query,
run policy, reserve, stamp or start a thread.

## Host serialization and lock order

Host settlement needs an exact code-owned reservation held across the call but
not its condition lock. Today `poll` holds `_condition` through runner callbacks;
introduce an internal settlement slot that checks mutations/working/drain,
sets `_settling`, releases the lock, invokes runner, then clears/notifies in
finally. Admissions and competing poll/drain must respect that slot. No manager,
runner, host or manual registry lock spans SQL/policy callbacks or waits.

Permit acknowledgement remains permit-lock -> manager-lock. Manager never
acquires a permit lock under its registry lock. Cancellation remains outside both.
Manual tracking is registered/entered before retirement, so a post-retirement
blocked cancellation tail still prevents listener/resource closure.
Host inventory must recognize `early_settling` as retained pending ownership,
not reject it as unknown or count it as a clean suspended lease.

## Source semantics and unchanged gates

The timed candidate and store use the source ordinary predicate: no open goals;
no open/running intents. Empty or abandoned goals and paused intents are accepted.
This means execution is finished (`reason=frontier_drained`), not that all goals
were achieved. Do not reuse engine `_snapshot`'s nonempty/manual-mode and paused
intent restrictions for this timed branch. Existing zero behavior stays unchanged.
The engine positive SQL guard, runner/BootstrapHandle positive admission gates,
and automatic observer/finalizer wiring remain closed throughout this stage.

Immediate Go `GoalMet` completion with active workers is separate: it needs a
completion-driven child cancellation reason, actual drain/cleanup and compatible
retirement, without cancelling the manager token before settlement. No proof-only
strengthening, fake expiry or final-model call substitutes for that missing path.

## Ownership and acceptance split

1. Architecture: new tracked component, bounded candidate method on early store,
   dedicated tests. Preserve permit review cases.
2. Host/runner owner: exact settlement reservation and typed component binding;
   tests for concurrent poll/pump/pause/drain and no callback under locks.
3. Registry/listener owner: exact third component type, seal/cancel/retained
   inventory, post-retirement tail closure test. No new listener defaults.
4. Independent reviewer + QA: actual original manager/outer/engine ownership,
   no fabricated active flags, source empty/abandoned/paused cases, busy provider
   cannot settle, stale candidate quarantines without replay, grant revoke and
   commit uncertainty retain, concurrent seal blocks new call, executor cannot
   close during tail. PG must verify actual task+receipt atomicity and original
   deadline/config preserved. Positive fixture pins remain explicitly synthetic;
   this does not establish natural timed HTTP execution.

## Required no-lease branch: prepared but never dispatched

This is a separate missing acceptance case, not covered by the current early
store or its PG tests. Source `server/engine.go:845-857` performs the
HasOpenGoal/HasActiveIntent ordinary-done check before `stampFirstRun` at line
863. Worker stamping is likewise at its actual execution boundary (line 984).
Consequently a positive-timeout task may finish with both `first_run_at` and
`deadline_at` NULL after goals initialization, with no planner/worker inference.
The goals call itself must not stamp the execution clock. An empty/no-open
frontier cannot be forced through a dummy planning call to obtain a lease.

The proposed runner hook must classify an exact initialized owner into three
explicit paths before any planner/worker advance:

1. Prepared, no registered/reserved clock, authoritative SQL timestamps both
   NULL: **unstamped completion candidate** below.
2. Original attached exact lease: existing EarlyCompletionStore path.
3. Stamped SQL without original registered lease, partial NULL timestamps,
   suspended/frozen/uncertain owner, or foreign generation: refuse/reconcile.
   Never infer a lease from SQL or manufacture a clock to complete the task.

### Separate capability and persistence, not a fake DeadlineLease

Proposed new `UnstampedCompletionStore` and a distinct manager reservation:

- Exact original installed BootstrapHandle/token, retained prepared bootstrap
  Receipt, positive constructor-bound timeout, immutable config and typed permit.
  No arbitrary EngineScope or task-ID-only callback establishes this authority.
- Manager owns a separate unstamped terminal-reservation slot, not a fabricated
  `_DeadlineSlot`. Reservation atomically requires no deadline reservation/lease,
  no cleanup, running/unpaused/unqueued current generation, and no uncertainty.
  It blocks `owns_generation`, clock reservation, admissions and ordinary
  completion until confirmed retirement or explicit reconciliation. Clock
  registration checks the same slot, so completion and first stamp race with
  exactly one winner. No store callback executes under manager lock.
- After reservation actual handle.wait(0) and unresolved=False are required.
  No waiting on the active BootstrapHandle/engine completion frame. Host
  settlement serializes pump/admission; the store itself still checks drain.
- Under original lifetime guard, lock task then prepared bootstrap. Require
  exact config/timeout and both SQL clock columns NULL. Read bounded local
  goal/intent frontier with the same source predicate. A clock already stamped
  or a competing reservation rejects; never clear or overwrite it.
- A separate explicit fresh receipt table (proposed
  `artex_unstamped_completion`) records task/exp/run/bootstrap revision/config,
  positive timeout, graph digest, `clock_state='unstamped'`,
  `reason='frontier_drained'`, status done and completed_at. It does not contain
  invented first/deadline values. Do not alter the already reviewed stamped
  receipt schema to admit null clocks as if they were leases.
- Task CAS includes `first_run_at IS NULL AND deadline_at IS NULL` and the exact
  running lifecycle/timeout; task done and receipt share one transaction.
  Complete postcallback verification includes the unchanged NULL clock state.
  Use the same scoped irreversible permit acknowledgement protocol, pure final
  validation, private evidence and conservative postcommit quarantine.
- Manager retires the exact unstamped reservation/run after confirmed receipt.
  The tracked invocation remains active through cancellation and exact runner
  removal. Neither absence of a lease nor successful manager retirement means
  application resources may close before the invocation tail returns.

This requires dedicated manager slot/inventory and stamp-exclusion integration,
not merely a new callback around manager.complete (which currently performs SQL
under its registry lock and does not enforce this prepared receipt contract).
Implement/review this branch before claiming the whole ordinary timed completion
workflow. It is currently a blocker, not functionality supplied by the stamped
store. Public positive admission gates remain closed until both branches and
expiry ownership are connected and accepted.

### Paused intents and acceptance cases

Both branches ignore persisted paused intents for the source frontier predicate.
They do not resume, claim, reset, stop or otherwise mutate those intents. Only
actual active/unresolved execution blocks physical drain; a paused persisted row
with no owned running work is not itself a running worker.

Required actual-PG tests include goals preparation with empty goals, all abandoned
or met goals, and paused-only intents: done with frontier_drained, original clock
columns NULL, no lease, no additional model call. Also race first real dispatch
against unstamped reservation: either the original clock wins and unstamped
completion refuses, or completion fences dispatch before transport/stamp; never
both. Partial NULL clock and persisted-stamped-without-lease reject. Source empty
bootstrap acceptance remains separately constrained by GoalPlanner's existing
initialization contract; fixture-only empty graph tests must not claim that
natural HTTP goals initialization already supports every source empty-goal case.

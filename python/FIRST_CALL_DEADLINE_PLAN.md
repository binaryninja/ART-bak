# First-call clock: source boundary and minimum safe bridge

Read-only design. No timeout option, hook, timer or workload is enabled here.

## Important source distinction

`server/engine_timeout.go:stampFirstRun` is a no-op for timeout_seconds <= 0.
`server/engine.go` calls it immediately before a real planner operation (~863)
or worker operation (~984), after operation admission/readiness. The persisted
clock uses transaction now() and is stamped once; an existing deadline is never
moved. `server/goals.go:createGoals` performs goal decomposition during bootstrap
but calls BeginLLMCall/EndLLMCall, **not stampFirstRun**. BeginLLMCall is occupancy
accounting, not a clock hook.

Therefore exact current Go parity starts the task execution budget with the first
planner/worker run, NOT the first goal-decomposition provider call. Charging goals
initialization against the same budget is a deliberate policy change, not a bug
fix or already-established source behavior. Keep this choice explicit; do not
silently count chat/side questions, readiness or replay as execution. The prepared
journal subset can eventually support source-parity planner/worker stamping; an
initializing receipt capability is needed only if an explicitly selected policy
also times bootstrap or a separate initialization budget is introduced.

## Why the prepared-only helper is insufficient by itself

`TaskDeadlineStore` now verifies exact retained prepared Receipt, full journal CAS
identity, installed manager token/outer handle and the shared guard. It is a safe
standalone persistence primitive, not a first-call hook or deadline enforcement.
There is no owned clock context passed into bounded planner/worker calls, no final
settlement coordinator, no source-faithful paused deadline owner, and nonzero
timeouts remain rejected by runner/handles. Running bootstrap does not yet retain
its journal receipt on the handle, so passing an arbitrary Receipt or changing a
phase string cannot authorize initialization clock writes.

## Explicit first-dispatch hook

Use an explicit task-scoped provider-boundary adapter, not runner.ready, provider
resolver, constructor, transcript begin, capture callback or engine tick time.
These can run without a transport call, run multiple times, or occur on replay.

At the final authorized model boundary: permission/request validation → fresh
provider/profile binding → exact turn and installed-generation checks → guarded
clock stamp/read → repeated turn/profile/token checks → actual provider call.
For streaming, invoke immediately before consuming the transport's first iterator
step, not while constructing a lazy generator. Compaction's actual summary call
needs the same scoped hook if its planner/worker run is clock-eligible. Explicitly
exclude unrelated conversations/side questions and never infer role from a string
supplied by a model. Internal transport retries reuse the same absolute clock;
retry count must not reset or extend it.

A SQL transaction and network send cannot be atomic. A successful stamp followed
by cancellation or transport failure still consumes wall-clock budget. Document
this as first *authorized dispatch admission*, not proof bytes reached a vendor.
Any uncertain stamp blocks transport and quarantines the owner; never start a
provider while assuming clock persistence succeeded. Even a known pre-transport
error does not automatically clear a previously committed timestamp.

## Narrow initializing-owned capability (only for explicit bootstrap timing)

1. After journal claim commits, before constructing/running GoalPlanner, the
   coordinator delivers the exact running Receipt to its actual installed
   BootstrapHandle through an internal one-shot claim checkpoint. Both objects
   must match manager/token/task/exploration/config/executor and shared guard.
2. Handle retains that Receipt privately only for its active initializing phase
   and owner thread. Add a separate `validate_initializing_receipt` capability,
   not a widening of `validate_prepared_receipt` and not arbitrary callback truth.
   It requires exact original configuration, receipt identity, manager installed
   execution ownership, no cancellation/unresolved state and current call phase.
3. The provider-boundary hook receives an internal capability with no external
   DTO/owner-token exposure. It cannot be invoked during factory construction,
   after GoalPlanner returns, from another thread, or by a new generation.
4. A separate stamp_initializing operation locks task then journal; requires
   running task and exact journal phase running, owner/run/request/config/revision;
   verifies the capability before/after SQL and commit. Prepared stamping keeps
   its existing separate interface and checks. No free-form accepted-phase arg.
5. Revoke the dispatch capability immediately after model execution admission
   finishes and always on exit. Retain only durable receipt state for uncertainty
   reconciliation. No automatic takeover/retry after cancellation or restart.

The checkpoint must not permit an early engine child or convert initialization
failure into a successful prepared receipt. Source-parity mode does not install
this bootstrap clock hook at all.

## Absolute budget and pause behavior

Source `deadlineCoordinator` counts absolute wall-clock time through pauses;
resuming does not restart the deadline. `agent/taskclock.go` clamps each run to the
minimum of its own budget and the remaining task budget, with an explicit expired
case (Go floors to one second because zero means unlimited). Python must never
pass zero as an accidentally unlimited remaining budget. Validate timezone-aware
DB timestamps; derive bounded local monotonic waits from a fresh wall-clock
comparison without treating wall-clock timestamps as monotonic values.

Before enabling timeouts, implement an explicitly owned deadline coordinator:
stop new work at expiry, cooperate with child budgets, drain with bounded grace,
separately cancel child execution if needed, run an authorized final planner mode
that cannot spawn intents, then atomically settle done only from persisted goal
truth, otherwise timeout. Do not cancel the manager token and then expect its
ordinary `complete` method to accept settlement. Paused-task timeout settlement
requires a reviewed durable same-generation settlement authority distinct from
execution permission; current prepared helper intentionally does not provide it.
No arbitrary generic current callback can substitute for that authority.

## Proposed fleet sequence / proof

- Provider/runtime owner: explicit dispatch-hook API in complete+stream+compaction
  paths, permission/rebind/cancel ordering, no hook on replay or denied calls.
- Bootstrap owner: optional claim checkpoint and phase/thread-bound initializing
  capability, only under an explicit budget policy; no status enabling.
- Persistence owner: separate running-receipt stamp API plus existing prepared
  path, exact full identity and postcommit uncertainty handling.
- Engine/manager owner: clock propagation and actual owned expiry/drain/final
  settlement path, including paused ownership. Only then remove timeout rejection.
- QA: temporary PostgreSQL stamp-once under competing calls, paused elapsed time,
  changed config/generation/receipt, commit uncertainty, lazy streaming, cancelled
  after stamp before transport, no-call replay/denial, compaction first dispatch,
  final planner no-new-work and no timeout overwriting done.

Until all enforcement/settlement pieces exist, do not market a stamped deadline
as an enforced timeout or enable nonzero timeout task creation.

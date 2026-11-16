# Connected positive-duration runtime plan (not enabled)

## Source clock boundary

`server/engine_timeout.go:stampFirstRun` is called at the actual planner/worker
run boundaries in `server/engine.go` (planning around line 863; worker execution
around line 984), after readiness/admission. It is NOT called for goal bootstrap.
Timeout zero is a no-op. The first stamp persists an absolute deadline; pauses do
not stop or reset that clock. The source coordinator observes expiry separately
from worker/planner loops, including while they block. Source final assessment
uses an independent context and chooses task `done` versus `timeout`, preserving
an existing terminal state.

Python deliberately differs on ambiguous stamping: retain/quarantine instead of
blind retry. Positive constructors and the engine SQL predicate currently reject
all real positive-duration tasks. Synthetic clock tests do not change this.

## What is already connected, and what is not

Available reviewed components include owned first-call persistence and readonly
clock validation, PreparedDispatchClock, manager lease/freeze/drain/assessment
capabilities, restricted final ChatAgent, and exact terminal settlement. None
currently installs a provider dispatch clamp or an automatic expiry observer in
TaskRuntimeAssembly.

Three concrete blockers precede opening any admission gate:

1. Each PreparedDispatchClock has its own first-stamp state. Creating separate
   planner/worker helpers against one owner can attempt a second reservation.
   Introduce one generation-owned clock coordinator, not independent stampers.
2. RuntimeContext uses a run-relative deadline. Returned DispatchBudget is not
   currently applied to that SAME context/clock before provider transport and
   subsequent tool checks. Calling the helper without clamping is insufficient.
3. Freeze currently denies ordinary `owns_generation` immediately. Assembly uses
   this same current predicate for worker settlement, planner persistence and
   activity projection. Expiry during a real call therefore risks unresolved
   persistence, even when effects are known. A restricted cleanup authority is
   needed before wiring automatic freeze. Broad `allow_cancelled=True` is not
   enough and must never authorize new model/tool work.

## Proposed implementation sequence and exclusive ownership

### A. Exact in-flight cleanup capability (architecture/core owner)

Files: `manager.py`, `agent/engine.py`, `agent/bootstrap_handle.py`, dedicated
cleanup tests. Add a capability for an ALREADY installed and already admitted
planner turn or worker claim, frozen with its exact scope/transcript/claim version
and expected permitted cleanup actions. Execution authority remains denied.

- Permit only that turn's canonical terminal transcript commit, exact worker
  running-version state transition, and receipt-idempotent terminal activity
  projection. No new transcript begin, provider, tool, intent claim or graph proof.
- Capture the permitted operation BEFORE freeze; do not mint it from an arbitrary
  post-freeze callback or a read lease. Require original config/owner/resources.
- Add an engine child-cancellation path independent of the manager token; source
  timeout drain cancellation must not be mislabeled as `task_pause` (which reopens
  the intent) or `planner_kill` without examining the source terminal classifier.
  Define/test an explicit timeout-drain classification; refuse unknown mapping.
- Handle known cancellation/expiry as a drained result, not a generic engine
  exception that permanently marks the parent unresolved. Unknown provider/tool
  or SQL effects still quarantine. No release just because a timer elapsed.

### B. Shared owned dispatch clock (clock/provider owner)

Files: `db/task_deadline.py`, `agent/deadline.py`, narrow provider-dispatch adapter
and tests. One exact generation owns stamp-once synchronization, persisted lease,
absolute timestamps and non-expanding monotonic ceiling. Role-specific checks
still bind planner/worker model and scope; goals/final/chat are excluded.

At the final authorized actual complete/stream boundary:

1. Resolve and grant exact provider/config/request and generation.
2. Stamp only if this generation has no retained clock; other callers validate
   the installed clock, not reserve another owner. Concurrent first calls must
   either share the one confirmed receipt or fail closed, never double stamp.
3. Recheck profile/config/cancel authority, clamp the existing context deadline to
   `min(existing_run_deadline, task_monotonic_deadline)` using the SAME monotonic
   clock. Validate remaining positive time after callback latency.
4. Pass this context through the actual provider/stream and tool boundaries.
   Streaming chunks must observe expiry too. A cached bind/readiness check cannot
   stamp. Stamping proves dispatch admission, not that bytes reached a provider.

### C. Owned expiry observer and phase coordinator (host/lifecycle owner)

Files: new `agent/timed_coordinator.py`, narrow `host.py`/`pump.py` integration and
tests. Constructor inert; explicit start/tick/stop. It must run independently of a
blocked synchronous engine-pump callback, using one bounded owned observer thread
or an explicitly managed external tick. No detached/untracked threads.

Phases: unstamped → running clock → freezing → draining → final assessment →
settling → confirmed terminal, with unresolved quarantine reachable at every
uncertain boundary. Exact capability transitions, not elapsed time alone, advance
phases. Freeze stops claims first; cleanup capability A permits known outstanding
writes. Bounded grace can request cooperative child cancellation, never falsely
claim a blocking provider ceased. Final assessment cannot overlap undrained work.

After clean drain, construct the actual separate journal + restricted final
runner with explicit independent provider/read/persistence grants and finite
budget. Then invoke exact final settlement using fresh local graph truth. No
callback may replay a pending model round or restart a quarantined generation.
Retained ownership prevents listener/resource shutdown until genuinely drained.

### D. Explicit opt-in assembly and admission (assembly owner)

Files: `agent/task_runtime.py`, `agent/engine_runner.py`, `agent/bootstrap_setup.py`,
`server/task_runtime_setup.py` and constructor/admission tests. Only after A–C are
reviewed, add a code-owned timed configuration object (all resources/policies
explicit) to the existing ONE-manager assembly. Keep default timeout zero.

Remove positive rejection only for that exact fully configured path; replace
`timeout_seconds=0` SQL checks with exact prepared timeout/clock capability checks,
not an unconditional deletion of safeguards. Carry positive timeout immutably
through provisional create, actual TaskStore row, bootstrap configuration digest,
prepared handle, engine scope and lease. Goals initialization still does not
stamp. New task status/start behavior remains source-shaped; no hidden work in
ready()/constructors and no second manager or synthetic durable run IDs.

### E. Independent review and actual PostgreSQL acceptance (review/QA owners)

No real providers, targets or user data: authored transport responses with controlled
blocking, actual fresh PostgreSQL, canonical temporary transcripts and the same
opt-in app. Assert create → goals bootstrap with NULL first_run/deadline → first
planner dispatch one stamp → worker shares clock → expiry freeze/clean drain →
restricted final receipt → exact terminal receipt and manager/runner retirement.

Also test expiry before transport, during streaming/tool callbacks, callback
latency, simultaneous first calls, backwards clocks, SQL commit ambiguity,
revocation during cleanup/final settlement, preserved existing terminal status,
provider ignoring cancellation, and shutdown while final assessment blocks.

## Explicit unsupported cases until separately implemented

- **Pause/resume:** wall clock continues. A suspended lease can be inspected and
  frozen after expiry, but resuming into a new execution generation has no lease
  transfer protocol. Initial timed admission should reject resume rather than
  reset budget. Pausing before first call should not stamp; pausing an active call
  needs A's exact cleanup. Do not silently make pauses an unlimited task extension.
- **Blocked provider:** cooperative cancellation cannot force thread termination.
  Grace expiry leaves unresolved retained work; do not run final assessment or
  close its database/provider resources under that work.
- **Restart/revival:** source supports more reload/revival cases. Current Python
  pending journal/lease adoption is intentionally unsupported, including after
  process restart and timeout→running revival. No automatic inference retries.
- **Final proof/inherited/goalless:** restricted final runner only reads local
  goals. It cannot establish missing proof. Settlement accepts only confirmed
  local proof and rejects inherited/goalless/unknown graphs. Label this narrower
  supported subset; do not claim full source final-planner parity.
- **Synchronous final run:** its separate budget is finite and cooperative, not a
  hard kill. Cancellation/persistence uncertainty remains visible and retained.

No gates or runtime implementation were changed by this planning pass. The next
coherent implementation is A, then B/C in parallel with stable shared contracts;
D must wait for their connected tests rather than merely the existence of helpers.

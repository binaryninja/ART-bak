# Timed tasks: minimum connected phase coordinator

Design only. Do not remove current nonzero-timeout rejection yet. Bootstrap
initialization remains outside the task clock, as in the inspected Go source.

## Existing usable pieces versus blockers

- BootstrapHandle already owns the exact installed manager token and actual
  engine child; journal prepared receipt identifies initialized graph/config.
- TaskDeadlineStore can stamp/validate that receipt; PreparedDispatchClock can
  calculate a bounded dispatch budget. Neither is installed or enforces it.
- RuntimeContext checks its own monotonic deadline cooperatively. BoundedAgent
  settlement currently handles per-run exhaustion, not task expiry/final rounds.
- Engine.advance selects planner/worker work; its completion path freshly checks
  graph truth. It has no settling/freeze phase or independently owned deadline.
- Manager.pause cancels and removes the execution run after drain. Runner retires
  the cancelled handle. This loses a clock owner needed to expire paused tasks.
- EnginePump is synchronous: a blocking child callback prevents the next pump
  tick. A timer implemented only as another ordinary tick cannot enforce expiry
  while that callback blocks. No Python wrapper can forcibly stop arbitrary code.

## Explicit phase machine on the existing outer owner

Use a real DeadlineCoordinator bound to the prepared receipt and outer handle,
not an AgentRunner pretending a single turn finishing means task completion.

`unstamped → executing → freezing → draining → final_assessment → committing → terminal`

`unresolved` is absorbing until explicit reconciliation. `paused` is execution
suspension orthogonal to this clock state; time keeps passing. Normal fresh-goal
completion can win before committing and must never be overwritten by timeout.

### 1. Stamp and enforce at real dispatch

Install an explicit clock hook through PlannerCycle/IntentWorker → ChatAgent →
BoundedAgent/RuntimeContext. Eligibility is host-bound planner/worker only, with
matching prepared receipt/model/policy; GoalPlanner/bootstrap, ordinary chat and
side questions get no clock hook. Retain one clock object per task generation,
not per turn, so local monotonic ceilings survive subsequent dispatches.

At the final authorized provider boundary, before transport, call the reviewed
helper. Apply its monotonic ceiling by a new explicit RuntimeContext clamp method
that only decreases deadline and retains the *task-expired* cause separately from
per-run timeout. Check immediately after clamp and before/after each provider,
stream iteration, tool, approval and compaction callback. Lazy stream and actual
compaction provider calls need the same hook. No hook in model resolver/readiness,
constructor/capture/transcript begin or reused completed turn. Tools receive the
same live context, not a copied map that drops its deadline check.

Own-run timeout before the task deadline remains ordinary per-run wrap-up. Task
expiry signals the coordinator; it must not automatically enter a general
settlement mode that can spawn more work. Provider/tool adapters retain their own
bounded IO timeouts and cancellation requirements. If they do not drain, keep the
owner unresolved and do not declare terminal completion.

### 2. Freeze admission before selecting another intent

Add a lifetime-guarded task phase capability consulted by TaskEngineHandle before
snapshot, planner wake, worker claim, and all generation-bound graph/task mutation
adapters. A deadline observation atomically moves executing → freezing. Stop new
worker claims and ordinary planner notifications; retain/drop their bookkeeping
explicitly, never act on them after final assessment. Foreign HTTP/task-chat and
scheduled admissions must reject frozen tasks through the same phase authority.
A prompt saying "no new intents" is not this fence.

Expose a narrow host operation for freeze/cancel observation that is permitted
while `_working` is active, like pause responsiveness, but cannot invoke a model,
SQL terminal CAS or admit another run. It takes the actual registered deadline
owner, not a caller-supplied task ID + boolean authorization.

### 3. Drain without destroying terminal authority

Execution token and deadline settlement owner must be distinct. Deadline expiry
first lets the clamped child finish durable observations. A finite grace can
cancel **child execution** with a deadline-specific cause without cancelling the
manager's terminal-settlement authority. Add a distinct worker cause/state mapping
(source interruption rules), preserving done/exhausted/blocked semantics; do not
reuse task_pause/planner_kill silently. Any SQL/transcript ambiguity blocks final
assessment and retains ownership. `wait=True` alone does not mean goals met.

A cooperative watchdog must be explicitly owned and start/stop/drain with the
host, or an independently scheduled trusted host must call its observation API.
It cannot be an untracked daemon. It only freezes/cancels; final model/DB work runs
through the existing serialized host work reservation once prior work drains.
A noncooperative callback makes hard enforcement unavailable, not successful.

### 4. Bounded final assessment with structural capability removal

Add a separately identified PlannerCycle final operation (or dedicated
FinalAssessment class) with a durable once-only receipt and explicit final-mode
policy/model/config digest. Do not reuse a normal wake/client ID or merely append
a prompt. Final tool registry omits add_intent, scope/constraint changes, worker
launch, process/filesystem/network/MCP calls and other operations. Only explicitly
authorized local graph reads and evidence-linked goal proof may be exposed. It
uses an independent finite final budget, never the already-expired task deadline
or zero-as-unlimited. No automatic retry of a pending final transcript receipt.

Source waits for a usable model; a bounded implementation must document its
maximum wait and outcome. Clean provider unavailability/refusal can proceed to
fresh graph assessment under explicit policy; uncertain effects must quarantine,
not be disguised as a completed assessment.

### 5. Terminal CAS from fresh truth

Under the same lifetime barrier and durable full journal/task identity, confirm
all owned callbacks/children are drained and no unresolved receipts remain.
Re-read authoritative goals/lineage, not cached PlannerResult or model prose.
Persist done only when actual goal conditions hold; otherwise timeout. Preserve
an existing done/failed/timeout result and exact generation/config. Use a narrow
manager deadline-settlement API bound to the registered deadline owner; ordinary
manager.complete rejects cancelled tokens and cannot settle a retired paused run.
Commit uncertainty leaves the owner retained. Release capacity/watcher only after
confirmed terminal state, never just after final model completion.

## Pause/resume: the enabling blocker, not a footnote

Introduce a manager-owned DeadlineLease registry separate from `_runs` and from
runner active/pending capacity inventory. A drained paused task frees execution
capacity but retains its immutable prepared receipt, absolute clock and one
single-host deadline owner. Pause revokes only execution. The lease has no normal
model/tool permission; it permits exact expiry checks and an explicitly authorized
bounded final assessment after drain. This follows source's pause-independent
final context, which must be clearly documented to callers.

Resume before expiry must explicitly transfer execution ownership to a fresh
manager token under this retained same-host lease, unchanged configuration and
exact durable receipt. This requires a new reviewed adoption path; current
bootstrap code rightly refuses prepared receipt adoption into a new token.
Do not enable resume by weakening that check globally. Old execution tokens are
revoked; the stable lease is still unique and its new execution attachment is
atomic. Resume after freeze rejects. Profile/policy changes or orphaned leases
require explicit reconciliation, not generic callback approval.

This minimal route remains single-host/no crash takeover. Persisting/recovering
cross-process execution epochs is separate work; absent it, restart timed-task
adoption must fail closed with a visible blocker rather than reset deadlines.

## Implementation split and acceptance gate

1. Runtime owner: explicit dispatch hook and deadline-clamp API; causes,
   streaming/compaction/tool checks, no bootstrap timing, no new permissions.
2. Engine owner: phase/freeze coordinator + child cancellation/drain + structural
   final assessment capability, exact receipt identities, no default startup.
3. Manager/host owner: DeadlineLease register/validate/transfer/settle, capacity
   independent paused watcher inventory, explicit watchdog ownership and shutdown.
4. Persistence owner: exact terminal journal/task CAS and once-only final receipt;
   no timeout overwriting done, no retry after uncertain commits.
5. Integration owner: explicit timed hosted configuration. Remove timeout
   rejections only after the whole path passes independent and temporary-PG QA.

Acceptance tests must cover: no stamp during bootstrap/readiness/denial/replay;
first planner OR worker stamp once; initial provider error retains clock; later
calls/read-only cache use same absolute deadline; callback delays consume budget;
pause beyond expiry still settles under lease without occupying active capacity;
resume before expiry preserves deadline and rejects old token; expiry during
provider/tool/stream/approval; freeze races with worker claim/task chat; grace
expiration without fictitious drain; final-mode tool set cannot launch work;
final refusal/unavailability and ambiguous receipt; done-vs-timeout CAS race;
commit uncertainty retains owner; restart with orphan lease refuses adoption;
clock jumps and own-run timeout cause separation; no hidden threads in constructors.

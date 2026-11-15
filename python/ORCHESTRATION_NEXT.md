# Historical design: task-generation engine

The initialized-task bridge described here is now implemented and independently
reviewed. See ENGINE_HOST_STATUS.md and ISOLATED_QA.md for current scope/evidence.
The source analysis and remaining bootstrap/recovery constraints below are retained.

Read-only source audit, 2026-11-15. This is an implementation plan, not a claim
that autonomous task execution is already connected. No model, target, database
or upstream application was executed for this audit.

## What actually connects today

* `TaskManager` accepts a runner protocol, persists admission, creates one exact
  cancellation token, and retains its handle until drain/settlement.
* `AgentRunner` runs **one BoundedAgent conversation**. Its completed result maps
  to task `done`; this is not an exploration engine or a planner outcome policy.
* `AgentHost` fences lifecycle mutation against settlement, but currently checks
  for the concrete `AgentRunner` type. Its handle inventory/drain/poll are useful
  and should be retained, not independently reimplemented in HTTP.
* `PlannerCycle` creates real graph intents and durable scoped transcript turns.
  `IntentWorker` claims a real intent/version, consumes tools, persists its turn,
  and CAS-settles the intent. Neither is called by AgentRunner or AgentHost.
* `GraphTools` has local record_fact/list_facts/list_goals/prove_goal. Worker
  registry assembly can explicitly supply it. PlannerCycle currently creates
  its own registry with **only add_intent**; a host cannot yet inject prove_goal
  or the other reviewed native tools into that registry.
* Task-main `TaskChatCore`/`TaskChatDispatcher` and source-shaped
  `server/task_chat.py` are a separate human-turn path. A chat 202 receipt must
  never imply that planner/worker execution resumed. The application factory
  currently does not register TaskChatService.

## Source mapping

`server/engine.go:748-779` starts one planner loop plus the configured worker
slots, registers task-owned routines, and kicks first planning only if **neither
open nor running intents exist**. It also starts the task deadline coordinator.

`server/engine.go:800-914` coalesces event wakes, uses a heartbeat with a minimum
600-second interval, and re-resolves agents each round. Pause/delete/terminal/
deadline-settlement checks precede planning. No open goals takes a separate
non-model goalless path; it may settle only when open/running intents are gone.
Worker completion/finding events carry incremental context. Planner end-turn
alone is not a task completion condition.

`server/engine.go:916-979,1237-1245` checks readiness/lifecycle and claims from
`Frontier(20)`. `db/exploration.go:1136-1147` orders open intents by **priority
DESC, id ASC**, not newest-first. WorkerStore.list orders id DESC and is therefore
not a substitute. Use existing ExplorationStore.frontier(20), then re-read the
candidate with WorkerStore.get to capture its content_version. IntentWorker
already owns claim; do not pre-claim then pass a running row into it.

`agent/mainagent.go` handles human collaboration independently of autonomous
planning. `agent/worker.go:349+` executes one intent, not arbitrary exploration.
The Python worker's done state records turn termination, not goal proof.

## Smallest meaningful implementation

Implement `agent/engine.py` with a single explicitly started, bounded
`TaskEngineHandle`. First support an **already initialized, authorized task**.
Use one planner at a time and a configurable bounded worker executor (initially
one worker is sufficient). Construction must start no work. Expose explicit
`advance(now)` rather than an implicit background scheduler initially; the
production serving host must call it. An optional later pump can own this tick,
not duplicate its state machine.

Suggested contracts:

```
EngineScope(task_id, exploration_id, run_id, revision)
TaskEngineHandle(scope, exact_manager_token, resources, *, worker_slots)
    notify(event_id, kind, intent_id=0, detail='') -> accepted
    advance(now) -> EngineProgress(planning, workers, pending, unresolved)
    cancel(reason) -> None
    wait(timeout) -> bool
    completion_candidate() -> VerifiedCompletion | None

EngineRunner.validate_create(description, goal, options, seed) -> None
EngineRunner.ready(task_or_provisional) -> literal True
EngineRunner.start(task, exact_manager_token) -> TaskEngineHandle
EngineRunner.handles() -> tuple[TaskEngineHandle, ...]
EngineRunner.settle_completed(manager_complete) -> settled_task_ids
```

Reuse the existing manager runner contract. Generalize AgentHost's concrete
runner type check to a validated explicit lifecycle runner protocol, rather than
subclassing AgentRunner while secretly changing its `Handle.terminal_status`
semantics. Keep current AgentRunner for its documented one-turn use case.

The resources object must hold actual reviewed PlannerCycle, IntentWorker,
ExplorationStore, WorkerStore, transcript adapter, explicit provider/profile
resolver, registry/gates and a shared lifetime authority. It is not enough to
accept unrelated callbacks that merely report successful execution.

### One connected progression

1. Under the task lifetime guard, verify exact task/exploration/run/config and
   manager token; reject orphan running intents or unresolved prior receipts.
2. On startup, inspect `has_active_intent`. Existing seeds go directly to worker
   dispatch; no active intents plus open goals schedules one planner wake.
3. Freeze `PlannerWake` with stable cycle ID before submitting exactly one
   PlannerCycle.run. Persist that exact wake/identity in an explicit durable
   control journal if restart replay is supported. Without it, restart must
   quarantine rather than recreate a changed wake under the same key.
4. Drain finished planning without converting `completed` to task done. Read
   priority-ordered frontier, then versioned worker rows. Reserve slot/owner and
   immutable WorkerScope before submit. IntentWorker performs its own CAS claim.
5. On worker future completion, keep slot owned until execution **and SQL/JSONL
   settlement** are confirmed. Emit an idempotent done trigger only after a real
   WorkerOutcome. WorkerUnresolved retains quarantine; do not retry or relabel it.
6. New facts/findings and worker completion coalesce into a bounded wake queue.
   A heartbeat refreshes stale progress, but cannot blindly repeat an unresolved
   cycle. Event overflow must report pressure, not silently discard all context.
7. Graph facts/goals, not model prose, determine whether a completion candidate
   exists. Recheck under the task guard, block new claims, cancel/drain all owned
   workers when the chosen source completion path requires it, and only then
   call manager.complete with the exact token after manager admission returned.
   A stalled planner with open goals remains running/blocked, never done.

## Required safety/integration work, not optional shortcuts

### Shared ownership and lifecycle authority

Manager tokens are process-local object identities; planner/worker scope run_id
and revision are currently host-supplied. No existing factory binds these values
into one durable generation. Supply an explicit generation authority/journal;
never manufacture run_id from task ID, timestamps, `id(token)`, or a fresh local
counter that restarts at one. Until a durable lease exists, require a documented
single-process host and quarantine restart uncertainty.

The SAME guard/current checks must cover engine, graph tools, worker versions,
task-main/side-question operations, deletion, archive, profile replacement and
transcript clear. Holding a lock only around engine reads is insufficient.
Cancellation fan-out must latch before a planner/worker registers internally;
repeat cancellation/current checks cover this start race. `wait(True)` must mean
all providers, tool callbacks, activity sinks and settlement writes have ceased,
not merely that a manager cancellation flag is set. No forced thread kill.

### HTTP currently bypasses the existing host fence

`build_persisted_application` creates TaskManager and supplies it directly as
`task_manager` to TaskService. With a real runner, create/control therefore do
not pass through AgentHost._invoke. A separately polled AgentHost would not see
those mutations. Add one explicit host-backed TaskHTTPManager facade: delegate
read/metadata APIs to manager, create/control to host, and use that same facade
in the persisted factory. Also resolve scheduler admission policy: scheduler.py
requires all admissions to obey its capacity gate; direct HTTP host.create_task
cannot be permitted to bypass global task concurrency silently.

### Graph initialization and deadline prerequisites

TaskStore.create already atomically creates the exploration, origin fact, task
metadata, source links, company scope and profile-chain rows (tasks.py:211-236).
It does not create goal nodes or the seeded intent. AgentRunner rejects seeding. Initial
engine support should accept existing initialized graphs and reject unsupported
creation before task persistence. Then add the missing source-faithful goal/seed bootstrap with a recoverable
receipt, reusing the existing creation transaction rather than duplicating its
exploration/origin initialization, before enabling create+start via HTTP.

TaskStore has first-run clock stamping, but no connected engine calls it at the
first actual planner/worker operation. Scheduler documents missing final timeout
planning/drain. An engine cannot claim timeout parity using only per-call Limits.
Initially reject nonzero task timeout unless the authoritative deadline adapter
is present; later port engine_timeout.go settlement sequence explicitly.

### Completion needs reviewed graph tools and aggregate sinks

Extend PlannerCycle with explicitly supplied registry handlers so reviewed
GraphTools can be exposed under the same revision/gate. Do not automatically
expose native process/network tools or infer approval from catalog enablement.

Task activity/status/SSE cannot be driven by fabricated main conversation
results. Attach actual bounded task/exploration-scoped activity persistence;
projection failure after provider/graph effects is unresolved, not safe retry.
Main-agent direct intent insertion eventually needs a notify bridge, but must
not auto-resume terminal tasks until source revival/goal insertion is ported.

## Tests required before connecting the factory

Use actual authored stores/ChatTranscriptAdapter with inert fake SQL/providers:

* Initialized graph → planner creates intent → worker claims same version →
  authorized fact tool writes graph → worker done trigger → next planner wake.
* Seeded open/running work suppresses first planner; priority DESC/id ASC wins.
* No open goals path skips model; unfinished worker prevents task completion.
* Planner completed with unmet goals does not call manager.complete.
* Duplicate advance/notifications do not create duplicate cycles or workers.
* Pause during preflight, claim and settlement drains owned operations; stale
  generation/profile/worker version blocks subsequent tools and commits.
* SQL or transcript uncertainty remains owned across drain/restart; no auto-retry.
* HTTP create/control crosses host fence; poll cannot settle an uninstalled
  handle; queue admission and ordinary create share explicit concurrency policy.
* Unsupported bootstrap/deadline/revival rejects before promising execution.

Do not describe this bridge as full Go engine parity until compaction, inherited
context, final deadline planning, streaming activity, worker steering/resume,
recovery, and source completion/revival policies are actually connected/tested.

GraphContext (agent/graph_context.py), added concurrently with this audit, can
supply bounded local hot/cold context. It starts no work and does not replace
the generation authority, cycle journal or engine ownership described above.

## First implementation checkpoint

`agent/engine.py` now connects a **serial**, explicitly ticked initialized-task
handle. Each advance performs at most one real planner or worker operation;
there is no background thread, autonomous pump or EngineRunner/factory integration.
It uses a conservative completion subset: no open/running/paused intents remain,
all persisted goals are settled, and no unresolved child exists. It does not
implement Go's goal-met cancellation of remaining open work. Completion re-reads
under the same guard immediately before exact-token manager settlement; cached
PlannerResult/EngineProgress is not authority. Completion never cancels the
manager token. Future goal-driven child cancellation must use separate child
cause/state mappings, not cancel the token required by manager.complete.
Nine initial authored offline tests exercise actual planner→worker→planner,
priority ordering, seed suppression, cancellation/drain, uncertainty and guarded
completion. Independent review is pending; this is not full engine parity.

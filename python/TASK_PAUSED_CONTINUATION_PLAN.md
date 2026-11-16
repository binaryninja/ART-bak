# Same-process paused task continuation: proposed, not implemented

## Source and current boundary

Go `server/task_control.go:102–155` pauses execution before persisting pause and
compensates the engine barrier if persistence fails. `server/goals.go:106`
requires an actually paused nonterminal task under admission serialization;
queue capacity and bootstrap-versus-resume mode remain significant.
`server/engine.go:306–340` clears the pause barrier and creates a fresh execution
context when the old context was cancelled. It does not revive a cancelled
context or rerun goal initialization for an initialized graph.

Python already exposes task pause/resume routes and manager methods. Pause drains
owned work, persists paused state, removes the execution run, and suspends an
attached deadline lease. Resume calls ordinary start, which refuses a retained
deadline. The concrete TaskRuntimeAssembly refuses initialized adoption even for
zero timeouts. Route presence is therefore not natural prepared-task resume.
Python failure after cancellation also remains retained/fail-closed; it does not
yet implement Go's compensation of a failed persistent pause.

## Smallest proposed executable slice

Restrict the first implementation to an explicitly opted-in, same-process,
zero-timeout task that reached prepared engine phase under this exact host. No
terminal resurrection, inherited/recovered task, expired lease, initialized-row
inference, or goals rerun. Queued and pre-bootstrap pause need separate source
mode handling and are initially unsupported.

1. A code-owned suspension component records the original host, runner, manager,
   outer handle/token, exact prepared receipt, executor/guard, immutable complete
   configuration, and original canonical transcript/process resources. Reserve
   it before pause removes the installed execution run. No caller-created receipt
   or public dataclass is authority.
2. Existing task pause performs cancellation and bounded drain. Confirm suspension
   only after canonical cleanup and claimed worker settlement, empty in-flight
   cleanup/process/control uncertainty, unchanged prepared journal/config, and
   exact persisted paused running task. Unknown SQL/cancel/drain outcomes retain
   the original generation and cannot issue a resume capability.
3. Resume reserves a host admission slot and exact unused suspension capability.
   Validate original resource/config/grants and fresh task/bootstrap/graph state
   under the original shared guard. Reject any terminal, deletion, archive,
   queue change, nonzero timeout, non-NULL clock or retained control/finalizer.
4. Construct a fresh CancellationToken and fresh exact BootstrapHandle through a
   private `from_suspension` seam. It starts at prepared/engine-only construction
   using the original retained receipt; it never changes that receipt, reruns
   coordinator.run, or accepts a bare prepared row as authority. A fresh engine,
   cleanup owner and role contexts bind only to the newly installed manager run.
   Old callbacks remain bound to the cancelled old token/old handle and fail.
5. Use an exact paused-task CAS, not the broad status-only ordinary admission,
   followed by confirmed install. Partial construction/SQL uncertainty retains
   both old suspension and new attempt until explicit reconciliation. Do not
   claim rollback or reopen retries after a possibly committed admission.

Suggested exclusive files: NEW `agent/task_continuation.py` for private issuance,
configuration/receipt validation and lifecycle state; NEW `db/task_continuation.py`
for bounded parent/bootstrap checks and exact paused CAS; narrow manager
suspension/resume reservation methods; private BootstrapHandle engine-only
construction; host/runner install/retirement hooks. TaskRuntimeAssembly supplies
its existing immutable engine factory explicitly, without initialized fallback.
These signatures and the necessity of a durable continuation journal should be
reviewed before implementation. A one-process receipt is not restart authority.

## Timing, shutdown, and rerun are separate

A stamped timed lease is bound to the original handle/token/store and remains
suspended; its absolute deadline continues to elapse. Timed resume requires an
explicit atomic ownership transfer of that lease, observer/finalizer/frontier
registration and manual action inventory, including refusal if expired/frozen.
It must not restamp, extend time, or leave an old observer able to retire the new
execution. Unstamped positive tasks also require timing-registration transfer.
Neither case belongs in the minimal zero-timeout slice.

A clean suspended receipt must remain available in the same application but must
not falsely represent an active worker. Shutdown seals it against new resumes;
active/uncertain resume attempts prevent resource close through their full tail.
A restarted application cannot recreate this capability from SQL alone. Restart
support needs a durable execution-generation/recovery protocol and reconciliation
of canonical transcripts, worker claims, process jobs, clocks and final journals.

Intent rerun is distinct: Go may reopen blocked/exhausted/stopped work and revive
a terminal task, rolling back the old node state if admission fails. Same-process
paused continuation can provide only its nonterminal paused-task admission
prerequisite. It does not authorize terminal resurrection, graph reopening,
deadline reset, or generic rerun rollback.

## Acceptance before claims

Actual owned blocked worker → task pause → canonical cleanup/open worker row →
paused persistence → explicit resume → fresh token/engine → next ordinary pump
without another goals turn. Old retained tool/provider/context callbacks must
remain denied. Test idle prepared pause, duplicate resume, concurrent pause or
shutdown, changed config/profile/receipt, foreign/copied capability, stale SQL
version, callback revocation, cancellation/commit uncertainty and partial engine
construction. Verify no provider during pause/resume handlers, no worker effect
from a cancelled old turn, and manager/runner/manual inventories through failure.
Repeat on genuine PostgreSQL with zero timeout from initial task creation, no
post-hoc configuration edits. Positive timeout and restart tests must still fail
closed until their separate protocols are implemented.

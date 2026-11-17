# Synchronous existing-task stepping

`artex.agent.task_step_driver.drive_existing_task(application, task_id,
max_ticks=16)` is an explicit API for a caller who already owns a correctly
assembled TaskRuntimeApplication and exclusively operates it manually. It is not
a CLI, full application launcher, task-creation path or runtime configuration
factory. Existing creation/listener/pump/lease/startup/shutdown code is unchanged.

The driver accepts a canonical positive int64 string task ID and 1..1,000 tick
attempts. The already-loaded manager must contain that task. With no owned handle,
only raw-created, unpaused, unqueued zero-timeout metadata can be admitted through
the existing facade and its capacity/admission policy. A stored running row is
rejected even if manager.status() presents it as created because no run is owned.
Paused, queued and terminal tasks are not resumed/restarted. The driver does not
construct providers, discover credentials or grant tools/scope/approval; existing
runtime currentness, provider selection, host admission and exact action grants
still govern every operation.

An existing database row is **not** certified runnable by these checks. The Task
DTO has no initialized flag and omits archive/deletion metadata. The unchanged
canonical bootstrap journal validates current persisted eligibility, prior
receipt and graph occupancy on the first admitted tick. It can reject after
admission already changed status; that is retained uncertainty to inspect, not a
clean never-started result. No initialized-task recovery or existing receipt is
adopted. If the same application still owns the original live generation after a
tick-budget result, a later explicit call can continue that live work without a
second admission. This is not reconstruction/restart after losing ownership.

Each advance uses application.tick(), including its existing host settlement.
`TaskDriveResult` contains task_id, state, ticks, advanced, status and started.
Its completed property is true only for state=completed, requiring this call's
fresh tick settlement to name the target, done status, no retained target run or
other ownership, an empty runner and an all-settled host result. Model text,
preexisting done metadata or a tick return alone cannot prove completion.
Already-terminal observations have state=already_terminal and completed=False.

Other states are tick_limit, busy, stopped, unresolved, cancelled and inactive.
ticks counts returned tick attempts, advanced counts attempts that reported the
target advanced; these do not count model calls or prove useful progress. Busy
and stopped outcomes are not retried. Existing pump failure is unresolved rather
than stopped. started reports a successful admission by this call only; if an
exception follows admission, no result is returned and effects may already exist.
The facade must return literal True before any tick. False or any other return
raises without ticking, retrying or releasing even if admission already retained
a handle. This is not treated as a harmless/no-effect failed start.
Lower layers retain their own error classifications; the driver adds no retry or
exception suppression. It never pauses/cancels tasks, drains/closes resources,
stops a pump or releases ownership. Caller reconciliation remains necessary on
non-completion and exceptions. Even completed is not a storage-shutdown receipt.

**Exclusive manual-controller prerequisite:** application.tick is host-wide. The
driver rejects unrelated retained handles, timed/continuation owners and any pump
with a prior/current thread reference. A per-pump lock prevents overlapping calls
to this driver only. Checks and post-tick receipts detect some foreign work; they
are observations, not an atomic fence against external admission/manual ticks or
callback mutation. Do not run this API alongside a listener/controller or call
pump/host/manager APIs independently. No general concurrent serving-loop safety
or prevention of already-performed effects is claimed.
Object pins do not authenticate arbitrary monkeypatched method implementations;
this is not a tamperproof in-process capability or forged-receipt defense.

max_ticks is **not a wall-clock timeout**. One synchronous provider, tool, database
or policy callback can block; existing cancellation/time limits are cooperative.
The driver does not advance/fake time, shorten the planner heartbeat, spin on busy,
sleep, force termination, retry failed effects or override positive-timeout gates.

The focused test example uses actual published TaskRuntimeAssembly, role resolver,
bootstrap journal, goals, planner, worker, native record_fact/prove_goal tools,
canonical temporary JSONL transcripts and host settlement over the published
in-memory SQL fixture that already contains task 5 and origin node 1. Eight fixed
synthetic responses finish in four synchronous ticks. No task/exploration INSERT,
POST-create, socket or thread start occurs. The fact is synthetic fixture evidence,
not a real-world observation. SQL is simulated and policy grants are explicit
fixture grants: this is not real PostgreSQL, live provider, rendered frontend,
full-app end-to-end or production acceptance.

Authored base is 9e119493. Newer published 3759642b is now available separately;
this candidate still requires independent review and fresh exact current-head
union tests rather than inferring compatibility from the base-only result.
Held task-create/startup-retention/traffic/QA artifacts are not used or modified.

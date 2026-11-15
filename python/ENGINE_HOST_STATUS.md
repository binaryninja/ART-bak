# Explicit task engine and hosted lifecycle checkpoint

The Python implementation now connects actual planner cycles, priority-ordered
intent selection, versioned workers, scoped graph tools and authoritative goal
checks through an explicit engine. This is not a production autonomous scheduler.

`TaskEngineHandle.advance` performs at most one bounded planner or worker operation
per call. No constructor starts threads or model work. Notifications coalesce
without replacing an active immutable wake. Existing unowned running work and
uncertain persistence quarantine the handle rather than trigger automatic replay.

`EngineRunner` implements the TaskManager lifecycle for already initialized tasks.
It never maps an ordinary model end-turn to task success. Completion requires a
fresh graph check under the lifetime guard, drained child work and the exact
manager token. Actual PostgreSQL tests cover persisted done/timestamp and retained
ownership after terminal SQL failure. Pause is distinct from terminal completion.

The optional hosted factory owns one actual manager, host, runner and narrow HTTP
facade. All task admissions require the same explicit capacity guard as queue
admission. Defaults remain separate and unchanged. Existing tasks load without
resuming; providers, tools, profiles, scope authority and ticks are host-supplied.
Unsupported creation/seeding is rejected before admission writes. Host/runner
ownership and manager/graph executor binding prevent cross-manager/database reuse.

Read-only native graph context tools require exact run/revision metadata and fresh
grants. Worker metadata now includes those fields alongside its generation digest;
older durable receipts without them conflict rather than silently replay. Opt-in
record deletion requires an action-specific host policy, uses exact scoped task
filters, and does not remove usage history. No production records were deleted.

Remaining limitations include task creation/bootstrap, durable generation leasing,
distributed execution, automatic pumping/deadline final planning, crash recovery,
inherited graph views, source goal-met worker cancellation policy, full activity
projection and complete tool/API/translation coverage. Cooperative callbacks must
supply their own finite budgets; no thread-kill or process sandbox is implied.

See ISOLATED_QA.md for immutable fixture manifests. Optional HTTP factory behavior
requires its own specific QA evidence; lower-level manager completion tests alone
are not proof of full HTTP-to-completion compatibility.

Frozen checkpoint: 1,545 authored tests pass; 336 captured SQL statements parse.
Static method/path coverage remains 116/261, including optional/unavailable
contracts, not a completed-feature count. Stage30 separately verifies the explicit
HTTP-to-manager-completion path using inert providers and disposable PostgreSQL.

# Early timed completion: source frontier-drained semantics

The implemented prerequisite follows the Go ordinary completion branch, not a
proof-only achievement test. No positive admission or automatic engine hook is
enabled. `done` with receipt reason `frontier_drained` means execution finished;
it does not assert that every goal was achieved.

## Exact prerequisite now implemented

`db/early_completion.py` provides explicit fresh-schema installation and one-shot
`EarlyCompletionStore(TaskDeadlineStore, grant=permit, authorize=...).settle()`. A separate
manager capability reserves the exact attached, unpaused original owner and
fences new execution before checking actual zero-wait drain outside the registry
lock. An active or unresolved handle cannot be settled. There is no cancellation
of working children to manufacture drain, and no adoption of a bare clock lease.

Under the original host lifetime barrier and task-first SQL locks, the store
checks exact prepared bootstrap/configuration/clock, an unexpired task, and a
bounded local frontier. No open goals and no open/running intents is sufficient.
Empty/abandoned goals and paused intents match the source predicate. The task
update and durable `frontier_drained` receipt share one transaction. Original
grants, committed task/receipt/bootstrap and frontier are rechecked before private
commit evidence is consumed for registry retirement. Attempted-write uncertainty
retains ownership; it cannot trigger model replay or an automatic retry.

The old ordinary engine completion hook still rejects attached leases. A future
host integration must call this dedicated reservation while idle, not synchronously
wait for the currently active engine completion frame. Zero-timeout behavior and
all positive runtime admission/SQL gates remain unchanged. Immediate all-met
completion with active workers requires a separate source-grounded cancellation
path and is not included.

## Evidence and limitations

Tests use actual authored TaskManager/BootstrapHandle and synthetic SQL/positive
prepared clock pins. They do not establish natural positive HTTP admission or
PostgreSQL acceptance. All graph writers must honor the same lifetime guard and
parent lock; readback checks do not create cross-process fencing against arbitrary
uncooperative writers. Any reserved refusal remains quarantined pending explicit
reconciliation rather than silently restoring execution or converting to expiry.

## Source-semantics correction (must govern implementation)

The stronger proof-only design above is **not source-equivalent**. Source inspection
shows two distinct ordinary done paths:

1. `agent/tools.go:1175-1184` prove_goal requires a local goal and a local OR direct-
   source fact/finding node. It does NOT require evidence state `confirmed`. It
   links evidence and sets the goal met (both returned errors are ignored there).
   `1187-1196` raises ToolSet.GoalMet only when every listed goal is exactly met;
   an abandoned goal does not count for this immediate all-met signal.
   `agent/planner.go:472` returns that GoalMet flag. `server/engine.go:883-894`
   persists done and cancels running workers with AbortGoalMet.
2. `db/exploration.go:1162-1170` HasOpenGoal tests ONLY state open, explicitly treating
   met/abandoned/no goals as settled. `server/engine.go:841-857` skips the planner
   when no open goals remain and writes guarded done when HasActiveIntent is false.
   HasActiveIntent (`db/exploration.go:1150-1159`) checks open OR running only:
   paused intents do not block this source path. It does not inspect evidence
   state, proves edges, count goals, or require all goals to be met. Thus abandoned
   goals can lead to done, not merely stop planning while waiting for timeout.

Additionally `agent/tools.go:1205-1213` retains the explicit goal_met bypass;
`agent/toolcatalog.go:52-59` makes it unbound by default but manually opt-in. This
port has not implemented that broad bypass. Deadline finalization's met→done else
 timeout mapping is separate (`server/engine_timeout.go:212-218`).

Current Python engine readiness (met OR abandoned, with no open/running/paused
intents, and a nonempty goal set unless manual mode) already differs from source
in paused-intent and zero-goal handling. Existing Python final settlement's local
confirmed proof requirement is also a deliberate narrower safety subset, NOT a
proof requirement imposed by Go. Do not use it to redefine ordinary done silently.

### Recommended minimal compatible decision

Start the new early store/capability with the source **settled-goals/frontier-
drained** path: exact original clean owner, no open local goals, no open/running
intents, fresh locked supported local graph snapshot, and guarded done. Abandoned
and empty-goal graphs count as settled; paused intents do not imply running work.
Record an explicit receipt reason such as `frontier_drained`, separate from an
`all_goals_proven` claim. The terminal status remains source `done`, which in this
path means task execution is finished, not that every original goal succeeded.
No model inference or proof strengthening is required to recognize that state.

Keep exact original-generation/config/clock CAS, explicit read/write authority,
clean actual worker/cleanup drain, atomic receipt, bounded reads, postcommit
uncertainty and retirement rules from the plan. Unknown/corrupt states can remain
an explicitly documented fail-closed validation limit. Inherited goals/evidence
are not needed to implement the local HasOpenGoal/HasActiveIntent predicate;
do not reject source task relations solely to enforce a nonexistent local proof
rule, but verify that the exploration selected is the task's own current graph.

The immediate GoalMet-with-active-workers path needs separate completion-driven
child cancellation/settlement semantics; do not disguise clean-drain-only support
as full parity. If the parent intentionally chooses a stricter proof-only mode,
make it explicitly named/opted-in and preserve the compatible drained path rather
than silently changing what abandoned goals or `done` mean.

## Stable revocation contract (unpublished early-only API)

`EarlyCompletionGrant(exact_prepared_receipt, operations=frozenset(...))` is an
explicit host-issued scoped permit. The store requires the same permit at
construction and from each `authorize(receipt, operation)` callback. Booleans,
copies, foreign scope and substituted permits are rejected. The finite operations
are reserve/read/write; reserve must be permitted before manager reservation.
`revoke()` is irreversible. There is no re-enable operation.

The final authority callback precedes complete persisted task/bootstrap/receipt/
frontier verification. The final SQL adapter may revoke the actual permit; a pure
permit check after SQL detects it. The permit lock is held through private commit
evidence consumption and manager acknowledgement. There are no external SQL,
configuration or authorization callbacks in that critical section. Manager code
never acquires the permit lock while holding the registry lock; order is permit
then registry. Token cancellation runs after the permit lock is released, and
failure restores quarantined ownership. Concurrent revocation is ordered before
acknowledgement (deny) or after it (cannot retroactively undo committed retirement).

This replaces the unpublished boolean API rather than claiming a fixed point
between arbitrary policy and SQL callbacks. Host policy revocation must revoke
the permit, not merely mutate an unrelated boolean. Callback-driven durable
mutation is still caught by the final persisted verification; arbitrary writers
outside the shared lifetime/parent-lock contract are not made atomic by readback.

## Invocation lifetime remains unintegrated

The standalone early store is not registered with `ManualActionOwner` and is not
an automatic engine completion path. Removing the manager deadline slot does not
mean its calling invocation has returned: token cancellation and failure handling
still run afterward. The eventual host/app integration must track that entire
post-retirement tail before advertising listener or executor shutdown safety.
Current standalone tests do not establish same-application shutdown safety.

# Internal registered timed engine branch (review candidate)

Implemented only for the original, fully registered prepared owner. Positive
HTTP/runner/BootstrapHandle creation gates remain closed; no schema installation,
observer timer, automatic finalizer or task terminal write is added.

The runner binds the engine once after successful original post-prepare lifecycle
registration with an explicit final plan. Exact binder/actions/outer/timing,
receipt/scope, original methods and original manual registry members are checked.
Sealed/failed/incomplete/replaced registration cannot grant the next engine step.
A partial bind retains the child as unresolved, not retryable construction.

The original zero-timeout SQL and completion behavior are unchanged. A separately
registered branch selects exact task + prepared bootstrap identity and positive
timeout. Both NULL clock fields are permitted only before the original kernel
has a lease; stamped fields must equal that exact lease. Partial clocks, unknown
stamps or changed prepared identity refuse execution. This query is not claimed
to make the whole graph/provider/JSONL workflow atomic.

PreparedTaskTiming.execution_status observes, never stamps, the original kernel.
Busy or elapsed status before snapshot/claim/turn yields busy or expiry_wait
without consuming queued wake events or allocating new work. Empty frontier is
not rejected as missing bootstrap in this branch, but this engine never marks it
done: the existing source-compatible frontier completion owner remains required.
Ordinary engine.settle_completed cannot bypass that owner for timed tasks.

An admitted task_deadline_expired terminal outcome remains distinct from generic
errors and per-turn timeout. Source server/engine.go timeout termination maps to
exhausted, not goal achievement; worker and canonical cleanup now agree on that
exact reason. Engine expiry_wait after admission requires persisted, nonstale,
nonreused canonical outcome AND successful original cleanup finish. Failed
commit/CAS/projection/process drain still retains uncertainty. Frozen cancellation
keeps its separate existing cancellation mapping. Monotonic expiry never grants
DB-expired final assessment authority.

Eight authored branch tests pass using actual JWT bootstrap/host/outer/registered
engine with explicitly synthetic prepared positive pins, actual original kernel
stamp protocol and inert SQL/provider. They cover busy/expired preclaim no effects,
exact snapshot/partial-clock refusal, copied/sealed registration, no ordinary
completion, and admitted fake-transport typed expiry through canonical cleanup.
Neighboring engine50/timing11/worker-expiry5 tests pass. Four independent branch
regressions pass (17 with authored branch and worker-expiry tests), covering SQL
expiry race before claim, generic clock error distinction, commit uncertainty
retention, and removed manual registration. Exact PostgreSQL acceptance remains
pending. No natural positive admission or
full production recovery is claimed; the earlier full persisted-app synthetic
zero→positive digest refusal remains an intentional boundary.

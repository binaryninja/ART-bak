# Post-prepare timing binding: connected but admission-gated

The opt-in assembly now creates an exact scoped `PreparedTaskTiming` from an
explicit frozen `TaskTimingPolicy`. Its revision and enabled mode participate in
runtime configuration identity; callable representations and secrets do not.
Cleanup authority is independently required, not implied by the timing policy.

Bootstrap retains the actual child before calling `bind_timing(manager, outer)`
after cleanup binding and before engine execution. The one-shot binder verifies
original prepared receipt, installed manager/token, executor, child and both
runtime clock identities. Binding does not query SQL, stamp, execute providers,
invoke grants or start an observer. Partial failures retain the child as unresolved;
there is no retry/adoption or second manager.

Zero timeout creates neither a deadline store nor shared kernel and returns the
original provider binding. For a synthetic positive prepared owner, exactly one
TaskDeadlineStore and OwnedGenerationClock serve all role wrappers. PlannerScope
can wrap only planner; WorkerScope can wrap only worker. Each lazy provider retains
its model/profile fences and independent dispatch grants. The exact same pinned
clock reaches the actual planner/worker ChatAgent and RuntimeContext. Goals are
never wrapped and do not stamp a task execution deadline.

Public positive-duration constructors/admission and engine SQL guards remain
unchanged and reject timed tasks. Positive component fixtures explicitly modify
prepared pins; they are not supported application admission. No automatic observer,
final-assessment trigger, terminal settlement, lease transfer, resume adoption or
restart recovery is installed. The manual observer remains separately explicit.

A reproduced cross-role scope acceptance regression was fixed at the wrapper
boundary before configuration callbacks. Seven owner and three independent binding
tests pass, alongside 59 runtime tests. Independent review cleared the exact
role/assembly boundary. Frozen full suite:2786 collected tests,455 SQL statements.
Exact-source isolated PostgreSQL stage94 passed three authenticated application
scenarios and21 focused tests: natural zero-timeout opt-in/default both complete
local goals/planner/worker flow without deadline stamps; positive entry refuses
before task creation; clock corruption at binding retains unresolved ownership.
This is genuine zero-timeout application acceptance, not positive timed support.

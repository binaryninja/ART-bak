# Shared dispatch clock and context clamp (not timed-task admission)

`OwnedGenerationClock` shares one original prepared generation's stamp/lease and
nonexpanding monotonic ceiling across planner and worker dispatch facades. It
requires an actual TaskDeadlineStore, explicit timeout and one monotonic clock
identity. Construction performs no SQL. Existing leases cannot be adopted by a
new kernel. First confirmed stamp is shared; later calls revalidate rather than
reserve another owner. Ambiguous persistence latches uncertainty and cannot retry.
Timeout zero remains a no-stamp operation.

`PreparedDispatchClock(..., generation_clock=...)` retains role/model/scope and
fresh request grants. All facades must use the kernel's identical monotonic clock.
A later worker/new RuntimeContext cannot extend the original generation ceiling
when wall time moves backwards. `validate_dispatch(context)` is a read-only
binding fence for transport boundaries; it neither stamps nor grants a new model
operation. Final sampling is followed by pure policy/resource/owner checks.

`RuntimeContext.clamp_deadline(deadline, clock=...)` shortens the SAME context and
requires its original clock identity. It cannot widen an earlier ceiling.
`remaining_seconds()` returns a positive duration measured in that context's own
clock, never an absolute timestamp for another clock domain. Exhaustion,
cancellation, backwards/nonfinite samples, rebound clock or a widened context
fails closed. Every sample is tracked, cancellation is checked after clock code,
and the latest callback-shortened deadline is used for the returned duration.

The separate `DeadlineProvider` adapter invokes the final authorized dispatch
facade immediately before actual completion or the first stream iteration, then
clamps the context passed to the real provider. Merely constructing a stream does
not stamp or send. Per-chunk fences do not create another stamp. The explicit HTTP
transport can min its own socket budget with `remaining_seconds()` without mixing
absolute clock origins. Provider cancellation remains cooperative; arbitrary
blocking callbacks are not forcibly interrupted.

These components are not automatically installed by TaskRuntimeAssembly. They do
not enable positive-duration constructors, create an expiry observer, replay a
pending stamp or provide terminal authority. Actual timed admission still needs
reviewed connected lifecycle wiring and separate acceptance. Synthetic positive
prepared pins in tests exercise the protocol only, not supported production task
creation. Cleanup/final assessment/settlement remain distinct capabilities.

## Verification

The frozen checkpoint passes 2748 collected tests and 454 PostgreSQL syntax checks.
Exact-source isolated PostgreSQL stage92 passed three component groups and fifty
focused tests (manifest and limitations in ISOLATED_QA.md). Distinct planner and
worker profiles/new contexts share one persisted stamp; close-time grant
revocation is surfaced and iterator close runs once. Actual postcommit stamp
acknowledgement uncertainty blocks second-role replay. No positive timed engine
entry or assembly gate was enabled.

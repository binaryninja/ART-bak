# Restricted final persisted-planner provider (review candidate)

The explicit `FinalTaskProviderAdapter` now uses the existing actual
TaskProviderResolver/ProfileFactory for the **planner** role. The final transcript
role remains `final_assessment`; goals, workers and ordinary planner execution
are not substituted. No default adapter, automatic FinalizationPlan construction,
new tool, proof write, timer, timeout admission or recovery path is enabled.

An exact FinalAssessmentRunner privately issues FinalProviderInvocation only
after its journal attempt is running. The original runner retains its identity,
thread, final local cancellation event, receipt/configuration, journal resources,
transcript/recovery guard, clock and independent finite deadline. The actual
model gate admits the original RuntimeContext/token; a receipt, copied scope or
arbitrary context does not authorize a provider call. Invocation authority is
revoked before journal finalization callbacks, before failure cleanup, and in
finally. Cancellation never revives ordinary frozen execution authority.

Reconciliation runs outside the provider lifetime guard. Inside that original
guard the current check is callback-free; it does not recursively enter journal
SQL. Persisted task/profile read grants remain independent. Role/profile/config
changes fail closed through the existing resolver. The same context is shortened
to the original final deadline using its identical clock, never the expired task
deadline. Existing transport budgets remain relative-duration bounds. Blocking
arbitrary providers still require cooperation; this is not forced interruption.

Streams are lazy, fenced at iteration/disclosure and close, and existing iterators
are closed even after revocation. An originating provider/cancellation failure is
preserved. Close/configuration failure cannot be reported as successful model
completion. Existing canonical failure settlement and retained manual ownership
remain responsible for uncertainty; no model retry is introduced.

FinalizationPlan has an explicit mutually exclusive typed adapter alternative to
its legacy resolver callback; the adapter is not hidden in the old lambda.
Timed lifecycle composition pins all plan fields and requires the original
runtime resolver when that alternative is explicitly selected. Constructors do
not read profiles or create transports.

Nine owner adapter tests pass with actual journal/ChatAgent/canonical transcript,
actual persisted resolver and inert HTTP; nine existing timed lifecycle tests
also pass. Five worker invocation tests pass, along with 31 final-assessment and
17 owned-finalization regression tests. Seven independent adapter regressions also pass (33 combined with adapter,
invocation and final-runner baselines): non-reentrant journal guard, grant-time
budget exhaustion, close-time cancellation, lazy unused stream, retained binding
revocation, profile drift and typed bind replacement. Exact real-PG acceptance
passed seven component scenarios and 33 focused executions in stage 117.
Typed OwnedFinalization forwarding, prior ordinary-token cancellation and
persistence-failure variants were not newly real-PG tested; see ISOLATED_QA.md. Registration PG113's full persisted
app synthetic timeout mutation was correctly blocked by its durable digest;
this adapter does not remove that boundary or imply natural positive admission.

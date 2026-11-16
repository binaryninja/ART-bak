# Shared conversation dispatcher callback lifetime

Reproduced on the actual shared ChatDispatcher: an ordinary issued token callback
blocked inside dispatcher.cancel while its runner/future finished. Concurrent
close returned drained=true, and the same conversation could admit a new runner
before the old callback returned. Standalone conversation runtime uses this queue;
task chat's separate outer accounting did not fix the shared queue itself.

Cancellation job selection and sweep reservations now share the queue condition.
Every issued token is a queue-owned Cancellation subtype: direct cancel and the
whole on_cancel call (including immediate callbacks after cancellation) acquire
exact job lifetime before callbacks. Per-job callback counts retain busy ownership
until terminal/Future settlement and callbacks finish. Global close waits under its
existing shared deadline and cannot report drained while callback work remains.
Callback execution occurs outside the condition for cancellation operations.
Callback self-close refuses before joining, preventing its own drain cycle.

Explicit compatibility boundary: after exact job retirement, issued cancel and
on_cancel raise DispatchUnavailable before event change/callback execution. The
cancelled/check observation interface remains readable. Standalone Cancellation
semantics are unchanged. A Future completion callback cannot submit another turn
for its own conversation until that callback returns. Different conversations and
queues remain independent. Existing uncertain persistence still retains ownership;
no rollback, retry, lifecycle recovery or namespace authority is inferred.

Timeouts do not preempt synchronous callbacks or acquiring a lock held by them.
In particular Future callbacks still execute under the existing settlement lock.
A close that invokes a blocking cancellation callback can exceed its timeout;
an independent close observes retained work. No arbitrary host-code containment,
forced callback termination, or automatic resource closure is provided.

Eleven new tests cover dispatcher/direct-token cancellation, immediate registration,
self-close/nesting/reentrant cancellation, exceptions, same-conversation retention
and next turn, retired tokens versus new jobs, Future callback admission, standalone
tokens and independent queues. Ninety combined shared/conversation runtime/task-chat
shutdown/HTTP/authority/stop tests pass. Independent review cleared source 08e4d23d with 57 focused tests and independent
direct-token/immediate-registration blocked callback probes, busy/next-turn refusal,
undrained-then-drained close and stale-token refusal.

QA236: `/workspace/shared/artex-qa/conversation-drain-236/REPORT.md`.
Manifest SHA256:
`f7899dd9ac77b1288b09ecb0d5fc15f87bfdfa3c7adb8e730eaeb18a7fcd2047`.
Ten actual ChatAgent/temporary JSONL/PostgreSQL ConversationActivityStore scenarios
and 93 focused queue/conversation/TaskChat tests pass. Integration uses an inert
provider through real ChatAgent and durable activity/transcript, not fabricated
successful ChatOutcome objects. Dispatcher/close/direct-token cancellation and
immediate on_cancel callbacks retain ownership after Future settlement; next turn
refuses and timed close remains undrained until release. Retired operations refuse;
a fresh turn persists afterward and the stale token cannot cancel it. Future
same-conversation admission refusal, nested/reentrant self-close, exception cleanup
and queued cancellation without provider/transcript execution were exercised.

QA235 is preserved as nonfinal fixture refusal-type failure. QA236 is a frozen
index plus explicit overlays, not exact staged-tree acceptance. It has no HTTP
server; TaskChat interoperability is covered by existing focused tests, not a new
actual-PG TaskChat integration run. No external provider/targets or production
assembly claim. Synchronous cancellation/Future callbacks and lock acquisition can
exceed timeout; no preemption or exhaustive race-freedom claim. Ownership does not
cover arbitrary callbacks newly attached to an already-completed Future or detached
external work. These limits do not weaken the explicit stale issued-token refusal
and completion-callback admission changes above. No staging/publication; held
dashboard remains untouched.

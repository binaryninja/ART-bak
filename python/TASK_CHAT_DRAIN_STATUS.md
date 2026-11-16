# Task chat full-lifetime drain correction

Reproduced defect: TaskChatDispatcher.running includes its core, but old close
reported queue-only drained=True while direct TaskChatCore.run remained blocked
in an inert provider. Closing shared resources based on that result was unsafe.

Core now tracks complete public prepare/run invocations before effectful callbacks,
seals new admission permanently, waits for outer finally and retains unresolved
persistence ownership. Cancellation is requested outside its lock, with callback invocations retained
until return even if the associated run has finished; tokens created
by already-admitted calls after sealing inherit cancellation. Internal preparation
within an accepted run does not attempt a second public admission after seal.
Core wait avoids waiting on its own invocation; it never clears unresolved state.

Dispatcher tracks/seals outer prepare/submit before activity factory/transcript/
persistence callbacks. Each accepted submit reserves an exact core-owned queued
ticket before callbacks, with copied scope and single-use execution. Tickets remain
until actual queue future completion, including terminal activity projection.
Executing thread identity remains attached through projection to avoid self-wait.
An explicit projection context also covers canceled-before-execution jobs that
never enter the core runner. Dispatcher cancellation and queue-close callback
lifetimes remain registered independently of worker or Future completion.
The dispatcher pins its original core and refuses public core replacement; ticket
completion also uses the original core rather than a late-bound public attribute.
This permits already accepted queued work to settle after public core sealing
when cancel=False, without allowing arbitrary supplied scope to bypass seal.
Repeated receipts retain their separate ticket until the same original future
settles. Existing queue uncertain ownership remains unchanged.

close seals dispatcher/core, waits outer calls, drains queue, then waits core under
one wait deadline. Synchronous cancellation callbacks can exceed that deadline;
the timeout is not a forced callback cancellation or a bounded wall-clock promise. TaskDispatchDrain retains drained/alive_workers/unresolved/
failed_executions attributes and adds outer_invocations/core_invocations. Timeout
or ownership uncertainty never grants resource-close authority or removes owners.
Cancellation remains cooperative; blocking callbacks cannot be forcibly stopped.
No provider/transport/transcript is closed by these methods. No automatic recovery,
unresolved clearing, new HTTP status route or fabricated idle dispatcher.

Fourteen new tests cover direct-core false drain, blocked preparation and late token,
unresolved persistence, blocked submit callback, self-close, single-use preseal
ticket, genuine accepted queued work after noncancel seal, and blocked terminal
projection, blocked cancellation callback retention after run completion, core
replacement refusal, terminal projection self-close, dispatcher cancellation
callbacks outliving Future completion, canceled-before-run projection self-close,
and worker self-close while an external close is joining that worker. 24 existing task-chat
tests also pass (38 total).

## Independent review and QA216

Independent read-only review cleared the corrected code under the cooperative
host-owned lifecycle boundary, including additional provider, terminal and Future
callback concurrent-close probes. This is finite coverage, not general race proof.

Report: /workspace/shared/artex-qa/task-chat-drain-216/REPORT.md
Manifest SHA256:
55f068888982f01a23d63931355be171ccb32954cc5d9d08904038ff10834e42

Twelve actual isolated PostgreSQL 17 / temporary-filesystem / inert-provider
scenarios and 38 focused tests passed. Coverage includes direct blocked provider,
resolver and tool callbacks; loopback HTTP 202/status and cancellation; accepted
queued work after noncancel seal; blocked terminal persistence; cancellation
callbacks outliving a run or Future; original-core replacement refusal; terminal
self-close including canceled-before-execution jobs; and nested dispatcher close
while an external closer joins its worker. Nested close reports not-drained;
after release, original work and terminal activity persist and external close
can report drained.

Core SHA256:
77b766a0e059f2a7dce0b84b5a98db61ba207bd9a997b53c458edc0aba21e519
Dispatcher SHA256:
610480e6b32f382a9e754b5889403f8654bfdb03f71340ca98b4d8326d4191d5

QA213–215 remain preserved as nonfinal evidence preceding additional callback,
identity and self-wait corrections. QA216 used a frozen index plus explicit
candidate overlays, not exact staged-tree acceptance. Its static synthetic HTTP
authorization and fixture lifecycle guard do not establish production or JWT
integration. No real external provider, target or credential was used.

Synchronous cancellation callbacks may exceed the invoking close/cancel timeout.
Concurrent timed close retains them, but this is not preemption or a hard deadline.
No namespace exclusivity, runtime-launch authority, main-segment ownership,
recovery, resource disposal, default wiring, staging or publication is established.

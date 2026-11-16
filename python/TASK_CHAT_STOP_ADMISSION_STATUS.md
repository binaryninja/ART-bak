# Cancel already-issued task-chat work before token creation

Reproduced with genuine inert TaskChatCore: while run was blocked in preparation,
running wasTrue but cancel returnedFalse; release invoked the provider and persisted
a transcript. HTTP prepare/factory/check_scoped variants likewise returned idle
from stop and executed afterward. Cancellation previously saw only active tokens.

Core cancellation now marks currently issued prepare/run/queued/request invocation
records before tokens exist. Tokens bound later inherit that cancellation. Canceled
public prepare does not return a usable result. Callback lifetimes remain tracked;
no cancellation flag is persisted onto future unrelated admissions for that task.
Preparing work is included in running observation rather than fabricated idle.

TaskChatService send now holds dispatcher.operation across prepare and submit.
This creates a real core-owned request invocation before preparation callbacks;
submit checks exact live operation membership/task identity under the same registry
lock before issuing the queued ticket. Stop in the prepare→submit gap therefore
marks the operation and denies subsequent admission, not merely an unrelated
prepare receipt. Direct host submissions remain separate explicit admissions.

Queued user admission checks cancellation after factory/transcript/authority
callbacks and before persistence. Already issued writes cannot be rolled back by
stop. If user persistence completes while stop is requested, the existing queue
records cancellation terminal activity and skips provider execution. If the write
acknowledgement fails, existing uncertain ownership remains; no false no-write or
clean-drain assertion. Terminal projection is not blocked merely because the turn
was canceled. Existing callback timeout/drain and host-authority limits remain.

Six new tests cover direct run/prepare pretoken cancellation; three HTTP callback
seams; an exact prepare→submit gap; future fresh turn admission; actual user write
followed by canceled terminal; write-then-error uncertainty; wrong-task/expired
operation rejection. All 58 task-chat tests pass. Independent review and scoped actual
PG/HTTP QA227 passed. No main-segment/runtime owner, default mount, recovery or
namespace capability is added. Dashboard candidate untouched and remains held.
No staging or publication.

## Independent review and QA227

Independent read-only review verified the corrected hashes with 53 focused tests,
the three original cancellation seams, and an additional cross-task probe. No
remaining blocker was found within per-issued invocation lifetime semantics.

Report: /workspace/shared/artex-qa/task-chat-stop-227/REPORT.md
Manifest SHA256:
a7568c236cc1f19ea9a7eb0cabfaac5ae52d7d557df8bc1c8090940e9786ad65

QA227 passed10 actual PostgreSQL/filesystem/threaded-loopback HTTP/direct-core
scenarios plus58 focused tests. Seven scenarios use concurrent real HTTP requests
with actual TaskActivityStore and inert provider. Preparation, activity factory,
transcript precheck and returned-prepare→submit gap report runningTrue/stopping;
the original send refuses503 without activity/transcript/provider effects. A
separate fresh turn then succeeds202 and persists/drains, so stop is not a seal.

When actual user activity has committed but its acknowledgment is blocked, stop
leaves send202 and persists user plus canceled terminal result without invoking
provider. A synthetic post-write acknowledgment error instead retains actual user
row and unresolved ownership with503; no rollback or clean drain is claimed.
Different-task stop does not cancel the original request. Direct prepare/run
pre-token cancellation and wrong-task/expired issued-operation refusal passed.

Verified source SHA256:
- core: 9ac9baf0f199e9986dd5a76186550745c6c511c139ebebb3aca57742f793259f
- dispatcher: 3041b293ec5150c96abf17588c1437a5fd8a9c84551d4e880e1b72bb1ff9cc8d
- service: 60e41936bfe52ed49dac7e77f2948ac3f8ed20dbae09a385053430f2f8689897

The frozen index-plus-overlays snapshot is not exact staged-tree acceptance.
Manifest files stayed unchanged; QA made no shared source/index changes. Dashboard
candidate is excluded and remains held. Authorization/lifecycle guards are fixtures,
not real-JWT or production-default mounting proof. QA cross-task stop used a
different synthetic identifier, not two simultaneously active tasks. Deterministic
blocked seams are finite coverage, not exhaustive scheduler/race proof. Blocking
callbacks still require cooperation; stop cannot preempt or undo effects already
issued, or grant recovery/cleanup authority. No staging or publication.

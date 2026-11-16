# Conversation send/stop request authority

Reproduced using the original conversation runtime with JWT routing, temporary
JSONL and inert provider: revoking the outer Router grant during the resource
resolver, activity factory or preflight provider resolver still accepted and wrote
user/result activities and transcript while calling the provider. Conversation
source authorization alone was not the original HTTP authority.

Registered POST messages/stop routes now retain their original Router checker and,
for AuthService, its owner/method/store/codec/key/clock identities. Request/header,
service/dependency and selected conversation ID/agent/profile bindings are checked
again after callback-bearing preparation. An optional submit authorize callback
checks immediately before queue admission/user persistence. Existing direct queue
callers without it retain their existing host-owned contract. Direct service.handle
without registration does not invent a Router/JWT authority.

Execution checks occur through explicit request-bound resolver/gate and transcript
begin/current/commit wrappers, before and after underlying callbacks. Reused durable
result loading also reauthorizes. Tool callbacks have an actual effect-entry fence;
authority loss after invocation retains uncertainty rather than undoing the effect.
Original provider complete/stream entry and every streaming iterator advancement
are fenced too. Iterator cleanup still runs when authority is denied. After durable worker admission, any observed authority loss in shared admission
checks stays sticky for this request, including outer streaming/live fences, so cleanup that
restores a grant cannot convert it into a normal transcript commit. Cancellation
alone remains distinct from this authority-loss state.

This does not assume ChatAgent calls an external
token.check (it observes cancelled). Stop reauthorizes after selection before
cancellation; it does not pretend a subsequent denial can undo cancellation.

Pre-admission refusal does not write user activity or call a provider. After durable
user admission, loss of authority becomes failed/unresolved execution, not rollback
or a fresh authorization denial claiming no effects. User write acknowledgement
failure returns persistence-uncertain and retains ownership. Transcript begin/commit
may already have persisted when their post-callback checks fail; no automatic retry
or success is inferred. Terminal failure activity remains the queue's existing
owned settlement, not a new authorized model turn.

Scope is POST send/stop. Existing GET activity read routes are unchanged and are not
claimed to have this receipt or post-header protection. This finite boundary is not
atomic revocation with arbitrary callback mutations, storage commit or provider
execution. Trusted host callbacks and existing single-process ownership assumptions
remain; no default runtime mounting or production readiness.

Thirteen new unit tests using real assembly/temp JSONL or original inert provider fixtures exercise all three revocation seams,
request/router/source changes, denied live stop, committed user revocation and ack
failure, transcript begin and committed-final transcript revocation, tool effect-entry and streaming entry/advance/cleanup seams. Ninety combined
conversation/queue/task-drain tests pass. Independent review cleared service 6bfa421c
with 85 focused tests and independent clock-threshold probes. The ordinary streaming
contract assumes normalized provider generators/iterators whose __iter__ is pure.
iter(provider.stream(...)) obtains the iterator before the post-factory authority
check; this is not a containment guarantee for arbitrary effectful __iter__ methods
or hostile host callbacks. No broader provider protocol guarantee is inferred.

QA240: `/workspace/shared/artex-qa/conversation-auth-240/REPORT.md`.
Manifest SHA256:
`7d73170653577a0fcb3db1952b10f0d9fc6b441d57f2b9462269dd6c6683d425`.
33 actual signed-JWT/PostgreSQL/temporary-JSONL/loopback-HTTP scenarios and 91 focused
tests pass. Earlier resource/activity/provider JWT/router revocations, partial user
and transcript effects/replay, denied stop, tool effect-entry/uncertainty and
stream/nonstream probes were rerun. The calibrated stream sweep covers all 13 clock
ticks 12..24 from provider entry through last iterator advance, including intervening
external StreamingCompletion context/live fences. Every revocation preserves the
admitted HTTP202 but leaves execution unresolved, with one durable user transcript
entry and no normal commit/drain. A started iterator closes exactly once; cleanup
restoring the JWT clock cannot clear sticky loss. Before-entry denial creates no
iterator. Partial stream and nonstream effect-restoration probes remain uncertain.

QA237 is preserved nonfinal with a stale fake Queue fixture missing the optional
authorize argument; its integration observations do not erase focused failures.
QA238's earlier tool-fenced candidate remained nonfinal due to the subsequently
reproduced stream-entry gap. QA239 remained nonfinal because external live-fence
loss could be cleared by cleanup. QA240 is frozen index plus explicit overlays,
not exact staged-tree acceptance. All three source/test-fixture hashes match.

Providers/iterators and signed JWT clocks are inert synthetic fixtures; SQL and
JSONL/HTTP boundaries are real. No external model/network effects were attempted;
tool markers stayed in disposable files. Threshold calibration covers this exact
execution path, not arbitrary scheduling, hostile host code or every provider
implementation. GET conversation reads remain excluded. Callback timeouts are not
preemption. Held dashboard unchanged; no staging/publication.

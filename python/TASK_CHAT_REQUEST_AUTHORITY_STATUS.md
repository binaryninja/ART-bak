# Task chat request-authority admission correction

Existing explicit TaskChatService could return202, persist user/result activity,
write a transcript and invoke its provider after its request authorizer was revoked
during prepare. The same defect was reproduced in the activity factory and
transcript.check_scoped callback. Core.current remaining true did not imply the
independent HTTP request grant remained valid.

The service now captures the request value, selected task, resolver, authorizer and
dispatcher. A ticket-bound callback re-resolves the same selection, checks the
independent request grant and rejects changed bindings/request data. It runs after
prepare, after submit factory/transcript checks, and inside activity projection
after lifecycle/current callbacks immediately before the activity sink. The real
issued queued ticket carries this callback into execution start, transcript
admission/current/commit, provider resolution and tool-grant boundaries. Direct
host submissions without an HTTP request keep their existing host authority.

This does not make SQL, JSONL, callbacks or policy revocation atomic. Callbacks are
cooperative trusted host adapters; mutually mutating policy cycles, concurrent
revocation after the last observation and revocation during an effect cannot be
rolled back. A callback inside a persistence operation can already have written.
Existing queue uncertain ownership is retained, including when a request is
revoked after user activity acceptance. Such work can have returned202; execution
then refuses rather than fabricating completion. Write-then-error remains503
persistence uncertainty, not a claim of no durable write. No new cleanup authority.

Ten new tests cover three pre-admission revocation seams with zero activity,
transcript and provider effects; changed request/ambient selection; revocation
after actual user persistence; write-then-error; and explicit false ticket-authorizer denial. All48 task-chat tests pass.
Independent review and scoped QA220 passed; evidence and limits follow.

No route is mounted by default. This is not a runtime chat ownership adapter,
main-segment authority, namespace proof, launch path, fake idle status or
automatic recovery. Registering on a Router now captures that exact router's
original authorize callback and carries it with the frozen transport request into
the admission/execution ticket checks. Replacing the callback is refused, rather
than trusting a new permissive callback. Ordinary revoked/expired router grants
are refused before persistence, or retain uncertainty if revocation follows an
actual accepted user write. Two additional tests cover outer grant revocation,
callback replacement, and revocation after durable acceptance.

Direct handle() calls without registration have no implied transport authority;
those hosts must supply their own authenticated transport. The repeated router
check uses its actual configured policy, not an invented JWT implementation.
No postheader status-disclosure guarantee or mutually mutating-policy fixed point
is claimed. Earlier QA217/218 do not cover this new router-authority correction.
No staging or publication.

Registered status and stop use the same pinned authority after selection callbacks
before dispatcher inspection or cancellation. Status repeats the check after the
running callback before returning its body (not an actual-header receipt). Stop
does not claim to undo a cancellation already issued if policy changes during the
cancellation callback. Regressions include a genuine active token that must remain
uncanceled when its stop request loses outer authorization. QA219 is nonfinal
because it preceded this status/stop correction.

## Independent review and QA220

Independent read-only review cleared the finite pre-effect/pre-return contract.
Five additional genuine active-token denied-stop probes covered outer grant
revocation/replacement, task grant revocation, selection change and request
mutation: all denied without canceling the token. No universal race-freedom claim.

Report: /workspace/shared/artex-qa/task-chat-admission-220/REPORT.md
Manifest SHA256:
7b10c9f6858cb467966ca7f402c48b019f8f1bc72f2b8ec7fdb0c5f67c20cb84

Nineteen actual isolated PostgreSQL/filesystem/admission scenarios and 48 focused
tests passed. Sixteen scenarios used loopback HTTP; three used direct dispatcher
authorization. Send-grant and outer-router revocation before admission denied
without user activity, transcript writes or provider invocation. Genuine success
persisted activity and transcript. Actual user commit followed by revocation
remained202 with unresolved retention/no provider; commit acknowledgement loss
remained503 persistence uncertainty, not rollback.

Live blocked-job status/stop tests denied resolver-time outer revocation with zero
running/cancel calls and an uncanceled token. Running-time revocation withheld
status after one running call. Released original jobs persisted and drained.
QA217–219, including QA218 before router continuity and QA219 before status/stop
checks, remain preserved nonfinal evidence, not final acceptance.

Verified source SHA256:
- core: 6c50254d6b789226fa4e990c9201bd47061092b277be806c3a2bb0808bddb60f
- dispatcher: c1a1819222836cd14a48224c93fbf7a438514665710d3c217beac2fdc9d36ce2
- service: 873ca64b213f598372a52f1be651a2d4ff4ad1759ff40d1a2429c0efce9bbb97

The snapshot used index plus explicit overlays, not exact staged-tree acceptance.
Static synthetic Router grants and fixture lifecycle guards are not real JWT
expiry or production-runtime assembly verification. Callback rebinding/request
mutation were focused tests, not network injection. Status authorization is
pre-return, not postheader. Already issued cancellation or writes are not rolled
back. No production ownership, launch, deployment or publication authority follows.

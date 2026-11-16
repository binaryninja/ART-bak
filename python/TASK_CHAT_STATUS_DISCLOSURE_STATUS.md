# Existing task-chat status disclosure receipt

Status-only extension to the explicit TaskChatService: successful status now
returns the existing DisclosureResponse with its pinned admission callback.
The serialized running value remains an observation from one dispatcher.running
call, not a claim it stays current. Disclosure re-resolves the same selected task,
checks service request authority and original registered Router authorization,
and rejects mutated request/service/router bindings without recomputing status.

Existing WSGIApplication validates the receipt when emitting the single body.
Existing RuntimeListener _AuthenticatedView additionally validates it after the
real start_response callback when headers were deferred. Revocation after headers
withholds the body rather than appending an error to a200 representation. Plain
WSGI iteration raises a sanitized disclosure exception; gateways must close the
iterator. Headers may already have been sent, so no replacement403 is promised.
No new transport implementation, default route mount or runtime chat owner exists.
Send and stop response/action semantics are unchanged.

Four new tests exercise real AuthService signed-JWT expiry, actual-header task/
selection/router/service-binding revocations, selection callbacks revoking final
task/JWT grants, one-time running observation and plain-WSGI sanitized refusal.
All 52 task-chat tests pass. Independent review and scoped QA221 passed.

Finite callback ordering is selection observation, task grant, original Router
policy, then pure binding checks. This is not an arbitrary mutually mutating-policy
fixed point, atomic database/network revocation or protection from malicious host
code. Direct unregistered handle calls still have no invented transport policy.
No production assembly, browser behavior, launch or namespace authority is proven.
No staging or publication.

## Independent review and QA221

Read-only independent review cleared the status-only receipt: 57 combined tests,
four additional final task/transport/exception probes, and plain-WSGI single-use
and close-before-iteration checks passed. Send/stop behavior remains unchanged.

Report: /workspace/shared/artex-qa/task-chat-status-221/REPORT.md
Manifest SHA256:
cc00e613961b3df29588e3d903b644e439817d2c0f1a2873617ec2e30cfe6cb6

QA221 passed 28 isolated actual PostgreSQL/filesystem/HTTP and exact-transport
scenarios plus 52 focused tests. It retains all 19 QA220 admission/control/partial
write scenarios, adds eight deterministic receipt probes against an actual blocked
core/dispatcher with PostgreSQL activity, and adds signed-JWT loopback success.
The receipt probes cover valid runningTrue, actual-header task/selection/Router/
JWT-expiry/dispatcher-binding revocation, and final resolver revoking task or JWT
authority. Revoked cases send200 headers but yield zero body, with running called
exactly once. Original blocked jobs later finish and persist terminal activity.

The eight header probes invoke the exact WSGIApplication + _AuthenticatedView
start_response path directly; they are not socket-scheduled race experiments.
Seventeen scenarios use actual loopback HTTP and three direct dispatcher calls.
Separate loopback signed-JWT status succeeds200/runningFalse. JWT uses a synthetic
local key and controlled clock, not live credentials. Guards/assembly are fixtures,
with inert providers and no production/default-mount proof.

Verified service SHA256:
d8ee141691c673ebe46154dc5147b10f3fb82dc636b72b7f48c57bd5dbeee52d
Core and dispatcher remain the QA220 versions. The frozen snapshot is index plus
explicit overlays, not exact staged-tree acceptance. All manifest entries remained
unchanged after testing. No code/index change or publication was performed by QA.

This is finite cooperative observation coverage, not exhaustive race freedom,
an atomic status snapshot, arbitrary policy fixed-point convergence or production
runtime assembly authority. Send/stop effects are not retrospectively rolled back.

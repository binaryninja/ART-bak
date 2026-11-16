# Optional authenticated literal tool-policy mutations

`bind_tool_intercept_mutations` explicitly wraps an original WSGI application
with JWT-protected rule create/update/delete/toggle and tool-configuration writes.
It requires an exact policy owner, declared interceptor caches and an exclusively
owned PostgreSQL executor. Original routes remain unchanged; overlapping mutation
routes refuse binding. No route or execution guard is enabled by default.

Timestamp and configuration-digest CAS, request authorization, independent global
permission and original resource identities are checked through the existing
literal-policy mutation path. Regex/judge configuration, approval decisions and
task-runtime interception are not added. Successful writes reset declared caches;
this is not distributed cache coherence.

An unknown write/acknowledgement outcome quarantines the original shared owner,
store and HTTP wrapper. A service postcommit denial returns 503 with `retry:false`;
a failed response-start callback or post-header revocation raises without returning
the body. Retrying or binding another wrapper cannot bypass quarantine. This does
not undo committed writes or supply reconciliation. Prewrite 400/403/409 refusals
remain recoverable. Finite authorization fences assume cooperative callbacks,
not a fixed point over mutually revoking policies; returned bytes do not prove
network delivery.

## Isolated acceptance

Stage150 manifest SHA256:
`8082a0b548f1ab25779d73b2b00d5c515f43fe3253b8982095ea92a95a9c934a`.
Wrapper SHA256:
`6c179bdc40f2e95711d92787ee8c7362f3d9b03bc075e82700dde0f86737b91b`.

Four fresh original-schema PostgreSQL17 groups pass: authenticated CRUD/settings
and CAS refusals; service postcommit authority denial; response-start failure and
post-header revocation; native commit followed by deliberately injected driver
acknowledgement loss. The uncertainty cases retain the committed row, prevent
replay and refuse rebinding. The last case is controlled fault injection, not an
observed network failure. 251 intercept-related tests pass on the frozen snapshot.
An earlier command selected a nonexistent test module; its retained import error
was corrected by discovery, not by changing product source.

Tests use synthetic rules in a credentialless filesystem/user/network namespace
with loopback-only PostgreSQL. No real tools, external targets/providers, deployment,
production listener or default activation were exercised. This is bounded evidence,
not an exhaustive race or production-readiness guarantee.

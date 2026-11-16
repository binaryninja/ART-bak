# Conversation metadata list disclosure

The existing GET /api/conversations returned private titles after list SQL or the
running-state resolver revoked the original Router grant, and again after actual
response headers. These three callback seams were reproduced before changes.

The registered list handler now retains the original Router authorizer and, for
AuthService, its original method/store/codec/key/clock resources. Request/header,
store/executor/list method and running resolver bindings are pinned for the read.
Authorization is checked before and after list SQL, after running observations,
after DTO serialization, and through a retained actual-header disclosure receipt.
Final pure binding checks follow the authority callback. Failed reads reauthorize
before sanitized errors. An authority loss after headers withholds the body;
already-sent headers cannot be replaced.

This preserves the existing workspace-wide Router permission: it does not invent
or infer per-conversation history grants. List SQL executes once, and each running
value is observed once. No snapshot requery or idle inference is added. Empty lists
still need no running resolver; nonempty lists require actual boolean observations.
The existing unpaged list shape/order and mutation handlers are unchanged.

## Verification

Independent review passed 46 focused tests and additional SQL/running denial,
actual-header refusal, final-authority binding mutation and signed-JWT expiry
probes. No blocker was found within the global-list authority and finite-pin scope.

Fresh QA244 report:
`/workspace/shared/artex-qa/conversation-list-244/REPORT.md`

Frozen manifest SHA256:
`6207e1de7e6ef8f5f3a0d7f01a9ddcfdfead066b4fdbcacfba8e1ad0debb587f`

35 actual PostgreSQL/HTTP/transport cases and 18 focused tests passed. Disposable
PG17, real schema/ExecutorPool/ConversationStore and original signed JWT returned
two synthetic private titles with supplied true/false running values over isolated
loopback HTTP. Exactly one list SQL and one running callback per row executed.
Post-list-SQL and running callback expiry/Router replacement returned403 without
titles. Twenty-six actual-header/final-JWT-clock probes withheld every body byte
following200 headers, including original request, store/executor/list/running and
AuthService resource/method replacement. These receipts did not repeat list SQL
or running observations. Header probes used the exact WSGI/_AuthenticatedView
callback seam, not a claimed network scheduling race.

HEAD, unsupported-query-before-SQL refusal and empty/nonempty lists without a
running resolver preserved their existing behavior. The complete current-tree
suite also passed 4,113 tests. Mutation routes were unchanged and are not newly
accepted by this read-only QA run.

Limits: existing list SQL remains unbounded in rows/text and encoded response size.
This is authorization hardening, not a transfer-bound or pagination change. The
result is an observed snapshot, not a live selection or atomic DB/network view.
Finite cooperative callback checks and original resource pins do not contain
arbitrary host-code mutation, synchronize revocation or establish production
ownership/readiness. No default assembly, write capability, merge or deployment
is added. Held dashboard and private/research files remain untouched.

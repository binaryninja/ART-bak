# MCP HTTP port boundaries

Implemented from `mcphttp/client.go`: names, RPC envelopes/errors, initialize and
initialized notification, session header propagation, buffered JSON/SSE response
selection, tool metadata/default schemas, concatenated text call results, and
best-effort session DELETE. Tests exclusively use injected in-memory transports.
No default transport exists and importing/constructing the client performs no I/O.

Intentional hardening differences: reject malformed header tokens/values and
case-insensitive duplicates; reserve protocol headers; match JSON response IDs and
versions; require exactly one of `result` or `error` (a null result is valid but a
null error is not); reject cross-origin legacy endpoint announcements; suppress
server HTTP error body snippets; bound buffered responses to 8 MiB. JSON rejects
non-JSON NaN/Infinity constants, matching Go's decoder. SSE multiline data uses
standard newline joining (Go concatenates without a newline), recognizes only
CR/LF/CRLF as line endings, preserves Unicode separators inside JSON strings,
and ignores one initial UTF-8 BOM. Both buffered parsers still flush a final data
frame at EOF. Server requests are skipped even when their ID matches the pending
client request; they are not dispatched or answered by this buffered client.
Calls are serialized. Tool calls are not retried.

A session is adopted only from a successful, validated `initialize` response,
before sending `notifications/initialized`. RPC errors, malformed responses,
ordinary calls, and notifications cannot establish a session. An established
session is not replaced by later response headers. This tightens Go's broader
first-header assignment and the earlier Python implementation, which could latch
a session before the reply was parsed. If the initialized notification fails,
the valid assigned session remains available for best-effort `close` cleanup.
Initialization result schema/version negotiation remains unimplemented; validation
here covers the JSON-RPC envelope, not the method-specific result schema.

Not complete/live-tested: long-lived legacy SSE GET/pending-request pump,
cancellation/timeouts, actual TLS/HTTP transport, SSE reconnect/resume, protocol
version negotiation, tools/list pagination, session expiration/reinitialization,
Norma CoreTool and AskUser enforcement, output Capture/truncation/spill-to-disk,
structured/non-text output adaptation. `permission_prompt` is metadata, NOT an
authorization check. `call` deliberately mirrors Go's low-level backend bypass;
agent routing MUST add an enforcing permission adapter before exposing it.

Any future transport must enforce endpoint authorization, SSRF policy, redirect
and DNS-rebinding protections, credential scoping, TLS verification, deadlines,
and streaming byte limits before buffering. Same-origin URL validation alone is
not SSRF protection. Response byte checking here occurs after transport buffering.
No insecure-TLS option is supplied. No live endpoints were contacted.

# Opt-in Python development SSE bridge

Combined integration baseline: `8285047d639cc9123e12555f939062ac3896d400`.
This includes the separately committed explicit frontend-origin, process-signal
drain and admission-seal changes. This candidate adds only the six frontend
development/test/documentation files; it does not change those backend contracts.
The earlier bridge checkpoint and its test scope are retained below. Aggregate
combined-tree validation does not turn the unrun Next/browser/Python-stream
integration gates into passes.

This is an experimental **development-only** transport candidate, not a backend
launcher, deployment, general HTTP proxy or browser-readiness claim. Default
`npm run dev`, Next configuration, build and static-export behavior are unchanged.
Do not activate this candidate until its independent review is complete.

## Why a separate development host

The actual frontend uses relative `/api` requests, but `sseUrl` defaults to direct
port 8787 in development. The Python listener does not provide permissive CORS.
The source also warns that Next's ordinary API rewrite can buffer SSE. This host
avoids that rewrite **only** for these existing, bodyless GET stream routes:

- `/api/logs/stream`, with optional `since` and `token`.
- `/api/exploration/activity/stream`, with required `task`, optional `since` and
  `token`. No alternate session/intent selector is supplied by this frontend.
- `/api/side-questions/<request ID>/events`, with optional `token`. IDs use the
  backend's bounded ASCII ID alphabet.

All other requests still go to Next's original request handler. This host does
not add API capabilities. In particular `/api/update/stream` is not an implemented
Python feature and is not intercepted. Encoded/normalized alternative SSE paths
are not recognized by this bridge; no new normalization or redirect is performed.

An ordinary request-dependent Next route handler would change static-export
compatibility, so no app route is added. Next documents the optional custom-server
entry pattern separately from static exports. See [custom servers](https://nextjs.org/docs/pages/guides/custom-server)
and [static exports](https://nextjs.org/docs/app/guides/static-exports).

## Explicit setup, once the prerequisites are available

1. Install the unchanged locked frontend dependencies through an approved route.
2. Independently assemble the existing Python runtime and its permitted read
   services. This host does not create keys, seed a database, choose providers,
   grant scopes, start a pump, or make unsupported streams available.
3. The backend must use the separately reviewed explicit origin candidate:
   `frontend_origin='http://127.0.0.1:3000'`, retaining its literal-loopback Host
   and auth checks. Use the actual selected frontend port, not a wildcard.
4. Explicitly invoke from the repository root:

   ```sh
   node web/dev/python-dev-server.mjs --frontend-port 3000 --backend-port 8787
   ```

The host binds only `127.0.0.1`; open that exact numeric-loopback origin, not the
`localhost` alias. Both explicit ports must be different and 1024..65535. The
selected backend process is a trusted destination. No DNS, incoming destination,
environment proxy, remote target, redirect-following or automatic retry is used.

Before loading Next, this invocation sets process-local Python profile,
`NEXT_PUBLIC_SSE_BASE=''`, the fixed numeric-loopback `AUTOPENTEST_API`, and disables
telemetry. It refuses mock, production and static-export modes. It does not edit
environment files, alter the default npm scripts, or start the backend. Upgrade
tunnelling, WebSockets, HMR and general custom-server compatibility are not
supported claims; this host closes HTTP upgrade/CONNECT attempts. Use manual
refresh for this development acceptance path.

## Authority, data and framing

The frontend Host must exactly match its configured numeric-loopback authority.
If Origin is present it must exactly match that frontend origin. Missing Origin
is not an authentication signal and retains the existing development behavior.
Duplicate authority/auth/framing headers, unsupported body framing, upgrade,
Expect, request trailers and Connection-nominated headers are rejected. URLs and
headers are bounded. Normal Next requests also have a 1 MiB declared-body limit;
chunked requests, oversized uploads and Server Action compatibility are not
promised by this deliberately restricted host.

For the three recognized SSE families only, the bridge forwards the original
query, Authorization, **complete Cookie header**, Origin and Last-Event-ID to the
one configured backend. Cookie/query/bearer values are never created, exchanged,
parsed as identities or elevated. Passing all existing auth carriers unchanged
preserves the backend's precedence, including invalid bearer refusal despite a
valid cookie/query token. The selected backend must be trusted with these existing
credentials, just as for the ordinary API rewrite. They are not sent to a separate
frontend upstream, redirect target or logger. Unknown, forwarded, proxy-auth and
hop-by-hop headers are not forwarded. Backend Host is generated for its explicit
port. Query token validation and authorization remain the backend's job.

Only a 200 `text/event-stream` identity-encoded response is streamed. A 204 ends
cleanly. Error statuses remain errors with a generic local body; redirects or
unexpected success/content encodings produce 502 without following a Location.
Set-Cookie, Location, CORS and arbitrary upstream headers are never returned.
No browser cookie setting, JWT generation, CORS enablement or auth endpoint is
implemented. CORS absence is not a guarantee against request-side effects.

## Streaming and resource bounds

The bridge flushes headers and forwards chunks without waiting for a full SSE
frame or EOF. Native stream backpressure is preserved. Client disconnects,
upstream errors, shutdown and limits close the active upstream, release admission
and do not synthesize a completion/error event into an established SSE stream.

Default bounds: four simultaneous SSE requests, 16 KiB request/upstream headers,
20,000-character target, 64 MiB total response bytes, 30-second header and idle
timeouts, and five-minute absolute stream lifetime. The 30-second header wait
allows the Python empty log stream's 20-second first heartbeat. Connection/limit
closure is a transport interruption, **not** task completion. The browser may
reconnect; Last-Event-ID is forwarded unchanged, not invented. Coordinate the
stream cap with the backend's connection capacity so other API requests retain
slots. The frontend host additionally caps 32 connections, configures Node's
five-second header and ten-second incoming-request timeout values, and disables
keepalive reuse. Node checks those values periodically; they are not verified
exact wall-clock deadlines. Shutdown callers share one completion promise that
waits for both server and Next cleanup. Signal handlers are removed once stopping
begins; cleanup failures are caught and reported without underlying error details.
Next cleanup remains cooperative, not a promised hard shutdown deadline.

## Verified scope and remaining gates

At base `55c5feb5bc1a39fcc68bc92253086e75d5b57d3e`:

- Thirteen dependency-free tests execute the real bridge with synthetic stream
  boundaries: exact path/query selection, raw duplicate headers, Host/Origin,
  auth/cursor preservation, unknown header removal, first-chunk delivery,
  response-header filtering, redirects/errors, concurrency, disconnect before
  headers and during backpressure, byte/time limits and sealed shutdown.
- Four real loopback HTTP fixture tests exercise first-frame delivery before
  EOF, exact fixed-upstream/auth/cursor forwarding, raw duplicate-header denial,
  response filtering, no redirect follow, and upstream closure on disconnect.
  They use synthetic credentials and an inert Node fixture, not a Python runtime,
  Next server, browser, provider, database or external target.
- Five dependency-free lifecycle tests cover shared in-progress/repeated shutdown
  completion, waiting for both resources, cleanup failures, signal-handler removal
  and sanitized failure reporting. They use fake cleanup callbacks, not an actual
  Next import, app startup or shutdown.

Run these separately so the socket requirement is visible:

```sh
node --test web/tests/python-sse-bridge.test.mjs
node --test web/tests/python-sse-bridge-http.test.mjs
node --test web/tests/python-dev-lifecycle.test.mjs
```

Actual Next compilation/custom-host startup and browser EventSource/credentials/
reconnect behavior remain unrun because frontend dependencies are unavailable.
The dynamic Next import is deliberately not replaced by a fake claim of a running
app. Python-runtime stream acceptance through this bridge also remains separate.
The existing static export and broader frontend gates are still open.

No merge, publication, deployment or real runtime activation is implied.

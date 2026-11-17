# Explicit loopback frontend origin

The existing Next development rewrite forwards `/api/*` to the backend on a
different port. A browser POST carries its frontend Origin even if a proxy rewrites
Host to the backend. The existing strict RuntimeListener rejects this shape with
403 before auth/route execution. A real loopback preserving-Origin test proxy
reproduces this boundary; it is not evidence of actual Next.js execution.

## Narrow opt-in, unchanged defaults

`DevListenerConfig(frontend_origin='http://127.0.0.1:3000')` permits that one exact
additional Origin. `prepare_development_runtime(..., frontend_origin=...)` passes
the same separately supplied, code-owned authority. It is not accepted in tuning
JSON, inferred from environment, or selected from incoming forwarded headers.
Invalid values fail before assembly SQL, lease claim or listener startup.

Only canonical `http://localhost[:port]` or `http://127.0.0.1[:port]` are accepted.
Ports are 1..65535; browser-serialized port 80 omits `:80`. There are no DNS aliases,
IPv6/LAN hosts, alternate numeric IP encodings, wildcards, credentials, URL paths,
query/fragment, multiple origins or trailing slash. `localhost` and `127.0.0.1`
are distinct origins, not interchangeable aliases. Omission preserves the prior
strict behavior, including rejecting the frontend's cross-port Origin.

The backend Host still must match the existing literal backend loopback authority
and configured backend port. A frontend Host, rebinding domain, foreign/null/other
port Origin or ambiguous duplicate framing/auth/cookie/origin headers is refused.
Forwarded/X-Forwarded-* fields are ignored, never authority. Existing bearer,
cookie and query-token verification, route-level permissions and provider gates
are unchanged. Selecting an Origin does not supply a JWT or execution permission.

The host must trust the entire selected frontend origin. This accepts requests
from that origin whether proxied or directly addressed to the backend; it does
not authenticate the proxy process. HTTP Origin is a browser protection signal,
not proof against non-browser clients. A local process can forge headers.

## Browser and cookie limits

No CORS headers, OPTIONS/preflight handler, credential change or cookie attribute
change is added. Browser response access to a direct cross-origin API/SSE request
is still restricted. **Absence of CORS does not prevent a simple cookie-bearing
request from executing.** The explicit origin choice is the host's transport
authority for that frontend, not full CSRF protection. Missing Origin is accepted
as before; this change does not solve legacy absent-Origin/cookie attack surfaces.

The frontend uses relative API fetches, bearer tokens from localStorage and an
`artex_token` cookie. The actual API already accepts that cookie without bearer
auth. Tests verify cookie-only acceptance at the selected origin, bad-cookie
rejection, and invalid-bearer precedence even with a valid cookie. They do not
simulate browser cookie storage, SameSite, credential modes or localStorage.

Development `sseUrl` currently defaults directly to `:8787`, which still needs
CORS for browser-readable SSE. The existing explicit `NEXT_PUBLIC_SSE_BASE=''`
selects a same-origin path, but Next streaming/buffering and real browser reconnect
must be tested separately. This change does not claim SSE/browser readiness or
full frontend compatibility, and does not silently alter the frontend defaults.

## Validation

Five new tests plus one assembly test cover canonical/invalid configuration,
original Host authority, malicious forwarded/foreign/null/alias origins, repeated
headers, original default refusal, authenticated proxy POST acceptance, cookie
and bearer precedence, absent-Origin legacy behavior, absent CORS and unavailable
cross-origin preflight. The proxy tests use actual loopback sockets and inert
stores, never a real database, provider or target; no pump is started.

Focused run: 30 tests passed across runtime frontend origin, development assembly,
existing listener and foreground suites. Independent review found no blocker in
this declared contract and separately reran 25 tests. The full Python suite passed
4,144 tests. These counts are not frontend or runtime feature parity. Real Chromium
could not start in this executor: process
singleton socket creation failed with `Operation not permitted` before any page
was opened. No browser security bypass was attempted. Actual Next/browser cookie,
SSE and task completion acceptance remain open gates, not implied by these tests.

No executable full-runtime CLI, runtime recovery, public binding, deployment or
merge is introduced. The legacy metadata-only CLI is unchanged.

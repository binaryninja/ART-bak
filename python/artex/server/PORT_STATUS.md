# Server port status

This package began as an **offline contract core** and now includes explicit opt-in
WSGI application/runtime compositions. These factories do not launch a listener.
The separate existing `artex.__main__` dev CLI explicitly serves the persisted
metadata/CRUD app on IPv4 loopback; it does not compose runtime providers, pump
or frontend. CLI tests mock its socket boundary, not actual listener acceptance. The initial
health/auth foundation was based on
`server/auth.go` and response/routing/health contracts in `server/server.go`.
The original foundation tests used memory-only stores. Later component acceptance
includes explicitly approved isolated Go/PostgreSQL and inert provider fixtures;
see the dated QA evidence, not this historical foundation list, for those checks.
No production listener or live-provider readiness is claimed.

Implemented:
- Request/JSON response/error envelopes and bounded JSON-object decoding.
- Explicit method routes, single-segment named parameters, static-route
  precedence, method errors, and fail-closed authentication/handler errors.
- Bearer, cookie, and SSE query token extraction in upstream priority order.
- Seven-day ARTEX HS256 tokens and signature/expiry/not-before validation.
- Health and auth status/init/login/change-password contracts using injected
  password-store and password-codec protocols. Initialization requires atomic
  insert-if-absent; database failures never imply an uninitialized password.
- Fourteen isolated tests with memory-only stores and a clearly test-only codec.

Intentional hardening differences: HS256 only (upstream accepts the HMAC family),
expiration mandatory, signing keys at least 32 bytes, only explicit public routes
(no broad `/api/auth/*` exemption), English errors without exception details.
Password changes retain upstream behavior: already-issued tokens are NOT revoked.

Current scope supersedes the original five-route prototype: the static inventory
records **193 unique method/path registrations** (214 declarations, including
15 alternatives), not 193 live or behaviorally equivalent endpoints. Optional
services require explicit adapters, grants and lifecycle ownership; absence may
mean an unregistered route or a fail-closed 503. See `ROUTE_PARITY.md` and
`DYNAMIC_TASK_READ_STATUS.md` for current composition and evidence.

WSGI, PostgreSQL-backed contracts, bounded SSE replay and narrowly authorized
filesystem/multipart operations now have separate tests and optional composition.
The historical statement that no routes beyond auth/health exist is obsolete.
These additions do not establish complete Go ServeMux semantics, WebSockets,
production listener/CORS/CSRF/rate-limit configuration, credential deployment,
full frontend parity or restart recovery. Explicit manual-observer timed profiles
have separate original-schema acceptance; see TIMED_ADMISSION_CANDIDATE_STATUS.md. No deployment or
live-provider readiness is inferred. See the component status documents for
specific tested boundaries. Do not use the test codec as password storage.

# Server port status

This package is an **offline contract core**, not a runnable web server. Based on
`server/auth.go` and response/routing/health contracts in `server/server.go`.
No upstream executables, model calls, tools, network listeners, or external
services were invoked during development.

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

Not implemented: real bcrypt adapter; PostgreSQL settings adapter; secure JWT-key
storage/migration; HTTP listener/framework/ASGI/WSGI adapter; Go ServeMux path
canonicalization, HEAD handling, or complete overlapping-pattern semantics; SSE,
WebSocket, multipart uploads, CORS/body timeouts/rate limiting, cookies/CSRF
policy, and all application routes beyond the five health/auth contracts.
No engine, provider, filesystem, proxy, agent execution, or tool integration.
Adapters must reject ambiguous duplicate authorization headers and define URL
normalization before dispatch. Production deployment needs security review and
end-to-end parity tests. Do not use the test codec as password storage.

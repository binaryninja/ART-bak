# Development runtime listener composition

## Existing source

`artex/__main__.py` already provides explicit `--check-config` and `--serve` modes,
requires externally supplied DSN and stable signing key, checks psycopg/bcrypt,
and serves `build_persisted_application` on literal `127.0.0.1`. It suppresses
request logs and never starts providers or a task pump. Its `test_cli.py` coverage
patches database/socket boundaries; no actual socket claim follows from it.

`server/task_runtime_setup.py` already constructs one authenticated runtime,
manager/admission owner and inert EnginePump. `TaskRuntimeAssembly` requires
actual persisted role selection, durable transcripts, explicit prompts/tool
catalogs and host grants. `web/next.config.mjs` proxies `/api/*` to explicitly
selected `AUTOPENTEST_API` (default localhost:8787); frontend hosting is separate.
Go `cmd/artex/main.go` starts a broader manager, proxy, update/bootstrap and server
lifecycle; reproducing all those side effects is not a minimal Python launcher.

## Explicit input boundary

The code-owned, programmatic `RuntimeListener` accepts an exact existing
`TaskRuntimeApplication` bundle and frozen validated development transport limits.
Do not add an arbitrary `--factory module:function` import, ambient model-key
lookup, permissive policy factory or implicit runtime construction. Validate all
resource identities before opening a socket. A check-only path must remain inert.

Explicit host inputs before constructing that bundle:

- Same exact bounded ExecutorPool for HTTP, pump and stored role providers;
  existing compatible schema/catalog/profiles, no automatic migrations or seed.
- Stable signing-key bytes and bcrypt codec; no generated default password/key.
- Approved transcript root and ownership/lifecycle guard, prompts and graph tool
  catalogs, input/scope/asset execution grants and policy revision.
- Provider transport construction with explicit origin allowlist/TLS, timeouts,
  model/profile-read grants and no hidden global profile fallback.
- Independent dynamic read/source/raw/asset/connector/global disclosure policies.
- Shared capacity, explicit pump start choice and shutdown/drain ownership.

Keep the existing metadata-only CLI separate until these policies have a concrete
checked configuration schema. Do not turn missing authority into `lambda: True`.

## Transport and lifecycle acceptance gates

Loopback-only literal address, bounded concurrency and socket/request limits,
quiet redacted logs, rejection of ambiguous duplicate authentication/framing
headers before WSGI normalization, and reliable iterable close on disconnect are
required. The current single-threaded dev server lets SSE delay control requests;
using a raw single PostgresExecutor concurrently is not a substitute for the pool.
No production TLS, proxy trust, browser origin or exposure guarantee is implied.

Do not autostart the pump in construction. Serving HTTP and starting owned ticks
are separate explicit actions. Shutdown must stop new admission, stop the pump,
cancel/drain the host and retain unresolved handles/leases before resources close.
A failed drain must report unresolved ownership rather than claim clean shutdown.
Positive runtime deadlines/recovery and non-graph tools remain unsupported.

Start with authored inert handler/lifecycle tests. Then request/coordinate approved
actual loopback HTTP plus disposable PostgreSQL acceptance, with fixed local
provider fixtures: no provider before an explicit tick, JWT-before-mutation,
concurrent activity/control isolation, disconnect cleanup and unresolved-drain
failure. No frontend execution or public network binding is needed for this gate.

## Implemented transport and verified acceptance

`server/runtime_listener.py` now implements the explicit bundle/pool/owner boundary
as `RuntimeListener`, `RuntimeResourceOwner` and `DevListenerConfig`. This does not
change the metadata-only CLI or provide a permissive resource factory. Independent listener tests use mocked sockets plus actual WSGI dispatch;
separate stage89 exercised real isolated loopback sockets and PostgreSQL.

The new transport pins the original bootstrap Router/JWT callback even when the
served app is a code-owned read overlay. It validates that authority before and
after response construction and each stream chunk, withholding a response or
closing an already-started stream on replacement. It does not call replacement
authentication callbacks. Header validation rejects repeated authentication and
framing headers and raw network-path targets before WSGI normalization.

Host must be `127.0.0.1:<configured-port>` or `localhost:<configured-port>` (bare
names only on port 80). If Origin exists, it must exactly equal `http://<Host>`.
Foreign, `null` and cross-port development origins are rejected. This deliberately
does not silently authorize Next port 3000 or provide CORS; same-origin serving or
an explicitly reviewed narrower development proxy/origin policy is separate work.
This addresses the local listener's rebinding boundary, not production browser
security or public exposure. Shutdown callback timing remains cooperative and
bounded by the caller's implementation, not a preemptive cancellation guarantee.

Stage89 passed four actual loopback/PostgreSQL groups and nineteen focused tests.
JWT task creation precedes explicit pump start; finite SSE coexists with pause/read
requests. Raw framing, network-path target, origin and size refusals were tested.
An outstanding HTTP callback retained resources during shutdown; releasing it and
using the explicit idempotent owner callback allowed close exactly once. This is
not a test of an ambiguously executing external tool. The initial owner fixture
was corrected to avoid pausing an already-paused handle; no product change was
needed. Manifest:
`f782a3eaadbefab54b4b8748a45a6e39923929057c96126d9878bed60136f305`.

The metadata-only CLI still does not construct these full runtime resources. This
programmatic transport is not a ready-made production deployment or full frontend
launcher. No public socket, live provider, real credentials or original frontend
execution was used.

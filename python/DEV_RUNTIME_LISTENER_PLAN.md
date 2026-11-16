# Proposed dev runtime listener composition (not implemented)

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

## Smallest coherent next component

Provide a code-owned, programmatic `serve_loopback` that accepts an exact existing
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

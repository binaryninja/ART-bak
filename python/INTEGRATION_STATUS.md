# Historical early integration checkpoint

Later implementations and live-driver verification are recorded in README.md and
ISOLATED_QA.md. Assertions below about then-missing adapters/testing describe this
early checkpoint, not current aggregate coverage.

The Python port remains incomplete and is **not** a drop-in replacement for the
Go service. The English UI is not yet connected end-to-end to a Python backend.

## Connected slices

- `db.settings.SettingsStore` satisfies the authentication `PasswordStore`
  protocol. Initialization uses a single parameter-bound `INSERT ... ON CONFLICT
  DO NOTHING`, not a racy read/check/write sequence. Executor writes must commit
  before success is returned. The concurrent fixture proves the adapter contract,
  not live PostgreSQL concurrency.
- `db.task_categories.TaskCategoryStore` provides parameter-bound category CRUD
  and transactional category assignment. Batch moves require a transaction-bound
  executor. A documented `FOR KEY SHARE` difference is intended to prevent
  concurrent category deletion; live database validation is outstanding.
- `server.task_categories.TaskCategoryService` registers six protected contracts.
  List/create call the repository. Rename/delete/single/batch moves require the
  live-manager adapter to synchronize persistent and in-memory task metadata.
  Without that adapter, these operations return 503 and do not pretend success.
- `server.wsgi.WSGIApplication` is a callable bounded-body transport adapter.
  It does not start a listener or configure TLS, trusted proxies, timeouts, rate
  limits, database connections, bcrypt, or key storage. It exposes only explicitly
  registered routes and preserves router authentication.

## Verification

Combined authored suite: **473 tests passing** at this checkpoint. It includes
fake database execution/transactions, concurrent-init simulation, authenticated
router dispatch, unavailable-manager behavior and WSGI input/header bounds.
No live PostgreSQL, network listener, upstream app, provider, scanner or target
was executed. No full frontend type/build/runtime or visual verification.

The original Go source and its persistence schema remain the reference. New code
must retain SQL parameters, row scope, permission and approval behavior. Partial
adapters are not grounds to remove original enforcement paths or migrate real data.

## Optional production-dependency adapters

`db.postgres.PostgresExecutor` lazily uses psycopg 3.2+ `RawCursor` so native
PostgreSQL `$1` placeholders are passed untouched with separate parameters. It
requires exclusive idle/autocommit connection ownership and commits standalone
operations before returning. Explicit transaction scopes use driver transactions
and savepoints; no connection is opened on import.

`server.passwords.BcryptCodec` lazily uses official PyCA bcrypt 5.x, no fallback,
with default cost 10 to match Go and the existing 8-character/72-byte policy.
Neither actual psycopg/PostgreSQL nor bcrypt is installed or exercised in these
contract tests; dependency extras are declared but no install was performed.

`server.application.build_application` composes settings-backed auth, category
services and WSGI from an explicitly supplied executor, signing key and password
codec. The fake integration test passes through WSGI to authentication/storage and
category listing, without a network listener. Production deployment still needs
secure key lifecycle, real schema/driver verification, TLS/timeouts/rate limits
and the remaining service/runtime adapters.

The WSGI audit tests cover path reinterpretation, malformed/header-injected
responses, hop-by-hop headers, Content-Type/length validation and HEAD/204/304
body suppression. These do not replace a production web-server review.

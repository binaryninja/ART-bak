# ARTEX Python migration (work in progress)

This directory is an incremental Python port of the Go backend. **It is not a
runnable replacement server**: isolated HTTP/auth contracts and storage/provider
adapters now exist, but the production server, database wiring, agent loop and
SDK tool integration are not assembled. See [INTEGRATION_STATUS.md](INTEGRATION_STATUS.md) for the
connected WSGI/auth/category slice and optional psycopg/bcrypt adapters. The original Go backend and
Next.js frontend remain intact. See [MIGRATION.md](MIGRATION.md) for the parity
inventory and remaining work.

Implemented modules:

- `artex.config`: configuration representation/loading (see module tests).
- `artex.guard`: tool-call interception primitives (see limitations in module).
- `artex.health`: LLM profile circuit-breaker registry, including cooldown policy,
  restore, snapshot, persistence callbacks, and synchronization. This is not the
  LLM transport/failover pool.
- `artex.failover`: provider-error classification helpers.
- `artex.asset_dsl`: offline asset-query parsing and parameterized SQL expressions,
  not scoped database query integration.
- `artex.report`: English Markdown/CSV finding exports and safe filenames.

Additional offline/injected-adapter slices now exist for `agent`, `db`, `enrich`,
`evidence`, `intercept`, `llmpool`, `llmrec`, `mcphttp`, `notify`, `server`,
`sidequestion`, `skills`, and `traffic`. Each package includes its own status
notes describing implementation boundaries and deliberate differences. The
combined suite currently passes **473 tests**; these are authored unit/adapter
tests, not Go differential tests or end-to-end frontend compatibility tests.

Run `python python/tools/translation_inventory.py` from the repository root to audit remaining Han text.
The audit intentionally includes compatibility fixtures, which must not be
blindly deleted.

## Tests without installing dependencies

Requires Python 3.11 or newer. From this directory:

```sh
PYTHONPATH=. python -m unittest discover -s tests -v
```

These tests execute only the new Python modules and standard library. They do
not execute upstream build/install scripts, send traffic to targets, call model
providers, or run the original Go test suite. Passing unit tests does **not**
prove backend feature parity or production readiness.

## Compatibility and safety

Preserve upstream authorization, session, target-scope, approval, cancellation,
and SSRF controls as their consumers are ported. Do not wire tool execution to
this foundation until the complete interception/approval path is implemented
and tested. Do not point any development server at production databases or
provide live API credentials for initial tests. Never assume a translated
prompt or response is behaviorally identical without regression testing.

Upstream source is a third-party backup whose provenance against the deleted
original is not verified. No upstream install/build scripts were executed for
this migration. The project remains licensed under the repository's AGPL-3.0
license; source attribution and license notices are preserved.

See [TASK_RUNTIME_STATUS.md](TASK_RUNTIME_STATUS.md) for task persistence,
manager/lifecycle, provider serialization and log integration, including strict
execution and parity limits.

Latest details: [GRAPH_RUNTIME_STATUS.md](GRAPH_RUNTIME_STATUS.md). Optional SQL
syntax QA: `python tools/check_sql_syntax.py` from this directory after installing
the separate `qa` extra. This runs authored tests only, not a database.

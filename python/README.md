# ARTEX Python migration (work in progress)

This directory is an incremental Python port of the Go backend. **It is not a
feature-complete replacement server**. An opt-in persisted-role task runtime now
composes actual goals/planner/worker execution, scoped transcripts and graph tools
under one authenticated host; completion-path acceptance uses fake HTTP and real
disposable PostgreSQL. See [PERSISTED_TASK_RUNTIME_STATUS.md](PERSISTED_TASK_RUNTIME_STATUS.md). Production
planner/worker orchestration, scheduling, full API coverage and SDK/tool
integration remain incomplete. See [INTEGRATION_STATUS.md](INTEGRATION_STATUS.md) for the
connected WSGI/auth/category slice and optional psycopg/bcrypt adapters. The original Go backend and
Next.js frontend remain intact. See [MIGRATION.md](MIGRATION.md) for the parity
inventory and remaining work. See [STREAMING_INSPECTION_STATUS.md](STREAMING_INSPECTION_STATUS.md) for the prior streaming checkpoint, [PLANNER_WORKER_STATUS.md](PLANNER_WORKER_STATUS.md) for scoped cycles and compaction, [ENGINE_HOST_STATUS.md](ENGINE_HOST_STATUS.md) for the explicit initialized-task engine and hosted lifecycle, and [BOOTSTRAP_POLICY_STATUS.md](BOOTSTRAP_POLICY_STATUS.md) for scoped configuration APIs, policy composition, MCP and workspace components, and [BOOTSTRAP_OWNERSHIP_STATUS.md](BOOTSTRAP_OWNERSHIP_STATUS.md) for explicit creation/ownership and bounded settlement, and [TRAFFIC_POLICY_STATUS.md](TRAFFIC_POLICY_STATUS.md) for optional workspace traffic reads and rule metadata, and [WORKSPACE_MUTATION_STATUS.md](WORKSPACE_MUTATION_STATUS.md) for the restricted file-mutation subset, and [TOKEN_UPLOAD_STATUS.md](TOKEN_UPLOAD_STATUS.md) for token reads, upload and frontend CAS adaptation, and [COMMAND_VISIBILITY_POOL_STATUS.md](COMMAND_VISIBILITY_POOL_STATUS.md) for inspection/policy adapters and the reusable integration harness, and [AUDIT_INTERCEPT_RESET_STATUS.md](AUDIT_INTERCEPT_RESET_STATUS.md) for optional diagnostics and bulk health reset, and [READ_DISCLOSURE_STATUS.md](READ_DISCLOSURE_STATUS.md) for the bounded disclosure-grant regression review, and [INTENT_ASSETS_STATUS.md](INTENT_ASSETS_STATUS.md) for intent-to-asset provenance, and [DEADLINE_OWNERSHIP_STATUS.md](DEADLINE_OWNERSHIP_STATUS.md) for the internal clock-ownership protocol, and [MAIN_SESSIONS_STATUS.md](MAIN_SESSIONS_STATUS.md) for scoped session metadata, and [ISOLATED_QA.md](ISOLATED_QA.md) for scoped real-database evidence.

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
combined suite currently passes **3221 tests**; these are authored unit/adapter
tests, not Go differential tests or end-to-end frontend compatibility tests.

Run `python python/tools/translation_inventory.py` from the repository root to audit remaining Han text.
The audit intentionally includes compatibility fixtures, which must not be
blindly deleted.

## Tests without installing dependencies

Requires Python 3.11 or newer. From this directory:

```sh
PYTHONPATH=. python -m unittest discover -s tests -v
```

Core tests execute only the new Python modules and standard library. Archive
codec tests require the optional `archive` extra (zstandard 0.25); without it,
those tests are explicitly skipped rather than installed automatically. They do
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

## Existing development launcher and remaining composition gap

`python -m artex --check-config` validates explicit `ARTEX_PG_DSN` and
`ARTEX_SIGNING_KEY_HEX` inputs without opening a database or listener.
`python -m artex --serve --port 8787` is an existing dev-only, single-threaded
stdlib WSGI loopback launcher for the persisted metadata/CRUD application. It
requires the optional psycopg/bcrypt dependencies and an existing compatible
schema. It does not create credentials/schema, infer provider keys, host the
frontend, start a task pump or resume tasks. Do not expose it publicly.

The CLI tests mock database/server boundaries; this statement describes source
capability, not an actual socket acceptance result. Full task-runtime resource
and shutdown composition into a packaged launcher remains separate work. See
`DEV_RUNTIME_LISTENER_PLAN.md` for the proposed explicit-input boundary.

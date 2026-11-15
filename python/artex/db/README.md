# PostgreSQL contract tranche

This package is a partial database-layer port, not a runnable replacement for
the Go database subsystem. It has no third-party dependencies, connection code,
import-time I/O, or implicit schema migration.

## Implemented

- Immutable `Query(sql, params)` plans using PostgreSQL `$1` positional binds.
  An injected `Executor` contract supplies row retrieval and affected-row counts.
- Asset DSL list/count plans with optional type and task association filtering,
  pagination defaults, and stable last-seen/id ordering.
- Declared-scope asset list/count and ID lookup. The exact Go scope CTE preserves
  direct source-task inheritance, soft-deleted-task filtering, company/domain,
  network containment via `try_inet`, and normalized ICP matching.
- The exact 32-column Go asset SELECT and decoder for its nullable fields,
  JSON-rendered PostgreSQL arrays, JSONB metadata, and textual timestamp.
- Exploration-scoped task-constraint CRUD, allow/deny validation, default
  `system` origin, original ordering, and not-found errors.
- Offline tests include source-contract drift checks, fake-executor CRUD,
  SQL binding, scope composition, JSON shapes, and NULL/empty-array distinctions.

The canonical schema remains `db/schema.sql`. Existing database constraints and
persisted enum values are unchanged. No generic SQLite substitution is made:
PostgreSQL array, JSONB, TIMESTAMPTZ, inet-containment, and SQL-function semantics
must remain intact in a future driver-backed implementation.

## Deliberate corrections / API distinctions

The DSL expression is always enclosed in parentheses before appending AND
constraints. The Go query composition can otherwise allow the left branch of
a top-level OR to escape a task/type/scope filter. This fix is covered by an
offline SQL-shape regression test; it is not a claim of PostgreSQL runtime
validation.

Malformed JSON and incorrect top-level array/member shapes raise errors instead
of Go's ignored unmarshal errors. Nullable columns remain None; empty arrays
remain empty lists. Driver-decoded mutable JSON is copied.

Scoped count is an additional query-plan helper. Empty ID lookup produces a
FALSE predicate rather than executing an invalid IN (). No executor is called
by query-plan helpers. Nonpositive scope task IDs retain Go's global fallback;
callers requiring mandatory scope must reject missing IDs before using them.

## Not ported or verified

- Live PostgreSQL driver, pool, transactions, migration/DDL runner, SQL function
  creation, startup schema upgrades, or end-to-end query execution.
- Asset creation/upsert/deduplication, company ownership/locking, task-source
  metadata hydration, scope mutation, task coverage, and bulk deletion.
- Most other Go stores, including explorations, graph nodes/edges, findings,
  evidence/traffic references, LLM records, intercept approvals, and enrichment.
- HTTP/agent integration, authorization enforcement, serialization with Go
  omitempty semantics, or database failure/retry/concurrency behavior.

A future executor must bind parameters rather than interpolate them. Its
transaction lifecycle and conversion from these positional placeholders to a
chosen driver's dialect must be explicit. It must return affected-row counts
and propagate exceptions. Passing the fake tests does not prove driver
compatibility, production safety, or full database-port parity.

Run isolated tests from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=python python -m unittest discover -s python/tests -p test_db.py -v
```

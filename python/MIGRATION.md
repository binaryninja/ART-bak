# Python migration ledger

## Scope and status

This is an **incremental port**, not a drop-in replacement for ARTEX. Keep the Go
implementation and its data untouched until compatibility has been demonstrated.
The English-language work is a translation, not permission to remove license,
provenance, authorization warnings, approvals, or functional behavior.

This inventory was read from source at
`7ad252ad3b891c2ab3fe747da509feb149166e8f`. It is not a security certification of the
backup or its dependencies. No upstream application, install script, build script,
network scanner, or integration suite was executed for this inventory.

## Measured baseline

- 600 tracked files; 327 Go files totaling 93,282 lines, including 153 Go test files.
- 261 HTTP method/path route registrations, plus two mount handlers, across
  `server/server.go`, `server/finding_traffic.go`, and `server/side_questions.go`.
  Nine side-question routes are loop-generated. Seven finding-traffic paths are constructed from a common base, so counting
  quoted path literals alone is insufficient. Counts describe registrations, not
  proven externally reachable unique endpoints.
- 49 `CREATE TABLE` statements in `db/schema.sql`. This is not a complete count of
  migration operations or constraints.
- 12 direct Go module requirements in `go.mod`, plus indirect requirements.
- 350 baseline text files contain 189,238 CJK codepoints in the Unicode ranges
  U+3400–U+4DBF and U+4E00–U+9FFF. These include prose, comments, prompts, test
  fixtures, logs, and persisted values. The count is a review aid, not a license to
  blindly replace every match. It excludes image text and other Unicode ranges.

### Baseline translation footprint by directory

| Area | Text files containing CJK |
| --- | ---: |
| Root files | 16 |
| `.github` | 1 |
| `agent` | 37 |
| `cmd` | 1 |
| `config` | 1 |
| `db` | 56 |
| `enrich` | 1 |
| `evidence` | 1 |
| `guard` | 1 |
| `intercept` | 7 |
| `llmpool` | 2 |
| `llmrec` | 1 |
| `mcphttp` | 1 |
| `notify` | 26 |
| `report` | 2 |
| `selfupdate` | 5 |
| `server` | 77 |
| `sidequestion` | 9 |
| `skills` | 7 |
| `traffic` | 5 |
| `web` | 93 |

Translated visible text must retain meaning. Persisted rule names, prompt variables,
JSON keys, enum values, and machine-matched error strings need compatibility review
before changes. Historical records and user-supplied non-English text must remain
valid. Test intentional Unicode handling rather than imposing an ASCII-only input
restriction. Screenshot/image translations are a separate deliverable.

## Architecture and parity gates

The following is the integrated checkpoint, verified by 373 offline authored
Python tests. Every package remains a partial port; see its README/PORT_STATUS/
PORT_NOTES file. Passing mocks does not establish Go or production parity.

| Area | Implemented slice | Remaining integration |
| --- | --- | --- |
| config / guard / health | DSN configuration and fail-closed redaction; audit/interceptor hooks; circuit-breaker registry | Guard without interceptor remains audit-only; adapters must enforce cancellation/decision; health uses Unix seconds and persistence callback ordering remains untested |
| db / asset DSL | Bound Postgres query plans, optional RawCursor executor, settings/category/task CRUD, source/company snapshots and constraints | Live driver/database validation, migrations, remaining stores, cross-process locks, archive recovery |
| llmpool / llmrec | Ordered provider chain, ranking/retry/capacity, capture and OpenAI/Anthropic completion/SSE adapters over injected transport | Full Norma SDK, production HTTP policy/transport, async lifecycle and DB wiring |
| intercept | Priority rules/cache, reviewer envelopes, human approval/cancellation adapters | SQL/config/model/trace services; deliberate fail-closed differences need acceptance testing |
| agent | Deadline/metadata/boundary/trace/replay primitives | Main/goals/planner/workers SDK loop, real tools, graph transitions, scheduling |
| server | WSGI application, auth/category/task/log contracts, JWT and optional bcrypt | Real dependency verification, production keys, live runner/metrics, 244 remaining routes, SSE and frontend integration |
| manager | Synchronized task registry, DTOs, admission, cancellation/drain gates and category refresh | Production runner/bootstrap/metrics, FIFO scheduler, timeout monitor, multi-process coordination, startup recovery |
| mcphttp / skills | Injected-transport RPC/session/SSE parsing; metadata and offline catalog | Real HTTP/TLS, legacy SSE pump, Norma registry, full YAML/ZIP, tool invocation |
| traffic / evidence | Offline SQLite search/capture/archive and blob/reference/hash/store helpers | MITM proxy/CA, DB/SDK wiring, staged deletion/recovery/retention |
| notify | Filtering, credential destination guard, signatures/rendering, static SSRF checks | Production transports, digests/retry services, dynamic DNS/connect-time safeguards |
| report | English Markdown/CSV exports and safe filenames | DB adapters and sanitized UI rendering |
| sidequestion / enrich | Fake-provider budgets/replay/cache/cancellation; queue/cooldown/DNS records/bounded HTTP adapters | Main hooks, provider API, DB generation enforcement, Norma budgets, DNSX and stores |
| selfupdate / orchestration | Not ported | Runtime-specific update design; complete scheduler and restart recovery |
| web | English source localization, historical protocol aliases preserved | Full typecheck, build, live/visual QA; historical screenshots remain untranslated |

The orchestration layer depends on `github.com/Autumn-27/norma`, not merely on HTTP
calls to a model. Replacing its hook, session, tool, transcript, provider, and
cancellation contracts is a substantive migration dependency. Other direct modules
cover JWT, UUID, PostgreSQL, compression, MITM proxying, DNS, DNS discovery,
cryptography, networking, text handling, and SQLite. Do not assume Python packages
with similar names have equivalent behavior.

PostgreSQL is the current main data store (`db/db.go`), despite stale comments in
`cmd/artex/main.go` describing dual SQLite graph stores. Traffic still has a
separate SQLite/file role. Copying those old comments would produce an incorrect
Python architecture.

## Authored-test milestone

Combined suite: **373 tests pass** after integrating thirteen additional worker
packages and credential-query regression coverage. No upstream application,
Go test suite, integration database, live provider or target execution was run.
TypeScript parse checks are syntax checks only, not type/build/runtime checks.
Report exporters deliberately harden CSV cells, evidence fences and filenames.

## Implemented asset DSL slice

`artex/asset_dsl.py` ports the parsing and WHERE-compilation helpers only. Fourteen
authored offline unit tests passed with
`PYTHONPATH=python python -m unittest discover -s python/tests -p 'test_asset_dsl.py' -v`.
They cover precedence/grouping, aliases, bound values, parameter offsets, numeric
validation, array behavior, unknown-field fallback, injection-shaped input,
lexical quirks, Unicode and malformed/deeply nested queries. No PostgreSQL execution
or Go differential test was performed.

Public entry points are `parse_dsl`, `build_dsl_where`, `compile_dsl`, and
`validate_dsl`. Identifiers come only from field allowlists; values are separate
bound arguments. The port retains unusual upstream semantics: unknown field-like
terms become bare-text search, array `!=` excludes exact membership rather than
fuzzy matches, task/company ID operators compile to equality regardless of the
parsed operator, and unterminated double quotes are accepted. Documented changes:
Unicode whitespace is recognized correctly, placeholder offsets must be
nonnegative integers, nested-parser exhaustion becomes a validation error, and
integer bounds are explicitly 64-bit (matching 64-bit Go, not 32-bit Go `int`).

`QueryDSL`, `CountDSL`, `QueryDSLInScope`, database execution, task-scope CTEs and
source hydration are **not implemented**. Callers must wrap the returned expression
in parentheses before appending `AND` restrictions. The upstream query functions
append scope/type clauses without enclosing a top-level OR expression; replicating
that pattern can let the first OR branch escape the appended filter. This source
observation was not database-tested and should be reviewed before enabling queries.

## Safety invariants to retain

1. `guard.New()` has no interceptor. With no interceptor, or a tool disabled for
   interception, the Go guard allows execution. Destructive rules are ordinary
   configurable database rules, not hard-coded in the guard. A standalone guard
   port therefore is **not** a secure autonomous execution environment.
2. Preserve the complete `allow / deny / ask` lifecycle, cancellation handling,
   timeout behavior, audit records and full-input review. Platform blocks must be
   described as platform governance, not target defenses for an agent to evade.
3. Keep task/company/asset scope and exclusions, per-agent bindings, skills/MCP
   visibility and deferred unlock behavior. Review target authorization before
   any real traffic, even if an upstream default is permissive.
4. Password setup must not interpret a database failure as an unset password.
   JWT signing material must stay outside the workspace browsing API; retain
   restricted file modes and explicit authorization on protected endpoints.
5. Preserve archive/workspace/upload containment and size controls, credential
   masking, notification blocked-address protections, evidence integrity and
   cancellation/delete barriers. Their existence is not proof they are flawless;
   the port needs its own negative tests.
6. No automatic execution of fetched skills, shell tools, updater artifacts or
   network payloads during migration verification. Keep new tests offline and use
   fabricated data and temporary directories until explicitly authorized.

## Validation and release constraints

The upstream English README documents `go test ./...`, `./dev.sh`, and mock UI
preview with `NEXT_PUBLIC_MOCK=1 npm run dev`. The frontend scripts include Biome
`lint`/`check` and `build:static`; `npm ci` invokes the `prepare` script (`husky`).
These are source instructions, **not authorization to run unreviewed code**.

The Go database/server/agent test suites coordinate on a PostgreSQL advisory lock.
Tests can skip when no database is configured, and cleanup paths modify database
rows. A green summary without an isolated test database is not full coverage.
Never point parity tests at the user's live database or use existing credentials
implicitly.

The only checked workflow is `.github/workflows/release.yml`, triggered on `v*`
tag pushes. It runs npm installation/build, `build.sh`, and a binary smoke test,
then publishes GitHub release artifacts and Docker images using secrets. Its
workflow permission is `contents: write`. A normal branch/PR does not match that
trigger. Do not create tags, enable workflows, run the release, merge, or deploy
as part of the port. No PR CI validation pipeline is supplied by that workflow.

### Acceptance sequence

1. Review translated diffs and remaining CJK inventory; distinguish intentionally
   retained fixtures/provenance from untranslated visible content.
2. Run only reviewed, authored offline Python unit tests; record command, count,
   failures and unsupported behavior. Syntax compilation alone is not parity.
3. Add representative contract fixtures from reviewed Go logic, including malformed
   config, encoded credentials, approval denial/cancellation, missing data and
   concurrency races. Do not invent coverage for absent modules.
4. After appropriate approval, use disposable PostgreSQL and mocked providers for
   migration and API differential tests. Verify SSE and frontend workflows as well
   as JSON endpoints.
5. Only after the above, test a strictly authorized sandbox target. Preserve the Go
   rollback path and keep this port non-default until real acceptance criteria pass.

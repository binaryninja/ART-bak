# Literal tool policy configuration and cache mutations

New ToolInterceptConfigStore reads actual `intercept_rules` and
`intercept_enabled_tools`, supplying real configuration to declared exact
Interceptor instances. InterceptPolicyOwner serializes explicit idle-only writes,
holds original interceptor cache locks, invalidates those actual caches after a
transaction, and retains sticky uncertainty after any attempted write failure.
No automatic retry or reconciliation clearing exists. Local generation is not a
distributed policy revision. All users of the adapter must be declared; the host
must exclusively reserve admission/execution through the supplied lifetime guard.
An idle callback alone cannot establish this outside that host contract.

Standalone ToolInterceptMutationService supports explicit create/update/delete/
toggle/tools operations; no routes/default wiring. Rule changes require original
`X-Rule-Updated-At` CAS; enabled tools replacement requires
`X-Intercept-Tools-Digest`, SHA256 of JSON-encoded **raw stored string or null for
absence**, from `tools_digest`. The current GET API does not supply this token;
HTTP composition requires a separately reviewed authorized token read seam.

Source: server/intercept.go, db/intercept.go, intercept/intercept.go. Literal
`string`, action allow/deny/ask, target tool_name/tool_input, priority and timeout
wire values preserved. Rules order priority DESC/id. Empty or null tools PUT is
[], order/duplicates retained. Create/update returns source fields and timestamps;
missing update/delete/toggle is stricter409. Regex is explicitly unsupported,
including existing regex configurations, because Python re is not Go RE2.
Timestamp CAS and atomic transactions are stricter than source. Settings replacement
uses a table lock to serialize absent-key CAS; this can block unrelated settings
writers and is unsuitable for unconstrained high-throughput live updates.

Final request authority follows callback-bearing owner gates before each UPDATE/
INSERT/DELETE and before commit. Postcommit/guard-exit/serialization denial reports
uncertainty, not rollback. Original pins follow callbacks. These are finite
cooperative policy boundaries, not an atomic snapshot of arbitrary mutually
mutating authorization callbacks. Callbacks must not run execution/reentrant work.

This is not full interception enforcement integration: pending approval/log
persistence is explicitly unsupported; no reviewer, judge PUT, providers, tool
execution, approval decisions or new guard installation. Existing runtime guard
and default TaskRuntimeAssembly are unchanged. Inert tests use literal matching to
verify actual adapter/cache consumption, not real tool execution. Independent review and actual PostgreSQL evidence are recorded below; they
do not establish guard enforcement.

## Actual guard trace limitation (tested, not installed)

Do not install this configuration-only adapter as a production guard store.
`Guard.pre_tool_use` with a matching allow/deny invokes `Interceptor.log`, which
swallows the unsupported log persistence error: allow proceeds and deny blocks,
with only Guard's bounded in-memory audit. Matching ask fails at create_pending
and blocks; no pending row or human decision is produced. No-match/no reviewer,
or tools excluded from the enabled set, retain the existing permissive guard
behavior. Tests deliberately exercise these actual paths with synthetic strings
and zero tool execution, demonstrating the missing durable audit rather than
claiming it implemented. Production activation requires separate reviewed
approval/decision/audit persistence and runtime ownership integration.

Unlike Go's permissive loading, preexisting JSON null or malformed enabled-tools
settings fail closed in this adapter; PUT null still intentionally stores [].
Final request callbacks cannot change the original primitive mutation snapshot
or introduce pending approvals before effect: both are rechecked without further
policy callbacks. Unsupported regex configurations also fail closed.

## Settings-lock executor contract correction

Actual PG stage133 exposed that LOCK TABLE has rowcount -1 and must not use the
normal DML execute contract. The corrected adapter requires the fixed no-argument
`PostgresExecutor.lock_intercept_settings()` capability, usable only within an
already owned transaction. Ordinary DML negative rowcounts remain errors. Setup
rejects executors without this capability before any reads. The existing pool
transaction yields the pool itself and has no such capability: **pooled settings
mutation is unsupported**. Current concrete acceptance targets one owned
PostgresExecutor session, not implicit pool forwarding or a generic command API.

## Actual owned-session acceptance

Stage133-fix passes four actual PostgreSQL groups and 41 focused tests, including
literal CRUD and timestamp CAS, duplicate/empty enabled-tool replacement and
digest CAS, actual cache consumption/invalidation, pre-SQL authorization refusal,
and committed acknowledgment-loss quarantine without replay. The original LOCK
rowcount failure is preserved. The rerun explicitly changed the fixture from a
pool to supported owned-session execution; it does not claim pooled support or
a byte-identical harness. See ISOLATED_QA.md for immutable hashes and limits.

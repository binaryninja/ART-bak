# Isolated ARTEX baseline and Python PostgreSQL QA

## Scope and safety
- Original Go source commit: 7ad252ad3b891c2ab3fe747da509feb149166e8f (exported archive, no checkout edits).
- Python snapshot: 5444aa4578f3c73fcee162cc2fb60bfa1abfec76.
- Source TestMain/build setup inspected before execution. No frontend/npm/build.sh workflows run.
- Outer user+network namespace, only loopback and no routes; inner bwrap PID/user/IPC/UTS namespaces, UID1000, cleared environment, selected readonly runtime mounts and dedicated QA directories. No user workspace/home or credentials mounted. No live targets/provider keys.
- Go1.26.3 official archive SHA256 verified: 2b2cfc7148493da5e73981bffbf3353af381d5f93e789c82c79aff64962eb556.
- Modules downloaded separately using official Go module proxy/checksum database. Test runtime GOPROXY=off, GOTOOLCHAIN=local, no proxy variables.
- PostgreSQL17.11 Debian13 official signed-index packages extracted, not host-installed. Ephemeral data in sandbox /tmp, TCP only on isolated127.0.0.1; server stopped afterward. Unix sockets are denied by host policy.

## Original Go results
`go test -p 2 -timeout 8m -json ./...`:
- 903 pass events, 2 fail events, 1 skip event (test counts include named subtests).
- Package results: 13 passed, 2 failed, 2 no-test packages skipped.
- FAIL agent/TestGraphOverviewExpandsAssociatedCompanyScope: blackboard_inheritance_test.go:87, `task scope missing: <nil>`.
- FAIL server/TestCoreTaskLifecyclePG: core_test.go:88, `expected goal nodes seeded, got 0`.
- Live provider TestLiveContextReview intentionally skipped because ARTEX_REVIEW_LIVE_CONFIG absent.

Repeated complete suite with `-p 1 -count=1`: same903/2/1 results and same failures.
Focused isolated rerun of the two test names: agent failure reproduced; server lifecycle test passed. This suggests server suite-state/order sensitivity, but root cause is unproven. Neither failure is a Python-port regression: all runs used unmodified original source.

## Python real PostgreSQL smoke
psycopg3.3.9 binary driver, RawCursor, Python3.13, PG17.11, original schema loaded via direct driver setup. Seven explicit checks passed:
1. Native $1/$2 parameter binding, including quote-containing text.
2. Transaction exception rollback.
3. Nested savepoint rollback with successful outer commit.
4. Category create/get.
5. Task create and read/hydration.
6. Constraint create/read/update/delete.
7. Task enqueue/dequeue/status persistence.

This is smoke coverage, NOT comprehensive schema/store/locking parity. No production migrations or deployments occurred. Deadlocks, concurrent cross-process stores, all104 SQL statements, every task options combination, service/provider/Norma runtime and frontend remain unverified. PostgresExecutor deliberately rejects DDL rowcount -1: schema setup used direct driver, not executor.


The test harness was disposable; the commands above require equivalent isolated dependency and schema preparation. No raw test logs or credentials are included in this checkpoint.

## Extended asset/finding checks and correction

Published `0dd89f3f8f829da2caa5161382e145982aaef0ba` passed 11 targeted
PostgreSQL checks covering six asset types (seven variants), API provenance,
repeat-write identity, rollback, finding metadata mirroring and report-version
checks. One additional check exposed a port defect: schema-valid `archive_failed`
was incorrectly blocked because the port compared against `failed`.

After correction, immutable local snapshot
`ff9071d3840a2a685a1b169bc64c68356d44d857`, tree
`d3e1d8cefc9e6d47eb771e2d153ab9046ca06b88`, passed all 12 checks in the same
isolated PostgreSQL environment. Failed archives now allow report updates and
finding creation; queued archives still block mutation and preserve prior data.
This snapshot identifier is local validation evidence, not a claimed remote commit.

Fixture company resolution returns no company, mutation guards are inert and
root-domain resolution is a deterministic mapping. Production policy adapters,
public suffix handling, notification/evidence workflows and concurrent lock
contention remain unverified. Fixture domains/addresses were never contacted.

## Profile and node-board real PostgreSQL checks

The exact tree `dad29d5c007eabc1c6ec638674b117ba15917abf` (published as
`ecece212d8309c3e6798fd30e00f56328a7571ec`) passed eight additional isolated
PostgreSQL fixture checks: profile public/private masking, blank-key preservation,
active selection and missing-ID rollback, explicit unsupported deletion guard,
node paging/search/scope, inherited intent filtering, edges and anchors.
Dummy credentials only; no provider calls. Profile deletion, concurrent activation,
failover repair, pagination snapshot consistency and broader parity remain unproven.

## Composed WSGI and real bcrypt checks

The exact tree `60696dfbfe34269986ba4d5a46538c4a43e22f2c` (published as
`1effa5b36f8307626adda57d2017f6bd198b59bc`) passed six grouped integration
checks using PostgreSQL17.11, psycopg3.3.9 and PyCA bcrypt5.0.0: auth initialization,
duplicate-init rejection and hashing; login/JWT/protected routes; real profile
list masking and unavailable runtime response; scoped graph/not-found responses;
node-board neighbor and anchor composition. Tests invoked WSGI directly without a
listener. Dummy credentials and explicit fixture resolvers only. This does not
establish production hosting, provider integration or comprehensive interoperability.

## Company, scheduler and actual loopback transport checks

Immutable local snapshot `da4d7b3cff5990daab8917e5d1d525f96ff29032` passed six
grouped checks: company normalized upsert and scoped domain/IP/ICP resolution;
real TaskStore FIFO admission/completion; pause and stale-generation rejection;
and actual HTTP transport POST against a fixture server inside the isolated
loopback network. No external provider, user credentials or target was contacted.
Later skill-label-only edits did not change tested code. Distributed scheduling,
company scope mutations and external-provider interoperability remain unverified.

## Side-question, worker and company HTTP checks

Immutable local snapshot `376dc0a91124aa6e28c53ccda02674f244c4d5f9` passed seven
grouped PostgreSQL checks: three side-question parent kinds, snapshot ordering,
idempotency and clear-generation rejection of late writes; foreign worker
rejection; worker claim/state version and transcript CAS; lifetime/archive guards;
and composed company API authentication, create/list/conflict/unsupported scope.
Only disposable fixture history was cleared. Lifetime guards and transcript
backends were fixtures, and ordering checks were sequential, not concurrency
proof. No optional profile factory/provider execution was performed in this run.

## Go/Python authentication interoperability

On immutable `36e88a4b61cd1468d54b7922e80a0ab0ec6031a8`, bcrypt hashes and ordinary
seven-day HS256 login JWTs verified in both directions using source-equivalent
Go helpers and Python authentication code. Both reject expired/future-not-before
tokens. Both accept signed arbitrary audience/subject and future issued-at claims;
these are not configured authorization selectors in the single-admin model.

Confirmed stricter Python differences: it requires expiration and accepts HS256
only; original Go accepts missing expiration and the broader HMAC family including
HS512. Ordinary source-generated HS256 tokens interoperate. Dummy keys only; no
keys, hashes usable as credentials, or tokens are published in this report.

## Streaming, retry and side-runtime integration

Immutable local snapshot `386010389537a906b69386e96f43cab3fad5914e` passed six
grouped checks in isolation: real PostgreSQL template atomic patch/FK rollback and
fixture deletion; actual side-question coordinator/service/store preserving partial
usage and enforcing profile/clear-generation guards; real retry transport/provider
against loopback HTTP (429 then success used two calls; refusal used one); and
composed authenticated WSGI log SSE replay/live delivery/close. No product mismatch
was found. Provider behavior was a fixture and retry waits bypassed; external
provider compatibility, timing behavior and production streaming remain unverified.

## Two-connection ordering checks

On immutable `376dc0a91124aa6e28c53ccda02674f244c4d5f9`, six controlled PostgreSQL
race checks passed using independent connections: competing different-client
starts, same-client idempotency, maximum snapshot ordering, clear-before-late-update,
update-before-clear, and worker version claims. Clear/update tests observed actual
PostgreSQL lock waits before release, not a shared Python lock. No hangs occurred
under bounded SQL/future timeouts. One controlled run per ordering is not exhaustive
stress or all-interleaving proof; only disposable fixture records were affected.

## Actual fresh-bootstrap and dispatch checks

Immutable local `6c055deabbfb075d73bf19b5a7cdbeb74706237e` passed seven grouped
integration checks on PostgreSQL17.11. The Python packaged bootstrap itself created
all 49 tables; all four legacy Unicode values were preserved. Second bootstrap,
existing objects and wrong database identity were rejected, and an injected real
SQL error after DDL rolled back every new table. Archive queue/claim/progress/fail
CAS, statistics, and actual scoped side-question HTTP/coordinator/dispatcher also
passed with inert providers. Only PG17.11 was exercised; PG12 compatibility,
built-in seeding, upgrades, archive execution and real-model interoperability are
not claimed.

## Conversation stores and bounded pool (stage 10)

Disposable PostgreSQL 17.11 and psycopg 3.3.9, under the same credential-free,
network-isolated namespace/bwrap harness, exercised the new conversation metadata,
activity store and connection pool. Initial run: eight checks passed and three
failed because optional-row fake tests returned None while the real executor
raises NoRowsError. Narrow absence handling was corrected; other driver failures
still propagate. The original failure evidence was retained.

The corrected immutable targeted snapshot passed all 13 grouped checks: missing
conversation/profile/parent outcomes, missing patch/profile-update behavior,
transactional creation rollback, actual UndefinedTable propagation, independent
connection pool concurrency/savepoints/query cancellation/close, observed profile
key-share blocking, and atomic activity/title/touch rollback. Source SHA256:

- conversations.py: 795a7e0cf7d08c099b81cce36efcc073f25af9ad652d0d5167fb1f91f84afed5
- conversation_activity.py: 52330da74c743676f39dd57a9f014e88b1181a5dedf5b308be42b251631f2ba9
- pool.py: 47f5bcf1dabcf0f2007cdf6961fc088f7c1cdf1108b00ad1ee651849dc8f361c

These are scoped DB checks with synthetic fixtures, not a complete chat browser
or provider integration, distributed transaction proof, or PostgreSQL-version
matrix. No production data, credentials, targets or providers were used.

## Authenticated conversation connected path (stage 11)

A detached immutable snapshot (manifest SHA256
`a67fa11bfdf77970bff47ac2666d48ccd0c52a961180424d71382e5d16acfd3f`)
passed four grouped checks with real isolated PostgreSQL, bcrypt/JWT, the bounded
pool, dispatcher and JSONL adapter. Actual build_chat_application requests saved
and activated a dummy profile, created a safe fixture conversation, returned
202, persisted a model reply, then served authenticated activity history/detail.
Repeating the client id did not invoke the inert in-process transport again.
Stop produced durable non-completion. An injected terminal SQL failure retained
unresolved ownership, rejected replay, and returned drain=false with zero live
workers and one unresolved item instead of falsely claiming successful cleanup.
No external model, tool, target, credentials or user data were involved. These
checks do not remove the documented SQL/JSONL crash-atomicity limits.

## Agent catalog real-driver validation (stage 12)

Initial real-PostgreSQL run passed five groups and exposed a duplicate-create
mismatch: INSERT ON CONFLICT DO NOTHING RETURNING raises NoRowsError in the real
executor, rather than returning None. The narrowed fix maps only this expected
absence to conflict409. All six groups then passed on the targeted immutable
snapshot (manifest SHA256 starts `f60470f9`), including atomic English starter
prompt/current-pointer creation, JSONB binding counts, metadata/missing404,
injected prompt failure rollback, true database-error propagation and explicit
false readiness. These checks do not execute or register agents, seed production
catalogs or establish full detail-default compatibility.

## Product archive snapshot adapter and task chat (stages 13–14)

The first archive SQL pass required a QA-only control adapter because generic
DML execute correctly rejects unknown affected-row counts. Product code now has
an explicit idle-only read-only repeatable-read transaction capability, including
pool pinning; ordinary DML validation is unchanged. Six actual-product PG checks
passed on immutable manifest
`71b6fa52525ad8f963a08b3ee98c275d3dab513777003b08342a982aa464dabe`:
22 scoped queries/LLM NDJSON, byte budget, typed missing parent, actual write
rejection/rollback, nested refusal preserving the outer transaction, and pooled
snapshot/drain. A wrong test fixture display-name assumption was corrected and
all six rerun; no product change was needed for that assertion.

Task chat separately passed five grouped real-PG checks on immutable manifest
`ede4b4fc23692683f638cde2b664f7d88f1e3eeb2e11568d0d8803b1f715fb47`:
POST202 through bounded dispatch and actual JSONL/SQL activities, no repeated
provider call for duplicate client keys, changed-scope rejection before activity,
wrong exploration rejection, full activity metadata/usage/segment persistence and
atomic rollback when a later activity references a missing node. These used
explicit fixture lifecycle guards and inert providers, not autonomous manager
or distributed ownership proof. Archive execution/restore/pruning remains absent.

## Translated Go packages and tool catalog (stage 15)

The isolated Go1.26.3 serial rerun passed all 264 reported test events (including
subtests), eight packages; enrich has no tests. Nine selected packages: notify,
intercept, sidequestion, config, enrich, evidence, guard, llmrec and mcphttp.
Immutable corrected-source manifest:
`1f871d6a0144c0a5c739a415ad870c4416aad0ee40fb394c917ca0432a4e92a0`.
Only loopback fixtures were reachable inside the namespace; no notifications or
external targets were contacted. The initial llmrec missing-table failure was a
harness prerequisite gap, corrected using source-derived optional recording DDL.
Two sidequestion compaction failures were real translation-exposed token-estimator
rounding boundaries. The chunk-fitting algorithm was corrected against the actual
estimator, retaining reserves and UTF-8 boundaries; the full selection then passed.
This is not a rerun of all original packages (earlier unrelated baseline failures
remain documented), nor a model-behavior equivalence proof.

Tool catalog separately passed six real-PG grouped checks: metadata CRUD/JSONB,
duplicate409, agent binding validity and actual key-share blocking of concurrent
agent deletion, builtin schema protection, and injected-trigger rollback. Exec
metadata was never executed or registered as a runtime tool.

## Recording setup, seeds, ownership and inspection (stages 16–18)

Explicit recording-schema setup passed seven real-PG groups on immutable manifest
`77dad9679a823d6b91eaf23c124f5a9295f9bf89b71220844cf38cde61102d96`:
fresh/repeated setup, five-column legacy additive upgrade, incompatible columns/
defaults/index/RLS refusal, wrong database/read-only refusal, actual SQL rollback
and independent advisory-lock contention. Catalog validation is a supported-shape
check, not a complete sequence-ownership/opclass/version audit.

Catalog/ownership checks passed eight groups on manifest
`dd86f357aff03004013b614f9465dfa68ec0fb4910c429b0c0938bcaeb1d96f0`:
six builtin/eight-variable metadata, customization preservation, late collision
rollback, prompt version/pointer behavior, cancellation/trigger rollback, real
snapshot ownership/shared-host/asset queries and byte limits. Sparse synthetic
ownership cases do not prove every company/anchor combination or cleanup safety.

Recording-store/scoped HTTP passed six groups on manifest
`1ffa70cb2432d8e6b0738da7f539c6428368700905f93c126ab250ce716ba848`:
exact nullable TEXT bodies, body-free pages, detail/missing rows, task/unassigned
scope, usage/picker, bounds/transaction failure, and HTTP authorization/redaction/
cross-task denial. No real records, providers, capture enablement or deletion.

## Streaming and process primitives (stage 19 and process harness)

Six connected streaming groups passed on manifest
`6a717165a86a74d4fb28f4dc9edba286c4fc66c102552eb8655a04cd6a96645f`:
actual PG/JWT/JSONL/activity path with inert OpenAI and Anthropic SSE, fragmented
tool buffering, refusal/EOF/cancellation and profile mutation inside transport
close. That mutation prevented stale tool execution. Response close and clean
pool/dispatcher drain were observed; arbitrary blocking iterators are not proven
preemptible. Only an inert memory spy represented tools.

A separate immutable process snapshot, manifest
`7b0315a6cf92e2bb566352cb868c10ff2253ccfca00b30ab46174b8c3af03a2c`,
passed seven actual POSIX checks inside the same cleared, externally disconnected
namespace: printf output/reap, explicit environment (no inherited fixture value),
denied launch creates no process, output bound, timeout, cancellation/reap and
exit-code annotation. Commands were fixed harmless printf/exit/sleep fixtures,
not agent-selected workloads or targets. This is not proof of OS confinement,
escaped-descendant cleanup, arbitrary spawn/wait bounds or absence of PGID reuse.

## Scoped planner and worker cycles (stage 20)

Six grouped real-PG checks passed on immutable manifest
`74ca17de52862d929f5b1a2c6a8d6932d2516029ffa5cba41029b1eb49f80310`.
Actual stores, registered memory-only spy tools and durable JSONL verified scoped
intent creation/anchors/lineage, transactional batch rollback, replay without a
second provider call, fresh wake context, worker claim/version/settlement and
three cancellation reasons. Model text claiming success did not mark a goal met.
Two contenders had one claim winner under a shared fixture lifecycle lock; this
is not overlapping-transaction or distributed lease stress. Injected settlement
SQL failure left a running, unresolved intent and prevented provider replay.
No autonomous scheduler, model, externally effective tools or targets ran. This
is not proof of cross-store atomicity, automatic recovery or complete orchestration.

## Explicit provider recording adapter (stage 21)

Six real-PG groups passed on immutable manifest
`ae8dd9d61486172e2b5720f7b882acb053e7087b76b6ae15c73059c3fab0ee22`.
Complete, streamed, partial-close, cancellation and error observations produced
redacted records plus known usage atomically. Explicit immutable provider binding
required literal approval; rejected and never-started streams caused no provider
work. A real trigger failure rolled back both rows. A committed transaction with
simulated lost acknowledgment raised recording uncertainty without provider retry.
The fixture used inert providers and explicit recording opt-in; automatic app
wiring, production redaction completeness and provider equivalence are not proven.

## Scoped graph tools (stage 22)

Six real-PG groups passed on immutable manifest
`7ebe2e6e389583e9fb1d22dc85029ee4b6fb00ee35ff8a65e7a5e0791ecaad49`:
local fact payload/asset anchors/intent lineage, foreign-evidence denial, actual
second-fact FK rollback, evidence-linked proof and duplicate edge behavior, actual
goal-state trigger failure rollback, and stale/cancel guards before writes.
Proving a graph relation does not establish semantic truth of evidence and never
automatically completes a task. Inherited-union writes remain unsupported.

## Explicit resolved-resource compaction (stage 23)

Six real-PG/JWT/profile/conversation/JSONL groups passed on manifest
`2e16f32e8f275ebeca6312d33377edc2367c43b9136bf777e2712de9af2fe439`.
The actual compacted main request matched capture; raw history was retained.
Summary usage survived denied main approval and main failure; exhausted budget
prevented a main call. Real transcript and profile changes during the final
resolver prevented stale summary transport. This snapshot explicitly constructed
ChatAgent from resolved resources; it did not test HTTP POST admission wiring or
automatic recording. Later assembly changes require their own evidence.

## Graph context non-mutating views (stage 24)

Five requested real-PG groups passed on manifest
`7db4c52a79d5b86f50eb94eaca22dd7ad2ab27258f21a5b73261177c5486e9d8`:
hot/cold lineage and revival-aware persisted digest views, source list limits,
node/edge/byte bounds, stale/cancel rejection and foreign-scope rejection.
An extra compatibility probe failed: PostgreSQL READ ONLY rejects FOR SHARE.
This is a non-mutating view requiring a transaction capable of parent row locks,
not a read-only-replica adapter. No inherited graph, cold-stamp persistence or
cross-writer consistency guarantee was tested.

## HTTP-admitted compaction (stage 25)

Four actual POST202 groups passed on manifest
`6b69eeceebb838cb7c059c699feb4d89ba738838d56a2afdc0d08461e1c6515f`.
JWT/PG-backed profile and conversation admission invoked the concrete inert
provider twice for summary/main; capture, raw JSONL, activity and combined usage
matched. Duplicate admission did not repeat providers. Denial/main failure kept
known summary usage and persisted an error. A real profile-window mismatch
returned 400 before activity or provider work. Dispatcher and pool drains were
clean. This is optional conversation compaction, not autonomous task orchestration.

## Explicit engine progression (stage 26)

Five real-PG groups passed on manifest
`7f52148da43bd8a85098f126934525d89d985de0fb7d1f04a6323f3bfe0b13e9`.
Actual planner created an intent, worker recorded a fact, next planner invoked
registered prove_goal, and the engine observed authoritative readiness. New open
work blocked completion requery. Orphan running work and uncertain SQL settlement
quarantined the handle without replay; active cancellation drained and reopened
its intent, and foreign scope was rejected. All provider results were inert.
The task remained running: this stage did not test successful TaskManager.complete
or application-factory wiring and did not substitute a fake completion callback.

## Opt-in synthetic record deletion (stage 27)

Six independently reviewed real-PG groups passed on manifest
`2e3d497b31eb136448000dccbff7080c0cd9b3a1910bca8d3a27d9ea61866756`.
Default 503/authentication, strict filters/scope and second authorization denials,
exact-task deletion, literal wildcard/quote equality, SQL rollback and redacted
trigger failure were checked. Only disposable synthetic llm_records were deleted;
usage rows remained intact. No production deletion or approval-flow integration
occurred; an explicit action-specific deletion policy is still required by host.

## Exact manager completion (stage 28)

Five actual PostgreSQL manager/host/EngineRunner groups passed on manifest
`4386d868ca20e9a787165e790662cf3077000e93ee8cbcb0c3c7732cd9872db3`.
Planner, versioned worker and registered proof tools led to authoritative goals;
host.poll invoked actual TaskManager.complete, persisted done/timestamp and
retired ownership. An injected terminal-update trigger failure retained manager
and unresolved runner ownership without replay. Idle and active pause/drain were
checked. Unsupported seed/type failed before admission SQL or construction.
This stage did not enable the hosted HTTP factory or use external providers.

## Explicit hosted HTTP-to-completion path (stage 30)

Four actual PG/bcrypt/JWT HTTP groups passed on manifest
`e8352f1656e8c13daa61030311bd26f4e862277c0655a1c98c8c3f80e44644cc`.
Authenticated resume/pause/drain/resume led through explicit engine advances,
planner/worker/proof and actual persisted manager completion. Construction ran
no provider work. Capacity denial performed no admission, second host/factory
adoption failed, and unsupported creation returned 503 without task writes.
The initial resume fixture was unpaused and correctly rejected; its persisted
state was corrected, not the product. No automatic ticking, default enabling,
external providers or production data were involved.

## Goal decomposition and explicit constraints (stages 29, 31, 33)

Goal-only decomposition passed six real-PG groups on manifest
`b3bd4124a6223c28ed6099bdd5702dea52bc08e452ca24e272fb221cf9aaff63`:
FK/cancellation rollback, invalid/denied/prose rejection, durable replay and
committed-SQL/failed-JSONL uncertainty without duplicate effects. This is not full
bootstrap or automatic user-scope extraction.

Standalone constraint tools passed six groups on manifest
`9fa635f2b09c8903581fce35bbbe8f90bf9c46817e2459fe2ddae218d0798075`.
Exact frozen allow and deny proposals needed independent host approval; SQL or
cancellation rolled back a whole batch. Foreign, paused, stale and changed
executor bindings failed. Model extraction is never treated as user permission.

Explicit GoalPlanner/ConstraintTools composition passed six groups on manifest
`103023201eec9fb2b66b232d49496e47c607a8e1e7542f46ee777fba373356de`.
The source English goals prompt and explicit catalog produced approved rows;
changed catalog/defaults or removed adapter conflicted with durable replay.
Denied proposals and SQL errors stopped the turn without later goals. Atomicity
is per tool batch, not across the conversation. No full bootstrap is claimed.

## Task-scope metadata (stage 32)

Six real-PG groups on manifest
`103023201eec9fb2b66b232d49496e47c607a8e1e7542f46ee777fba373356de`
verified normalized exact approval, duplicate preservation, batch rollback,
archive/deletion fences and actual company KEY SHARE blocking another connection's
delete. No hostname-to-root or CIDR broadening is inferred. Paused/status/run
policy remains the explicit host current/guard responsibility. The native tool
adapter has separate later review; this stage tested the metadata store only.

## Explicit pump (stage 34)

Five real-PG groups on manifest
`e2cafbe4cb231f2dabc8f2ee607916083364b88b8941bc5bb0bd1d78ba8d4d25`
verified actual proof completion, stop timeout without implicit task cancellation,
pre-install admission fences and uncertain terminal SQL without replay. Pausing a
blocked planner stopped the thread after release and persisted pause, but the
fixture's execution-current callback denied cancelled transcript finalization.
That correctly retained an unresolved handle; host drain was NOT all-settled.
Do not describe this snapshot as universal clean cancellation. A separate explicit
persistence-authority adapter requires its own later verification.

## Pure deadline and wrap-up policy (stage 35)

Source comparison plus 13 authored and four independent isolated regressions passed
on manifest `e58154c60950b47d7247832f63774efd1118248ca6a7962d90c2a92f7848ace0`.
Deadline floor/own-budget ordering, reason-specific prompts, override behavior
and code-owned tool exclusions were checked. No provider, timer, extra budget or
actual task deadline settlement was enabled.

## Optional constraint HTTP (stage 36)

Five real-PG groups on manifest
`b2b91b9969c016768949c1ed873a2a46fe46d279430d662f5a4572b8d32109e5`
verified source-shaped CRUD, explicit deletion policy, idle/revision/foreign-node
fences and stale-write rollback. Captured SQL outputs confirmed row, per-item,
origin and aggregate-byte overflow return sentinels before payload transfer;
HTTP returns unavailable rather than silently omitting policy. Only explicitly
registered fixture handlers and synthetic records were used.

## Native task-scope tool (stage 37)

Five refreshed real-PG groups on manifest
`991ef10989a554abfc3df8605850f00fdf0b32229f1c411bc28096dc631a27b0`
verified pinned store/executor and expected exploration in the same parent lock.
Foreign exploration and executor redirects failed before foreign connection.
Source-shaped per-entry partial results preserve earlier committed entries on
later cancellation/SQL failure; no false whole-batch rollback or automatic retry
is claimed. Normalized scope still needs independent host approval.

## Optional bounded goal HTTP (stage 38)

Five real-PG groups on manifest
`b81033c5552d4ca1fd0967aa939044b0fcf39384e8e2575df455c53f8428b827`
verified text/class edits preserve met state/proof/lineage, goal-only deletion and
its edge cascade, stale-delete cascade rollback, actual edge-insert rollback,
idle/foreign-scope rejection and SQL byte sentinels. Mutations no longer fetch
unbounded old node payloads. Oversized response origin rolls back the mutation.
No automatic revival, planner notification or task completion is supplied.

## Explicit planner cancellation persistence (stage 39)

Four actual PG/thread/JSONL groups on manifest
`73c0756e31e0b2a61eef3f7c197e2f92bef89b931ec63cf9c9898ad1c7a18492`
verified optional strict same-generation persistence authority can finalize the
cancelled planner's own receipt without executing returned tools or claiming a
graph observation. Pause/drain then completed cleanly for that fixture. Missing or
stale persistence authority still quarantines and retains ownership. Resource
rebinding failed before foreign connection. This narrowly resolves the stage34
fixture gap when explicitly configured; execution permission is not relaxed and
universal cancellation cleanliness/default enabling is not claimed.

## Task profile HTTP and ordered binding (stage 40)

Five real-PG groups on manifest
`579d2d2f6e8995bcdd46709848669f25ca16a17ea98ba9c022b512299531c0dd`
verified ordered chain/active profile/revision CAS, quota/idle restrictions,
secret-free static resolution and actual profile FOR SHARE blocking independent
updates. Post-commit coordination failure reports committed changes requiring
reconciliation; retained and cross-thread write callbacks cannot mutate. This
is configuration availability, not provider reachability or automatic enabling.

## Optional local scope HTTP (stage 41)

Four real-PG groups and five native-adapter regression groups passed on manifest
`1176fdf4467ff716266d668edbd9f590049bd34d3da03e7c9cd74a46ce1d7213`.
Local DTO/manual provenance, exact independently approved entry/deletion, idle/
revision/parent fences and SQL byte/row sentinels were checked. Any inherited
source relation makes this narrow GET unavailable instead of silently omitting
inherited scope; inherited deletion is not supported. Metadata does not itself
grant testing authority. Only explicitly registered synthetic fixtures were used.

## Read-only workspace HTTP (stage 42)

Ten authored and five independent disposable-tree checks passed on manifest
`f8b858c4f9dc719cafe1ab85966c4e9c66df7a039a0744d33655e04d1f95a1da`.
Root replacement, FIFO/symlink ancestor/hardlink/traversal, stale reads, header
encoding and oversized inputs were checked. The UNIX-socket fixture was blocked
by runtime permissions and was NOT RUN. Buffering is bounded but response limits
are not peak-memory guarantees: listing limits apply after serialization and a
growing inline file can consume the configured download cap. Host-exclusive root
rename/mount/writer coordination remains required. No real user files were read.

## Conservative MCP metadata (stage 43)

Four real-PG groups on manifest
`f1b9512b1e12a52e1b9ab8465b78d9b423fd3f16ebd6e4936b35ab19c58a23cd`
verified metadata CRUD, cached-tool/visibility cleanup, missing-row behavior,
immutable policy inputs, idle/executor fences and reconciliation after committed
sync failure. Command, arguments, environment/header keys and values, URL and
cached tool text are opaque in public DTOs; placeholders cannot be persisted.
Actual delete failure rolled back visibility changes. Only synthetic metadata
was used: no process, connection, refresh or discovery occurred.

## Explicit workspace write (stage 44)

Eleven authored and five independent disposable-tree checks passed on manifest
`2d0a591556042d787c9627393890076d80fe6ae77af4b44ff4135d4e3b1dac3a`.
Frozen proposals, root replacement, FIFO/xattr refusal, permission preservation,
precommit failure cleanup, post-rename uncertainty and closed retained contexts
were verified. No real user files were touched. ACL/ownership edge cases were
source-reviewed but not dynamically tested; exclusive host writer coordination
remains required. Outcomes after entering the primitive may conservatively be
unknown, with retry explicitly false rather than claiming rollback.

## Explicit constraints/scope/goals composition (stage 46)

Five actual PG groups passed on manifest
`b91fd6a5f8617a2ed777e7f4a0a50337dbcdda580e923a8685e3bcb11ca874ac`.
Explicit English source goals/scope-tail prompts and native catalogs composed
actual approved constraints, exact-host scope and goals. Catalog defaults did
not bypass normalized-entry approval; changed catalogs and foreign runs failed.
Later JSONL failure retained prior per-tool SQL commits and blocked retry.
No conversation-wide transaction, hostname promotion, complete bootstrap or
creation-to-engine default enabling is implied by this composition.

## Explicit MCP-native bridge (stage 47)

Twelve authored and five independent inert-transport checks passed on manifest
`e6fe27fb10bca8f7a61ad81e72133dc8c50cca54a74ca0332bff294414f52733`.
Actual Client.rpc and ToolRegistry code enforced fresh grants, exact client/server/
URL/headers/session/transport and runtime-scope bindings, schema/result limits,
remote error versus uncertain outcome handling, and no automatic retry/raw error
exposure. Unsupported audio/resources/extra text fields failed closed. No socket,
discovery, process, external MCP server or default registration was used.

## Explicit standalone bootstrap journal (stage 45)

Six disposable PostgreSQL groups passed on manifest
`1118e84f6821e8b21b9c43278b1cca8cd7368282f4950e88fc46fbd003e14aa2`.
Fresh-only authorized journal installation, database identity, DDL rollback and
advisory lock contention were checked. Actual GoalPlanner/TaskStore/JSONL paths
produced validated goals and sequence-allocated run IDs without starting an engine.
Prepared replay checked text/configuration/lineage; retained planner authority was
revoked. Competing ownership and SQL/JSONL failures preserved unresolved effects
without takeover or false rollback. Prepared means initialization, not completion.
This evidence is for standalone bootstrap, not the later admitted runner bridge.

## Exact installed-generation authority (stage 48)

Eight owner and three independent race/fence checks passed on manifest
`7f43230b5f14b2720707984379d7b6be4cff240990cd5310dd7fde11d7582779`.
Exact installed token/handle and lifecycle state are required for execution;
cleanup-only authority cannot become execution authority. Pause and completion
races retire capabilities correctly in these bounded fixtures. The helper invokes
no repository/provider callbacks while holding its lock. This process-local check
is neither a durable lease nor a replacement for guarded SQL/host lifetime checks.
No real provider, database or bootstrap integration ran in this stage.

## Actual HTTP creation through owned bootstrap and completion (stage 49)

Four fresh-PostgreSQL groups passed on manifest
`559170faed5665fe1cd77d3736af7fa0af77336209d50fb4d21168fb1ce9a41d`.
Authenticated HTTP create installed an inert BootstrapHandle. The first owned
advance allocated the journal run and prepared real goals, without completion.
The same outer handle/token then retained an actual engine child; planner/worker
fact and proof processing followed by host polling persisted manager completion.
Pre-tick pause drained without journal/provider effects. JSONL uncertainty after
committed goal SQL retained unresolved ownership with no child engine or replay.
Capacity denial prevented task creation. Explicit isolated fake-provider fixtures
only; this does not enable production defaults or prove all source task options.

## Scoped coverage and asset references (stage 50)

Four actual PostgreSQL groups passed on manifest
`6ee4b61e48e1458c78b5ee3322c5d7aa6193b60a768b70c7e7d069a3c808f7bc`.
Direct-source aggregation, terminal inherited references, graph/coverage DTOs,
archive denial, disabled coverage and explicit source authorization were checked.
SQL byte-limit sentinels prevented oversized text/JSON transfer to the driver.
An initial fixture used `created` rather than native intent state `open`; the
fixture was corrected without changing source. No external effects, targets or
default route enabling were exercised.

## Explicit assembly and bounded settlement (stage 51)

Four actual PostgreSQL assembly groups and all thirteen focused settlement tests
passed in isolation on manifest
`6460f7623563376063880807373143f9d62eb9c5d0baed6d8261148551675b50`.
The actual build_bootstrap_application helper was used for authenticated creation,
preparation, engine proof and completion, plus pause, uncertain persistence and
real AdmissionController capacity denial. Runtime settlement tests used inert
providers and enforced existing budgets, cancellation/refusal/uncertain-effect
exclusions and paired tool outcomes. No default enabling or external calls.

## Prepared-owner deadline receipt (stage 52)

Four real PostgreSQL groups passed on manifest
`a736afca6b1dc6c58543236401c97cd0b67140d1eb262e555ddb2478d9feda2c`.
Exact prepared ownership, stamp-once/unchanged repeat, configuration/generation
rejection, SQL rollback and postcommit uncertainty were checked. A DDL setup
fixture error was corrected without changing source. These fixtures verify the
prepared-only **zero-timeout** path, not nonzero engine timeouts or first-goal-call
support. Source Go starts the task clock at planner/worker operation, not bootstrap
goal decomposition; no changed timing behavior is implied by this helper.

## Translated Go workspace presentation

Six top-level Go tests, including five authored snapshot-only English-error
subcases, passed with zero failures/skips on source manifest
`ccfb14d33f4ec3fb2a8a6ea5229358f3eb3582c40ecd7cb8ed9646ef5c156021`.
Existing tests cover owned-file deletion/staging/rollback; no direct handler tests
existed, so isolated error regressions supplemented them. Offline cached Go in a
credential-free network namespace used only temporary files, no database. Formatting
was clean. This is not the full server suite, authenticated middleware coverage,
all translated branches, or a Python-parity claim.

## Translated Go task-management presentation

Six narrowly selected Go tests passed with zero failures/skips on manifest
`253bedd958f50d0505b413ecac1da6ec0fc52b4bb79832cf66af95402c42e6e9`.
Existing category, metadata and template HTTP tests and four snapshot-only English
validation checks covered category/Worker messages. The full server package
compiled; targeted formatting was clean. This is not a whole-server test-suite
claim. Execution used the approved disposable credential-free environment.

## Go/Python policy differential

Thirty-four matching deterministic fixtures produced zero mismatches on manifest
`213afdbdb71cfa191d526bcdbc998f6042302c4b29312d69e87c31177f004bee`:
eighteen deadline-clamp cases and sixteen wrap-up reason, turn-budget and tool
exclusion cases. Python stricter validation and code-enforced Bash exclusion are
explicit differences. This is pure policy comparison, not engine deadline
integration, real provider behavior or prompt-byte equivalence.

## Fixed-workspace SQLite traffic reads (stage 53)

Sixteen reviewed tests plus four extra real-SQLite checks passed on manifest
`cd746394e70f3dfedabc201f35a5630a1fe7e75f8a65eba2c0d9a428ab0d2628`.
UTF-8/NUL byte limits across six body/header/blob-reference columns were enforced
before readers ran, including legacy TEXT values in BLOB-affinity columns.
Filtered counts, escaped response-size limits, post-read revocation without
leakage and unsupported FTS denial were verified. Authorization covers an explicit
entire fixed workspace; the capture schema does not prove task ownership. No
capture, proxy, deletion, external traffic or default route enabling was tested.

## Scoped intercept-rule metadata (stage 54)

Four actual PostgreSQL groups passed on manifest
`31fff012df0465f2b816515027a95037e790bf6fbd936722ffe7c08183cca61c`.
Task/global scoped CRUD, action normalization and ordering, real timestamp CAS,
SQL byte sentinels, lifecycle denial, rollback and postcommit uncertainty were
verified. These are metadata persistence checks, not matcher, enforcement,
runtime revision, policy-cache or default/API wiring claims.

## Standalone dispatch-clock validation (stage 55)

Three actual PostgreSQL groups and thirty-two focused tests passed on manifest
`d6331357a658794efb1552442dbdfeca4a38baad11248dea9ae23b6075f1d810`.
Repeated read-only validation performed no UPDATE; stale ownership/configuration
was rejected. Zero-timeout dispatch performed no SQL, and fake-clock fixtures
checked budget charging. The actual supported runtime fixture remains zero-timeout.
Positive-timeout behavior has mocked evidence only. No runtime guard was bypassed,
and no provider hook, paused-owner lease or timed-engine execution is claimed.

## Optional intercept-rule HTTP metadata (stage 56)

Three real PostgreSQL HTTP groups passed on manifest
`4a75e518eeb485816bb33406ab46b93ef11d08cb2f40d132a7df96009145dd19`.
Task/global CRUD, normalized proposals, mandatory ISO timestamp CAS, stale-edit
conflicts, independent idle/scope denial and postcommit uncertainty were verified.
The required X-Rule-Updated-At header is an intentional frontend contract addition.
These are metadata endpoints, not matcher/enforcement, runtime policy revisions,
or default application enabling.

## Explicit workspace mutation subset (stage 57, corrected)

The initial snapshot `d0e524fc41b7763bb05a64f381aea85f20bb07b3e16b2fb1e66cad99c2138275`
passed thirteen existing tests and four of five extra groups. Symlink-ancestor
refusal preserved contents but returned generic 500 rather than invalid-path 400.
After the narrow mapping fix, fourteen existing tests and all five unchanged
extra groups passed on manifest
`b6ee9c3270e9c6c5c74c0366e8f1dc8fb398131cc551df6768a1db65212f01b2`.
Fresh immutable approvals, parent replacement, post-effect interruption/fsync
uncertainty and retained closed operations were exercised using disposable trees.
The primitive supports existing-parent mkdir, regular single-link file deletion
and empty-directory deletion only. No recursive deletion, real mounting, user
files or protection against uncooperative external filesystem writers is claimed.

## Go/Python failover differential

Nine hundred nineteen synthetic fixtures produced zero classification mismatches
on manifest `c2b3c4180bc22bb9eccca1eb4e6c7918507f210c6a28f64ab4662514292fba24`.
One text-trimming difference is retained explicitly: Go can keep a truncated
invalid UTF-8 byte, while Python drops it to preserve valid text. These fixtures
do not exercise provider calls, actual retries, streaming or network behavior.

## Scoped token accounting reads (stage 58)

Four real PostgreSQL groups passed on manifest
`83114dd32aaa0b699b3a2a753e208644879bd3b3055aad23d97686ccc476fc3e`.
Exact integer/Decimal totals, UTC daily grouping, conversation-creation date
semantics and archived refusals were verified. Two-connection archive/prune
ordering was checked around the single aggregate SQL snapshot. These source
APIs expose token counts, not prices or billing costs; no broad archive recovery
or reconciliation is claimed.

## Bounded single-file workspace upload (stage 59)

Thirteen existing/review tests and four additional checks passed on manifest
`1afd324200aa6e3316506daabc2d7564a483c9a7b6f895656dd84c9acc898d3a`.
Exact binary bytes/hash, immutable proposals, revoked overwrite preserving old
content/mode, post-fsync uncertainty without retry, and malformed-header rejection
before resolution were verified. Only disposable fixture files were used. The
subset accepts one file, not recursive directories, extraction or source multi-file
batch parity. Default routes and permissions are unchanged.

## New frontend timestamp helper only

The AST-extracted newly authored interceptVersion helper (SHA256
`1b410015211dcdaeb8fb0efcf43943003fe25aec71742fc3759690bdba7731ee`)
passed four precision/offset preservation cases and ten malformed-input rejection
cases. Only that function was transpiled with the existing trusted TypeScript
parser and executed in an isolated context without app imports, fetch or browser.
Its regex validates timestamp shape, not calendar validity; backend ISO parsing
and timestamp CAS remain required. Source-level review verified original edit
snapshots, no mutation replay and read-only refresh after conflicts. No component,
frontend build/typecheck, browser or request-serialization end-to-end claim.

## Go/Python asset-DSL differential

Of 212 fixtures, 208 matched and four documented Unicode-whitespace cases differed
on manifest `a0f7ea7f04064ad12c40c019636d749798a9704b51d0f65802196227709483e5`.
Python treats EM SPACE and NBSP as whitespace; Go's bytewise tokenizer absorbs or
rejects those cases. Acceptance, whitespace-normalized SQL structure and exact
ordered parameters were compared without executing SQL. No unexpected mismatch
was found. This proves neither SQL row semantics nor task-scope authorization;
callers must still parenthesize OR expressions before adding scoped predicates.

## Scoped command-history reads (stage 60)

Three actual PostgreSQL groups passed on manifest
`16b08402586b32df3f680aed31def1c32576d9b7a40541e8766e58d3444fbac8`.
Exact result pairing, ambiguous duplicate rejection, source DTO/filter/statistics,
separate raw-read authority, SQL text bounds and archive refusal were verified.
Only synthetic activity text was used; no command was executed. The source `task`
query parameter denotes an exploration ID, not a task ID. Stricter result pairing
prevents source cross-scope/duplicate matches rather than reproducing them.

## Visibility metadata (stage 61)

Three actual PostgreSQL groups passed on manifest
`a606b32baa1c6b642ff02301f355a501a4e808e40bcf937f873067aabbd08a17`.
Duplicate agent IDs, MCP all-tool-row disable, skill last-write-wins/default-false,
explicit authority, existence locks and postcommit uncertainty were verified.
An unsupported fixture role was corrected without source changes. Metadata can
influence available tool selection upstream, but it is not execution permission;
no runtime catalog, seeding or permission activation was performed.

## Go/Python health-registry differential

Twenty-five synthetic state transitions matched on manifest
`a22ae037b74f01b5ae6def22fd8e198ab7b9859d8ca65f7be17021a28fd11ec9`.
Counts, states, errors and callback counts matched; relative cooldowns matched
within 0.1 seconds. No persistence-ordering or concurrency parity is claimed.

## Reusable disposable PostgreSQL runner (stage 62, reviewed)

The optional `python/integration/local_postgres_acceptance.py` harness now makes
current bootstrap-to-completion and representative token/command reads reproducible.
On manifest `7980cc61554f01ee3926281b5cda61596c5f9c499e9b7ed85a88ecd2cd00d1f0`,
both actual PostgreSQL groups passed, a populated-schema rerun refused, and three
independent guard tests passed, including optimized-interpreter refusal before
connection/schema effects. Fourteen pure argument/environment/path cases also
passed. Literal-loopback connection was exercised; actual Unix-socket startup
was blocked by the sandbox, so only its path guards were tested. Isolation was
not relaxed. The runner never installs dependencies, starts servers or creates/
drops databases. It leaves fixtures for operator disposal and requires a fresh
explicitly confirmed local test database with no implicit credentials.

## Pool-health and retry-policy adapters (stage 63)

Three actual PostgreSQL/Registry groups and twelve focused tests passed on the
same stage-62-reviewed manifest. Validated wire timestamps, bounded profile data,
error redaction, real settings writes, immutable approvals/idle policies and
single-profile resets were checked. Retained/cross-thread application callbacks,
SQL trigger rollback, postcommit coordination uncertainty and reset persistence
failure were exercised. No provider calls, real keys or default enabling. Reset
all remains explicitly unavailable in this subset.

## Registry reset-all (stage 64)

Three actual PostgreSQL/Registry integration groups and 39 focused test executions
(including inherited repeats, not 39 distinct tests) passed on manifest
`4ece1b21fa4cfb2c707e36a68c84aa86556e3de60a01752bb40f95982e3c130b`.
Bounded registry inventory includes orphan IDs, immutable full-inventory approval
precedes atomic memory clear, and revision/callback/authority fences surround
individual persistence effects. Concurrent additions remain untouched. Partial
persistence failure stops later callbacks and requires host reconciliation: there
is no durable recovery journal and no safe automatic retry guarantee. Real settings
rows were synthetic callback observations, not production persistence wiring.

## Task-local audit (stage 65)

Thirteen reviewed tests and four actual Guard integration groups passed on manifest
`55d370a8ffac4bcb99d439098bd37ab2d3abd65f7ad86abd993221e72e12415a`.
Chronological 2000-entry ring eviction, detached snapshots, attribution counters,
missing-task response, selection/raw permission revocation, binding checks and
UTF-8/JSON bounds were checked. No tool executed. Entry and attribution snapshots
are not claimed atomically consistent. Optional application composition was later
independently reviewed with 19 audit tests; that composition is not part of this
stage's immutable acceptance manifest.

## Durable intercept inspection (stage 66)

Initial acceptance found disclosure after raw permission was revoked during SQL
fetch. The unchanged negative-case harness passed after post-fetch and final
response-time grants were added. Final manifest
`ff1e07244804c1c6fd91c9b15d873260f484f999eefbd3db81a62e9740b5bdff`
passed four actual PostgreSQL groups and 21 focused tests. Exact text task keys,
join/pagination/count snapshot behavior, bounds and raw grant revocation were
checked. No approval decisions, live waiters or default activation were tested.

## Expanded delivered acceptance runner (stage 67)

On manifest `f1960e525613ac360b0ec4125978afc3bc3decd569f527879aae9d8763bd0aa2`,
three real PostgreSQL groups passed, the populated-schema rerun refused, and
three unchanged connection/optimization guard tests passed. The delivered runner
now adds exact-text intercept inspection and post-query raw-grant revocation,
Registry timestamp/redaction reads, and Guard chronological eviction. This is not
visibility mutation, pool reset, approval-decision or production-runtime coverage.

## Command/token disclosure authorization (stage 68)

Manifest `316ce6f9c1605777867e54390ddb71254393809312298d4c85d611831b546b48`
passed two actual PostgreSQL groups comprising seven cases, plus 24 focused tests.
The four originally failing command/raw/request and token/store/request revocations
now deny without data, as does command metadata revocation; two positive controls
retain valid reads. Exact service interfaces use transaction-bound actual SQL.
This is scoped evidence, not blanket clearance of all readers. Additional reader
findings and independent review are documented separately.

## Coverage disclosure fences (stage 69)

Manifest `dc4bb563ea8609e4fb8d2cc00e295f6f1a7a283a823ef07372ef43e30718eb15`
passed two actual PostgreSQL groups (five cases) and 40 focused test executions.
Direct-source, HTTP and company-connector grants revoked after actual queries now
return 403 without aggregate/label disclosure; positive aggregate and graph controls
remain valid. Final accumulated grants and response serialization sequentially
reacquire the host guard. No default activation, target testing or tool execution.

## Finite remaining-reader acceptance (stage 70)

Manifest `79861539767e934227e6b07da76f607af5d2b6fd70efdd587ed5823a92effab0`
passed thirteen actual PostgreSQL/file/Registry cases and 39 focused test executions.
Task-scope and rule direct/HTTP lists, recording pickers/body, workspace files and
pool diagnostics deny post-read revocations while positive controls succeed.
Original read-policy callback identity checks were included in the reviewed
scope/rule snapshot. No lifecycle, production runtime or blanket-reader guarantee.

## Final callback-pin PostgreSQL rerun (stage 71)

Manifest `53585dde2756079d22ba4ffa99b831218dda3b2586d6cd31b28f81f8f1f8d7cc`
includes the final command/token/intercept original-callback pins. The unchanged
stage-68 seven actual PostgreSQL cases and stage-66 four groups all pass, along
with 47 focused test executions. Each harness used its own fresh disposable
database. No additional workload or broader safety guarantee is implied.

## Intent-to-asset provenance (stage 72)

Manifest `79422e4e28450f36a7fd625ed697f8d3847b40879361d5b8ea77fc5ee1e270bf`
passed three actual PostgreSQL groups and 17 focused test executions. Source-shaped
provenance, local-open/direct-terminal/nonrecursive contexts, source/request
revocation, callback replacement, archive refusal and SQL UTF-8/row bounds were
checked. Initial transaction-exit and serialization disclosure failures were
preserved and their unchanged regressions now pass. This acceptance covers the
service/store, not subsequent optional application composition or target execution.

Intent-assets optional factory composition was independently checked on manifest
`b9aa305c892b1ffe8d8d4494731cda1cbdc2185bb9c8d3150bfddd8bf72a6422`:
17 collected tests pass, including two additional private-binding/JWT-order
regressions. No listener or external provider was started for this composition.

## Internal owned-clock protocol (stage 73)

Manifest `d0b8d09a9368b8b908cf676e4b4f2caaefc4251e6079b78a1de1fd134404e772`
passed three actual PostgreSQL groups and 19 focused tests. Actual authored
bootstrap uses fixed inert in-process responses, not a live provider. Natural
prepared-zero clocks cannot become positive by changing only manager/SQL rows.

Positive protocol tests explicitly modify private synthetic prepared-timeout
fixtures as well as manager/SQL state. Within that test-only setup, registration
retains an exact durable lease; pause revokes old execution while preserving a
read-only suspended clock. Validation performs two SELECT FOR SHARE statements
and no UPDATE. Copied leases/stale journals reject; actual trigger rollback leaves
quarantine and refuses retry. This is not supported positive-timeout runtime,
automatic expiry/resume/final assessment or production recovery acceptance.

## Main-session metadata (stages 74–75)

Manifest `ca45aa1599b8d72387c0f6847353666906ed6a9b017a4e5c810410b412dbf23c`
passed two actual PostgreSQL groups and 18 collected tests (including repeated
imported owner tests). Descending sequences/implicit zero timestamp, exact scope,
revocation and policy replacement, row bounds and archive refusal were checked.
No session creation/reset, provider calls or execution.

Optional factory composition independently passed 18 collected tests on manifest
`5b35dd6bd56f63fd60d38f38b3ffe4c95c3ad9392fec6ce9fed487021d6e9081`, including
two added private-binding/actual WSGI JWT-order checks. No listener was started.

## Persisted-role runtime factory (stage 76)

Manifest `5bc709262572e26df22110b8576f489215d6eb7dd7eb61ebdee3842034c56682`
passed two actual PostgreSQL integration groups and 28 focused tests. The actual
TaskRuntimeAssembly/application, ProfileFactory/OpenAI parser, canonical scoped
transcripts, bcrypt/JWT and explicit EnginePump were used without consumer-written
lifecycle factories. Injected in-process HTTP responses are fixed synthetic data;
no provider socket, paid model or external target was contacted.

JWT creation remains transport-inert until owned ticks. Two goals, four planner
and two worker completions produce persisted intent, harmless local fact and
structural proof, then manager completion and handle retirement. HTTP DTOs exclude
the synthetic credential. Fixture policies approve these local graph facts; this
is not empirical truth verification. Negative profile grants, model drift,
pre-tick cancellation, post-preparation drift/unresolved retention and exhausted
chain refusal all pass.

Full-stack acceptance is completion-only (streaming=false). Focused provider
stream tests are not full-stack streaming evidence. No positive timed execution,
background pump thread, listener/browser, initialized-task adoption, multi-process
or production-readiness claim. Initial harness import typo was corrected without
product changes.

## Task finding projection (stage 77)

Manifest `5f6a47b4ebfd615d6d3abbfd6f199bac791a74ccf20ebcfc317d636b97d80eea`
passed three actual PostgreSQL groups and ten focused tests. Source DTO/local and
direct-source provenance, asset labels and additional asset grants, post-fetch
revocation/policy replacement, archive refusal, SQL UTF-8/row caps and ambiguous
metadata refusal were verified. Initial fixture used an invalid finding state;
correcting it did not require product changes. No global reads or mutation enabled.

## Task progress and fixed-task overview (stage 78, final batch fix)

Manifest `c25af357827552e3c2e460c894a71ffa4036683d6b48ca1692522d3571b103b4`
passed five actual PostgreSQL groups and 29 focused tests. Actual runtime terminal
projection produced three rows with real fixture usage totals 6/6; repeating a
receipt added no usage, and edited persisted payload refused reconciliation.
Authenticated fixed-task overview returned actual nonempty intents/findings.
History/detail/SSE replay, reconnect/close, nested null/scalar/array metadata and
later-batch grant revocation were exercised. The latter withholds buffered output
after the actual batch-specific grant is revoked, not only the original selector.

Projection is terminal-result progress, not live model-token capture. Injected
metadata fixtures are distinct from runtime-generated activity. No original
frontend build/browser, live providers, automatic all-task scope or unbounded
stream service was tested. Optional factory composition is reviewed separately.

## Dynamic authorized task reads (stage 79, final policy/auth fixes)

Manifest `59591807de5a902629f740670723fc4f07a27af466b136add7b5d0f5a63d20df`
passed five actual PostgreSQL groups and 26 focused collected tests. The optional
runtime wrapper received one dynamic read router before either task existed.
Two HTTP-created tasks completed using fixed inert provider responses; the same
unchanged application returned each task's real stats, intents and activity.
Denied selectors and a replaced builder policy tuple refused before task SQL.
Retained SSE streams stayed scoped across other-task reads and reconnects; selector
revocation or persisted exploration retargeting closed them. Authentication
callback replacement during profile serialization or first-frame SSE construction
withheld disclosure.

These are the listed Python HTTP contracts, not an entire frontend/browser pass.
SSE remains finite committed terminal activity replay, not token streaming. No
live providers, external targets, default grants, background pump, timed runtime,
inherited activity or restart recovery were tested. An initial provisional fixture
violated the exploration ownership unique constraint; the final fixture used a
fresh unowned exploration without changing implementation behavior.

## Goals initialization terminal activity (stage 80)

Manifest `056d2d0fc47eee23fa7f7187f980896e732e1d046b7c80fd7deea9d15f3ef65e`
passed four actual PostgreSQL groups and 12 focused tests. Actual runtime now
projects four terminal receipts across three roles: goals, planner, worker,
planner, with fixture usage totals 8/8. Duplicate receipt projection adds no
usage; edited persisted detail refuses reconciliation. A temporary PG trigger
rejecting goals activity insertion leaves the runtime unresolved with retained
ownership and no repeated inference, even after removing the trigger.

Separate explicit GoalPlanner same-key reconciliation reuses the canonical JSONL
receipt, creates one activity, and adds neither inference nor duplicate goals on
repeat. This is fixture-authorized core reconciliation, not automatic runtime
recovery. Terminal progress is not bootstrap success or task completion; only the
successful runtime's authoritative graph checks establish completion. All data,
providers and DDL triggers were inert disposable fixtures. No external targets,
positive timeout, restart recovery, live token capture or production execution.

## Stage 81: dynamic main-session frontend boot sequence

Immutable manifest `7524c2f9f3f5b1b6055a12f360e5dbd7b31bb5965639437249a8eada77888c57`.
Three disposable PostgreSQL groups and seven focused tests passed. One application
serves two tasks through main-session metadata, current `main:<seq>` history, then
snapshot-cursor SSE. Cross-task isolation, reconnection and cancellation are
verified. Main-session/activity rows are explicit synthetic fixtures, not evidence
of main-chat execution. No reset/message submission or original frontend execution.

## Stage 82: owned deadline freeze/drain prerequisite

Immutable manifest `a209c5ca19f3f24a8794730cb716bb47ecf91623413c58cefdee72be0e623278`.
Two disposable PostgreSQL groups and twelve focused tests passed. Actual registered
lease freeze adds no SQL, status, clock or activity changes. Ownership remains
retained; new admission/advancement is denied; exact pins and pending rechecks are
verified. The fixture explicitly injects a positive prepared-timeout pin: this is
NOT working positive-duration runtime. There is no expiry scheduler, final model
assessment, terminal-write capability, automatic release or recovery claim.

## Dynamic goals GET (stage 83)

Manifest `96d17d24cb704ac415ca39b7a5720bedabaf4c4a8fd24e3933b83ff6d22c82e6`
passed four actual PostgreSQL groups and ten focused tests. One unchanged runtime
application returns each of two tasks' own persisted met-goal source DTO through
explicit dynamic read grants. Response-time grant revocation and original JWT
callback replacement withhold disclosure; denied task selection does not affect
the other task. An oversized persisted UTF-8 payload refuses503, and POST does not
enable goal mutation. Reads make no model calls. This is Python HTTP acceptance,
not frontend/browser, live provider, automatic completion or goal CRUD coverage.

## Final-assessment journal prerequisite (stage 84)

Manifest `b124112e09d940196a1d3ec000582c63e629f1a86c18f3d6e3a0d0e1249754c8`
passed four actual PostgreSQL groups and nineteen focused tests. Fresh additive
installation refuses wrong databases and existing objects. Exact expired owner
reservation, phase CAS, canonical inert JSONL recording and read-only inspection
were exercised; stale receipt/config/bootstrap identities refuse. Actual trigger
rollback retains quarantine and disallows retry without clearing ownership.
Positive clock configuration remains a private synthetic prepared-pin injection,
not admitted timed runtime. No assessment model runner or terminal permission is
implemented or claimed; canonical test results were locally authored inert data.

## Dynamic coverage, graph and asset references (stage 87)

Manifest `ecac1991abd088cbbf365863dde28cf17781fccea0f5f512bea3ce9665df7810`
passed three actual PostgreSQL groups and nine focused tests. One unchanged
runtime application resolves two tasks' distinct synthetic scopes/assets with
actual coverage counts and graph company connectors. Asset-reference DTOs retain
local versus directly inherited terminal-intent provenance. Explicit asset
callbacks revoking previously granted source or connector authority return403
without graph content. One task's selector denial does not affect the other.
Reads invoke no models/testing; POST does not enable mutation. All assets/scopes
and provider responses are inert fixtures, not target tests or default permission.

## Restricted final-assessment runner (stage 88)

Manifest `826015fd8fe67bd712b9b20e98134a2c34bc136589e5d5b1b13634628034ad71`
passed five actual PostgreSQL groups and twelve focused tests, including the
post-turn execution fence. Actual native bounded list_goals, two inert provider
turns and canonical JSONL produce a final journal receipt without changing task
status or granting terminal authority. Model/read grants, expiry and generation
drift deny access. An actual final journal UPDATE failure quarantines ownership;
removing the failure does not permit retry or repeat inference. Positive timeout
configuration remains explicit private test-pin injection, not admitted timed
runtime. No live models, terminal settlement, automatic recovery or production
readiness claim. Initial denied-read fixture was corrected to expect the actual
safe one-turn stop rather than a second model call; no product change was needed.

## Explicit development runtime loopback transport (stage 89)

Manifest `f782a3eaadbefab54b4b8748a45a6e39923929057c96126d9878bed60136f305`
passed four actual isolated loopback/PostgreSQL groups and nineteen focused tests.
Real127.0.0.1 HTTP/JWT creates a task without inference; explicit pump start then
completes the real graph runtime with inert responses. Finite SSE permits concurrent
pause/read. Raw duplicate auth/length, Transfer-Encoding, network-path targets,
foreign/cross-port Origin and oversized bodies are refused. An outstanding HTTP
callback causes truthful unresolved shutdown with the pool retained; releasing it
and retrying the explicit idempotent owner closes resources once. An initial
fixture callback that paused an already-paused task was corrected; no product
change was needed. This unresolved case is HTTP, not an ambiguous engine run.
No public interface, forwarding, live model, real credentials or original frontend/
CLI execution. Same-origin dev transport only; no production deployment or arbitrary
blocking-provider cancellation guarantee. Host shutdown callbacks are explicit
fixture authority, not a generic inferred lifecycle policy.

## Exact final-settlement prerequisite (stage 90)

Manifest `48c5f128b5c17611ff67d6ee6b90c0ff147b50c7c781e9ba00fed10e8e6b92fa`
passed five actual PostgreSQL groups and fourteen focused tests. Fresh local proof
produces done; an open goal produces timeout. Task and settlement receipt persist
atomically, followed by exact owner retirement. An actual receipt INSERT failure
rolls back both. Final-policy changes to committed task status, receipt presence
or assessment-journal revision retain quarantined ownership and report uncertainty
rather than false retirement or rollback. Synthetic expired ownership is explicit
private-pin setup, not ordinary positive-duration admission. Inherited/goalless
settlement, external concurrent graph writers, recovery and live targets remain
unsupported. Initial fixture plain text in JSONB was corrected in the harness only.

## Stage 91: explicit in-flight cleanup with real PostgreSQL

Immutable snapshot manifest SHA-256:
`98cd0a4ebfd4829f9b8926e6a128ac52849f225a90260799d6e936e5f32b5f27`.
Five zero-timeout integration scenarios passed in isolated fresh PostgreSQL 17,
using actual runtime assembly, JWT task creation, installed bootstrap/engine,
canonical JSONL and activity projection with fixed inert provider callbacks.
Planner and worker cancellation produced cancelled canonical receipts and durable
activity, then released cleanup ownership. Explicit task-pause maps the worker
intent to open, not the blocked mapping used without that cause. During active
execution pause(timeout=0) first raises TaskConflict after cancellation-gating;
only the subsequent drained pause persists paused. Revoked cleanup permission,
actual activity INSERT failure, and cancellation between CREATED and reservation
retain unresolved ownership without provider replay (the last performs no model
call). All 27 focused cleanup tests passed.

A sixth real-PostgreSQL case verified that synthetic positive prepared-timeout
pins still cannot pass the engine's timeout_seconds=0 SQL guard: no provider or
cleanup reservation runs. This intentional refusal is not integrated timed-freeze
acceptance. Freeze protocol coverage remains focused SQL-double evidence only.
No guard was bypassed, no fabricated active-owner flags or real providers/targets
were used, and no product source was changed. Synchronous callback cancellation
is not proof of arbitrary network-provider preemption or cross-process recovery.
Harness-only corrections fixed capture argument position and task-pause mapping.

### Stage91 exact cleanup-only dependency rerun

Before checkpoint publication, the same six stage91 cases and 27 focused tests
were rerun against the exact cleanup-only frozen index, excluding later clock,
RuntimeContext and transport changes. All passed without source overlays or
harness changes. Manifest:
`e729a65b98a3b93a77986773d3b291b22b9c3b2085f2c8514869e1424f0b91a3`.
The earlier moving-source stage91 result remains separate evidence, not relabeled.
This exact rerun still verifies refusal, not execution, of positive timed entry.

## Stage 92: exact clock34 provider component, real PostgreSQL

Source: exact frozen clock/provider checkpoint, without later observer changes.
Manifest SHA-256: `577d2331e53f1b8737c0e91327ba35ab9380d7f55d81fc236e6207be39abe586`.

Three actual PostgreSQL 17 component groups passed in the existing credential-free filesystem/network isolation, with synthetic prepared positive-timeout pins and fixed inert provider methods. No timed engine advance or guard bypass occurred.

1. Shared OwnedGenerationClock and actual DeadlineProvider/RuntimeContext: streaming construction and unstarted close did not stamp or call provider. First next stamped once in real SQL. A genuinely distinct worker model identity/name/profile and fresh context reused the exact persisted clock, despite backwards wall time. Its monotonic ceiling did not extend; later expiry rejected the next event and closed the iterator once.
2. Original iterator close revoked its binding grant. Explicit close surfaced DispatchClockUnavailable, did not silently succeed, closed once and did not repeat provider effects.
3. Actual clock stamp committed, then the fixture raised acknowledgement uncertainty. Shared clock latched unresolved; the worker role could not retry or invoke transport, and actual first_run_at remained persisted. Rejection was DispatchClockUnavailable at the shared ownership fence, not necessarily DispatchClockUncertain. The original overly specific harness expectation was corrected; initial log retained.

Fifty focused collected tests passed on the same snapshot: owned_generation_clock, task_deadline_provider and review, runtime_clock_review, runtime_deadline_clamp, provider_deadline and review. These include mocked HTTP duration/stream checks; no live HTTP model provider was used.

Limitations: component-level opt-in only. Synthetic private prepared timeout pins remain necessary and do not establish natural positive-duration admission, default assembly wiring, first-goal deadline behavior, external transport preemption or deadline finalization. Distinct contexts share the same explicitly injected monotonic clock. Postcommit uncertainty is an injected acknowledgement failure after a real successful SQL write, not a simulated PostgreSQL server crash. No production credentials, infrastructure, external targets or source edits.

## Stage 93: exact observer35 manual expiry component

Source: exact frozen manual-observer checkpoint without later assembly changes.
Manifest SHA-256: `75ecef3830b98db76924b9c74d66f1dbc572af8c079b5dc4b77dcd705dafb109`.

Three actual PG17 integration groups and one intentional entry-refusal case passed in the existing credential-free network/filesystem isolation. Ten focused observer tests passed on the same snapshot. All fixtures local and disposable; fixed inert provider only for stamping. No product changes or guard bypasses.

- Observer constructor/unstamped tick did not stamp. Actual shared dispatch stamped a synthetic prepared owned clock once. A conservative injected local monotonic ceiling expired before the actual database deadline: observer froze and drained the idle exact owner, returned waiting_database normally, then after actual database time elapsed returned ready. Persisted timestamps stayed identical, task stayed running, token remained uncancelled, no assessment allocated or final model/action performed.
- Pausing the stamped original owner produced a suspended/cancelled owner. Actual database expiry was subsequently observed as ready without new stamp or reset.
- Two final observation callback negatives (owner phase changed to quarantine; freeze grant revoked) refused readiness and retained quarantined ownership after actual SQL reads. These deliberate internal-phase fixture mutations test callback fences, not public mutation APIs.
- An independently run genuine host→outer→engine fixture with synthetic positive prepared pin and actual positive timeout row was refused by the engine timeout_seconds=0 SQL guard before any provider or cleanup reservation. Therefore an actual blocked timed provider/independently ticking observer cannot naturally be reached in this checkpoint. No fake active flags or SQL-result overrides were used to force it.

The focused ten tests include blocked-provider threading against SQL doubles; that is protocol evidence only, not real-PG timed execution. Normal positive-duration admission, default observer scheduling/threads, final assessment, production deadline support, arbitrary provider preemption, and recovery are not established. The real-PG observer tests use explicitly synthetic positive prepared ownership and a near-expiry persisted fixture clock. No external targets, credentials or user data.

Artifacts: source manifest, actual component and refusal harnesses, two result logs. No fixture/product corrections were needed in this stage.

## Stage 94: exact timing36, natural zero-timeout application binding

Immutable source: exact frozen timing-assembly checkpoint without later finalization work.
Manifest SHA-256: `d4e5c61791624534871df4f5db9e1cb9f667d0326ca97a3df8cc9800fce0ad65`.

Three actual PG17 authenticated application scenarios passed, each in a fresh disposable database inside the approved credential-free filesystem/network isolation. Fixed inert in-process OpenAI-shaped transport only; no live provider, external target or user data. Twenty-one focused timing/assembly/review tests passed on the same snapshot.

1. Explicit timing policy plus cleanup opt-in, natural timeout=0 HTTP creation: no provider before owned tick; actual goals→prepared child binds exact timing owner. Planner/worker use the policy's identical clock object. No TaskDeadlineStore or OwnedGenerationClock is created. No timing stamp/dispatch grants run. Actual planner intent→worker fact→planner proof completes normally; persisted first_run_at/deadline_at remain NULL. Provider counts goals2/planner4/worker2. Positive timeout HTTP creation then refuses before adding a task.
2. Explicit no-policy/default mode completes the same actual lifecycle with engine.timing absent, no grants or persisted deadline, and positive HTTP creation refusal.
3. A fixture mutates the timing policy clock at the real binding boundary, before calling the original bind method. Binding refuses, actual child and unresolved outer owner remain retained, task stays running; only the two goals provider calls occurred, no timing grants or planner/worker calls. This is deliberate immutable-policy corruption for a negative check, not a product code change or supported mutation API.

No synthetic positive fixture was needed or used in this stage. This is genuine zero-timeout application acceptance only. Earlier synthetic positive clock component evidence remains separate and does not establish public timed admission. Goals are not timing-dispatched, timing grants are not inferred, and no deadline default activation, observer scheduling, final assessment or production behavior is claimed.

No product edits or fixture corrections were required. Artifacts: manifest, harness and three logs.

## Stage 95: explicit intercept configuration read overlay

Manifest SHA-256: `c89c75e79a483142ab76c85f8d96c331764483080db5e1d4e2da3fb9229e6cc4`.
Source composition: frozen timing36 Python tree with exactly these three frozen intercept37 files overlaid, no moving-tree source:
- python/artex/server/intercept_config_read.py
- python/tests/test_intercept_config_read.py
- python/tests/test_intercept_config_read_review.py

Three actual temporary PostgreSQL 17 groups and all 15 focused tests passed in the established credential-free filesystem/network isolation. No HTTP route registration or runtime enforcement was performed. The new bound SELECT was parsed and executed by real PostgreSQL against the actual original settings schema, establishing its SQL syntax and parameter/column compatibility rather than merely string inspection.

1. Missing setting returns ordered defaults. Malformed JSON, non-array and non-string-array values return empty list. JSON null remains null, null array element becomes empty string, unknown names/order/duplicates remain intact. Escaped unpaired surrogates become replacement characters and valid pair becomes the expected Unicode character.
2. Actual multibyte setting exceeds 65,536 bytes. PostgreSQL returns only the NULL sentinel (observed result [(None,)]), service returns redacted500, and oversized text is not transferred. More than1000 elements is refused rather than truncated.
3. Original read grant revoked immediately after actual fetch yields403 without the private marker. Already denied read skips SQL. Original guard exit revocation also yields403 without metadata.

No source edits, fixture corrections, model calls, targets, actual credentials, application wiring, tool permissions or enforcement changes. Focused tests additionally cover serialization/policy identity behavior; this is a bounded metadata-reader check, not blanket authorization assurance.

## Stage 96: global tool-input rule metadata reader

Manifest SHA-256: `5f0e042ddff05bee4e28c84f370c535b3c797a3f5aeec5f15a43a35a1ba33314`.
Base: exact stage95 config37 composition (timing36 plus its three intercept config files). Added only the reviewed db/server tool_intercept_read files, owner and independent review tests, and TOOL_INTERCEPT_READ_STATUS.md. No evolving finalization source included.

### Review and correction

Source Go db/intercept.go confirms the same fourteen fields, disabled-rule retention and priority DESC/id order. Original schema text columns are NOT NULL, making the cumulative byte expression appropriate. No matcher or regex execution is introduced.

Independent standard ZoneInfo UTC datetime regression initially failed500 because the reader accepted only exact datetime.timezone. Author corrected support to exact stdlib timezone or ZoneInfo while retaining rejection of arbitrary formatter subclasses. The unchanged three review cases now pass together with six owner tests (nine total). This runtime's actual PostgreSQL connection returned datetime with datetime.timezone; ZoneInfo support is separately verified by the focused regression, not misreported as the observed driver mapping.

### Actual PG17 acceptance

Three groups passed in the approved credential-free disposable filesystem/network isolation:
- Original schema, actual timestamps, fourteen source fields, disabled state, descending priority/stable ID ordering, negative timeout integer, raw invalid-regex pattern/message preservation, microsecond RFC3339 output. Query parsed and executed with actual parameter binding.
- Oversized cumulative UTF8 text caused every text projection to return SQL NULL before transfer (three actual rows inspected). Entire response refused500, no truncated policy. Row cap overflow separately refused.
- Original grant revoked after actual fetch or at final guard exit returned403 without raw marker. Already denied read skipped SQL.

No product edits by QA beyond separate regression tests; no HTTP registration, model/provider/target access, regex matching, cache activation, mutation route, or production claim. Temporary fixture inserts/updates only. No integration harness correction required.

## Stage 97: exact final39 manual finalization, real PostgreSQL

Manifest SHA-256: `995de159ecc9be7ce6493d55d5341accb565fae6c8f896ab44f8f57883051407`.
Source is coordinator-frozen final39 Python tree, no working overlays. Existing approved credential-free filesystem/network isolation, temporary PG17, canonical temporary JSONL and fixed inert provider used. No target, real credential or production state accessed.

Five actual integration groups passed:
- Before explicit additive installation, missing final journal schema retained a quarantined handoff with no provider call or retry. Schemas then explicitly installed in the same disposable fixture database.
- Genuine manual observer→assessment→canonical restricted final result→settlement completed one-shot. Actual confirmed local fact/proof and met goal produced done; an open goal produced timeout irrespective of identical model prose. SQL status and exact manager retirement verified; observer reported transferred and second run refused.
- Actual not-yet-expired database clock returned an ordinary preallocation wait with no assessment row/model call. After time elapsed, revoked start permission still prevented allocation/inference.
- Separate thread cancelled while inert final provider was genuinely blocked on an Event. Cancellation was accepted without prematurely returning the provider future or cancelling the original generation token. After explicit release, canonical cancelled outcome persisted, task remained running, no settlement row, owner quarantined, exactly one provider call.
- Actual settlement committed, then injected transaction-acknowledgement error occurred. Durable done/receipt remained; manager owner stayed quarantined, original token was not cancelled, and second run could not repeat inference or writes.

Twenty-seven focused finalization/observer tests passed on this exact snapshot. Initial harness lacked a tempfile import; corrected the harness only and retained original failure log. No product source changed.

All positive-timeout owners here require explicitly synthetic private prepared-timeout pin and persisted clock fixtures. No positive-duration public admission, timed engine gate bypass, complete timed application, default scheduling, live model, restart recovery or production guarantee is established. Assessment provider emits fixed local text; graph truth remains authoritative. The postcommit case is an injected acknowledgement failure after a real SQL commit, not an actual PostgreSQL crash.

## Stage 98: global judge-usage metadata read

Manifest SHA-256: `155458d6306b771036ea8b80acd881a3905363e1d6106f50a7932578e1c62d54`.
Exact final39 Python base plus only reviewed db/judge_usage_read.py, server/judge_usage_read.py, owner test, independent test_judge_usage_read_review.py and JUDGE_USAGE_READ_STATUS.md. No evolving runtime/registration overlay.

Independent source review matched Go db/llm_usage.go JudgeUsageStats: all-time judge ledger totals including failed/interrupted calls, UTC daily grouping and rolling days filter only on daily series. Python deliberately combines both source queries into one statement snapshot, caps requested days/daily rows, and fails rather than returning a truncated policy-sensitive result. Original ledger token columns are INTEGER, so SUM returns exact BIGINT; no floating conversion. Projection contains no raw payload columns.

Eight focused tests passed, including two added independent tests for exact integers above 2^53/inconsistent total rejection and final serialization policy-identity replacement refusal. No product correction needed.

Three real PostgreSQL 17 groups passed in approved credential-free disposable isolation:
- Explicit recording schema installer, empty-ledger zeros, old judge call included in all-time but excluded from daily, failed/interrupted included, worker calls excluded, exact 4,000,000,100 aggregate, same UTC series under Pacific/Honolulu session timezone. Actual bound SQL parsed/executed against original recording schema.
- Synthetic future ledger rows exceed daily-row bound: whole read refuses500 rather than silently truncates; removing only those synthetic rows restores successful read.
- Revoking original grant after actual aggregate fetch returns403 without totals; initial denial performs no query.

No route registration, inference, real records/credentials, external targets, default metering permission or production claim. Three explicit synthetic fixture groups only; no concurrency snapshot race or archive-merging claim. All-time aggregates scan the ledger and the single snapshot is a deliberate consistency improvement over Go's two statements. No harness corrections required.

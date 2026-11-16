# Authorized successful usage decoration for the skill catalog

The catalog now optionally accepts an exact `SuccessfulSkillStatsReader` through
`usage_reader`. Its explicit `UsageScope`, global grant, lifecycle/current policy
and executor remain independent of filesystem/catalog authority. Existing
callers without this reader retain the documented unknown-usage zero fallback.
An attached reader's denial, failure, malformed archive or bounds violation does
not silently become zero totals: it fails the catalog request closed.

The source reference is `db/skill_usage.go:SkillStats` and the existing archive
merger. One SQL statement groups found-only hot ledger rows (calls, distinct
nonnull task IDs, distinct agent keys and latest timestamp), unions eligible cold
task aggregate summaries via archived tasks, and bounds returned row/text volume.
Python uses the existing bounded archive decoder and UTC-aware merger before
returning the entire aggregate. No top-N truncation may silently drop a catalog
skill. Cold task counts are additive, not globally deduplicated; legacy skill
maps contribute one task per skill/archive. Modern successful stats take
precedence over legacy fields, and missing-skill calls are not counted. Agent
ordering follows source aggregation/merge behavior, not a promised usage ranking.

Catalog rows match usage by directory identity, not frontmatter name. Actual
calls/tasks/usage_agents and last_used replace fallback fields only for matching
skills. Uninvoked directories remain zero; ledger-only skills do not create
filesystem nodes. Updated payloads retain the catalog's total JSON byte cap and
sealed payload integrity. Instruction bodies remain absent and inert.

Ledger read/disclosure uses the independent scope and guard, with exact bound
receiver/function anchors and identity pins. Final ledger-current observation
follows catalog/content-current phases, then final request/global and child
content/ledger authorization sweeps precede pure receipts. The HTTP overlay
already repeats that source-authority sweep after its last outer callback and
checks JWT afterward. Post-header ledger revocation withholds buffered bodies;
already-sent headers cannot be replaced. Final authority callbacks are cooperative
observers, not arbitrary mutually revoking state machines.

Validation: 18 new tests and all 238 skill-focused tests pass. Coverage includes
found-only/distinct-task SQL shape, one-query reads, hot/cold/legacy totals,
missing exclusion, no top-N truncation, malformed/overflow/bound failures,
current/guard/fetch revocation, actual catalog/HTTP decoration, no error-to-zero
fallback, no query on ledger denial, no requery at disclosure, post-header ledger
revocation, final outer revocation, final ledger-current request revocation,
foreign receiver substitution and attached-reader replacement. These tests use
inert SQL adapters, disposable files and real WSGI/JWT. Independent review cleared the implementation, including adjacent final
ledger-current/content-current/outer grant revocations and post-header ledger
executor/guard substitution. Its additional harness reported 3,823 tests and
522 parsed SQL statements without failures; this is separate review evidence,
not an exact staged-tree validation claim.

PG+FS162 passed nine actual PostgreSQL/filesystem integration groups and all 238
skill-focused tests. New coverage verified hot found-only (1 call), modern cold
(6 calls/2 tasks) and legacy cold (3 calls/1 task) merging to 10 calls/3 tasks,
combined agents and latest timestamp. Missing-only usage was excluded, ledger-
only names did not create filesystem nodes, directory identity was retained,
and only one SQL query occurred per response with no disclosure requery.
Independent ledger denials, post-header/final callback revocation, foreign bound
receivers, real SQL row/byte caps and sanitized malformed archives all passed.
The eight earlier catalog/composition groups were rerun, covering five-family
JWT/grants, filesystem mutations, metadata-only output, method/delegate integrity,
late expiry and atomic conflicts.

Report: `/workspace/shared/artex-qa/skill-catalog-usage-162/REPORT.md`.
Manifest SHA256: `46fb51fb186d62c5b913a09e0e7dd67dadfddfb87141b44ff432199f38acba90`.
Successful reader: `09fe79b6c4a4eefb5848e1e4cda0d8cf977549f0b820f425bdfe341fd026a1dc`.
Catalog reader: `cf1d8b73d2bc753aaf79c191dd4aaaf65480245f3044d0217da8fd01a6e98918`.
HTTP service: `2d1359b03db8a94ee8301e7629f811cc553b072399cd98641f7414bb6eb8549f`.
All manifest entries and changed runtime sources matched after tests. Evidence
uses a copied index plus explicit current overlays, not an exact staged tree.
The fresh PostgreSQL 17/temp-filesystem run used UID1000 isolated namespaces,
loopback only, read-only sources, synthetic fixtures/JWT and no host workspace,
home or real credentials. No shared source/index changes or publication occurred.

Initial PG+FS161 is retained: its integration setup failed because a synthetic
exploration ID was reused despite `tasks_exploration_id_key`. PG+FS162 corrected
only that fixture with a separate exploration. This was not a runtime failure;
161's 238 focused tests also passed. Neither run used a real HTTP listener/browser
or default application integration.

The SQL statement provides one database statement snapshot, not a transactional
snapshot shared with filesystem discovery. Existing cooperative writer/lifecycle
requirements remain. Output row/byte caps do not bound all server-side aggregate
work; hosts still need database execution/resource controls. No metering writes,
SDK execution, default wiring, full YAML support or production/full-application
parity is claimed. This extends the prior catalog implementation; prior PG+FS160
acceptance does not establish this new ledger integration. No staging/publication.

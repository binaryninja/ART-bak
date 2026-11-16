# Exact-task by-model metering composition

Optional TaskReadBuilder pair model_usage_authorize(scope) and
model_usage_request_authorize(request,scope) enables only authenticated
GET /api/llm/records/by-model?task=<exact selected ID> in dynamic composition.
Missing pair returns503; half configuration rejects. No generic task/activity
read grant implicitly enables this summary. No debug record listing/detail/raw
bodies, deletion, global usage routes, prices, costs, recording enablement or
provider calls are introduced.

Uses existing LLMRecordStore.token_by_model with a new optional bounded branch;
legacy callers without bounds retain their original path. Accounting is the
actual llm_usage metering ledger, not activity results or debug llm_records.
It includes all statuses and preserves raw GROUP BY model semantics. NULL/empty/
literal '(unknown)' can be separate raw groups with the same display label;
no normalized-label regrouping or deduplication is invented. Ordering follows
input+output descending then displayed model. No per-status success filtering.

The bounded branch caps returned groups before transfer (limit+1), aggregate
model-label bytes using a SQL sentinel and Python validation, and nonnegative
integer counts below2^63. Integral PostgreSQL Decimal sums are converted exactly,
never through float. Overflow, malformed aggregates and excess rows/bytes fail
rather than returning a truncated top-N success. Response size is independently
bounded. Queries still aggregate the selected task's ledger in PostgreSQL; this
is not a DB execution-time limit. Deployment needs query timeouts and indexing.

Parent task/exploration is revalidated under SQL lock with no archive/deletion
or archive job. This deliberately refuses unavailable/archived scopes instead
of pretending hot data is a complete archived summary. No active/global fallback
or inherited accounting. Scoped service reuses accepted receipt-aware delivery:
all current/guard callbacks precede raw request/source grant sweep, then selected
selector/JWT validation and pure pins. Postheader refusal sends no body after the
original headers. Finite order does not promise an arbitrary mutating-grant fixed
point, instantaneous network revocation or atomic cross-system snapshots.

12 new tests pass: eight dynamic composition cases (populated DTO/task navigation,
optional grants, denials/query errors, post-SQL/postheader/final-current revocation,
policy rebinding) and four bounded-store cases (raw grouping, limits, sentinel,
invalid numeric/Decimal values, exact scope and unchanged legacy path). Existing
record and token/intent composition tests also pass.

Independent read-only review cleared the candidate: 126 focused tests across
bounds/composition, existing records/delete/disclosure, token/intent/router,
listener and WSGI passed. Additional final postheader guard-exit revocations of
source/request/selector/JWT withheld the body. Receipt validation made no second
accounting query: exactly one ledger aggregate. Review confirmed raw grouping,
all-status accounting and refusal rather than top-N truncation.

Scoped PostgreSQL + HTTP QA191 PASS:
`/workspace/shared/artex-qa/frontend-model-usage-191/REPORT.md`
Manifest SHA256:
`58ea448b92efdcd76f50fb54100608d8464367cc7f1edacd84a92e27aef79e9c`.
This is a frozen index plus explicit candidate overlays, not exact staged-tree
or whole-tree acceptance. All manifest entries were reverified. QA190's fixture
index-size error is preserved; QA191 needed no product changes.

Disposable PostgreSQL 17 and private loopback HTTP exercised two genuinely
completed tasks through the original runtime with 16 inert provider calls.
Synthetic metering rows verified repeated-model aggregation (2 calls, 24 input,
12 output, 5 cache-read and 3 cache-write), task isolation, exact six-field DTO,
malformed selectors and independent denials. More than 500 groups returned503;
490 individually valid labels totaling over1MiB also returned503. Removing those
fixture rows restored200. Recording schema was explicitly installed for the test,
not implicitly enabled by this route.

All 14 deterministic actual-header listener-adapter probes withheld bodies:
5 immediate source/request/selector/current/JWT revocations; 4 calibrated final
scope-SQL revocations (third observation); and 5 final external-current
revocations (sixth observation, including JWT). These are not real-network race
proofs. QA191's 106 focused tests passed, not the full suite. Real-PG NULL/empty/
unknown duplicate-label grouping, omitted-route and archived-task scenarios were
not separately exercised in this run; their unit evidence remains distinct.

The 52-check frontend workflow still exits1: six unrelated404s plus empty bounded
SSE framing remain. Both token endpoints now return200 and clean shutdown passed.
No browser execution, full frontend build, default registration, production
readiness or complete UI claim.

Six remaining unrelated frontend families: scope, constraints, intercept rules,
chat status, task LLM resolution and task intercept history. Empty bounded SSE
live framing remains unverified. No stubs, staging or publication.

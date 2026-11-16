# Optional persisted task intercept history

Four explicit TaskReadBuilder options are required together:
intercept_history_workspace, intercept_history_authorize(scope,selection),
intercept_history_raw_authorize(scope,selection), and
intercept_history_request_authorize(request,scope,selection). Source selections
use the existing store tuple; request selection is TaskInterceptSelection.
Absent capability returns503. Metadata authority alone cannot disclose tool_input:
raw authority must independently approve before SQL and at final disclosure.
Denial returns403, not fabricated empty or silently redacted success.

GET /api/intercept/task/{id} reuses unchanged TaskInterceptReadService and
InterceptHistoryStore. Task key equals the resolved numeric task ID's canonical
text; SQL filters the persisted intercept_pending TEXT key. This live numeric-task
adapter is deliberately narrower than Go/standalone arbitrary text-key history.
Current checks bind task/exploration/revision and refuse deleted/archived scopes.
Existing joined rule/conversation labels and raw tool_input require the explicit
history/raw grants. No global history, mutation, live waiter/decision authority,
intercept-rule settings or execution permission is introduced. Persisted pending
rows are not proof of a currently waiting request.

Unpaged response is items/total; page/size/status/decision_source retain existing
parser and paged-envelope semantics, default20/max100 and bounded offset. Unpaged
result max500 and SQL selected-page bytes max1MiB fail closed; response JSON has
an independent1MiB bound. Oversize refuses rather than truncating success. Counts
and filters still run in PostgreSQL, not an execution-cost/time guarantee. HEAD
uses GET selection while preserving original request authority.

Wrapper receipts run after standalone service serialization/current callbacks.
Initial grants precede observations; final transport/current/guard phases precede
request/source/raw grants, then selected-task selector/JWT and pure bindings.
After-header denial withholds the body even when200 headers were sent. This is a
finite cooperative observation contract, not an arbitrary grant fixed point,
atomic snapshot or network-revocation guarantee. Metadata/raw callbacks are
trusted policy observers; no fresh row reread at transmission is claimed.
Unique history_* closure locals keep existing family policy cells separate.

Eleven new tests cover exact task navigation/DTO, unpaged/paged response, optional
and raw gates, malformed filters, postSQL/header/final-current revocation,
configuration pins and opposite coverage/rule/history/raw callbacks.13 existing
standalone intercept-task tests,10 rule-composition and11 constraint-composition
tests pass. Independent review cleared159 focused tests and extra final postheader
guard-exit source/raw/request/selector/JWT revocations, with one history query and
no response body on denial. SQL task text, page/filter/offset parameters and
separate raw authority were checked. The current-checkout full suite separately
passed3951 tests; this is not exact staged-tree acceptance.

Scoped PostgreSQL + HTTP QA200 PASS:
`/workspace/shared/artex-qa/frontend-history-200/REPORT.md`
Manifest `edbedb59473cf4804fbbc2cf806181e9d69694400177f0572e0218ee2ca13927`.
Frozen index plus explicit overlays; all manifest entries and three candidate
source identities verified against checkout. Disposable PostgreSQL17/private
loopback HTTP exercised two genuine original-runtime task completions,16 inert
provider calls and clean shutdown, with synthetic intercept_pending data only.

Actual textual task isolation, original raw tool_input projection, metadata/raw/
request denial403 with zero history SELECTs, paging/filter totals and malformed
queries passed. Raw denial is not redacted200. Page-size100 clamping remains unit
evidence, not separately exercised in this PostgreSQL run. Above500 unpaged rows
and fewer than500 rows exceeding1MiB textual metadata refused500 using the existing
standalone service error semantics; cleanup restored200. No truncation or
execution-cost containment claim.

All54 real HTTP opposing source/request combinations passed across nine optional
families: coverage, constraints, scope, model usage, token usage, intent assets,
goals, main sessions and rules. When raw history permission was denied, all nine
other families still returned200. Findings/activity pairwise isolation against
history was not repeated; this is not exhaustive interaction coverage.

All17 deterministic actual-header listener probes withheld bodies:6 immediate
revocations,5 final scope-SQL revocations (third observation),6 final external-
current revocations (sixth observation). Each group includes independent raw
permission; immediate/final-current groups include JWT. These are not network-race
or arbitrary policy fixed-point proofs.157 focused tests passed (123 composition/
transport plus34 history), not a full-suite run. Previous cumulative checks passed.

The52-check frontend workflow still intentionally exits1: interceptTask now200,
but chatStatus and taskLLMResolution404 plus bounded empty SSE framing remain.
No browser/React execution, default registration or production-readiness claim.

Chat status and task LLM resolution remain unmounted. Chat status needs genuine
owned dispatcher authority, not a fabricated false value. Empty bounded SSE live
framing remains unverified. No default CLI wiring, frontend build/browser claim,
staging or publication.

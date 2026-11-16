# Dynamic task session-token read composition

Adds only GET /api/exploration/tokens?task=<explicit positive ID> to the optional
dynamic TaskReadBuilder/TaskRuntimeReadRouter composition. All three options must
be supplied: token_usage_workspace, token_usage_authorize and
token_usage_request_authorize. Workspace is explicit metadata, not inferred from
read-policy revision. Missing options return503 for the route; partial options
reject construction. Generic task/activity/asset grants do not enable accounting.

Reuses existing TokenUsageStore.exploration and TokenUsageService. Source matches
Go TokenTotal/TokenStatsByWorker/TokenStatsBySession: all persisted activity rows
with kind=result contribute to total and per-worker aggregates; session grouping
uses main:<segment>, plan and same-exploration intent:<id>. Non-result rows do not
count; retries remain counted. This is NOT the llm_usage/by-model ledger, a billing
estimate, live provider-call count or archive reconciliation. Empty verified data
may produce zero; errors/missing scope/grants do not. No recording is enabled.

Exact task/exploration scope is created per request and checked against selected
identity before and after callbacks. No active-task fallback, inherited task
accounting, global daily/conversation registration, credential/profile reads,
provider execution, mutations or target-testing authority. Existing store rejects
archived/unavailable parent scope. Builder bounds cap row count and response size;
excess fails instead of truncating totals silently. Guards must retain the
existing token service/store cooperative reentrant lifetime contract.

Dynamic route opts into the accepted bounded disclosure transport. Receipt
completes current/guard callbacks, then checks raw independent request/source
authority, followed by selector and JWT-only transport fences. No current callback
follows final authority checks. Postheader failure yields no metadata body after
already emitted headers; no second status or injected error body. Standalone
non-receipt token service routes keep their existing contract. This finite order
is not a fixed point for mutually mutating grant callbacks, instantaneous network
revocation or atomic snapshots across accounting SQL statements.

Eight new synthetic composition tests pass: selected-task navigation with actual
source-shaped totals, omitted/partial configuration, JWT and independent denials,
selector validation, post-SQL revocation, postheader source/request/selector/JWT
expiry, calibrated final-current revocation, and policy-tuple rebinding. Existing
14 token tests, 29 intent-assets tests and 16 read/router tests pass. Independent
review and real PostgreSQL/frontend workflow validation are recorded below. No whole UI,
React/browser/Next.js or full-suite acceptance claim.

The other seven previously unmounted workflow read families remain separate:
scope, by-model tokens, constraints, intercept rules, chat status, task LLM
resolution and task intercept history. Bounded-empty SSE live framing remains
unverified. No stubs or fabricated success values were introduced.
No staging/publication.


## Final scoped evidence: QA189
Independent review confirmed Go worker/session/total query semantics and cleared
the finite disclosure order. Additional final lifecycle-guard-exit probes revoked
source, request, selector or JWT and withheld the body. Receipts did not repeat
accounting queries. This review does not expand supported selectors or imply
arbitrary mutually mutating policy fixed-point safety.

Report: `/workspace/shared/artex-qa/frontend-token-usage-189/REPORT.md`.
Manifest SHA256:
`323bd2af3b9ec8d45612ca30121b8453df78121993e951ddcf2a795e6b2b47de`.
All frozen manifest entries were reverified and the three candidate source hashes
matched. Frozen index plus explicit overlays is not exact staged-tree evidence.

Actual disposable PostgreSQL17 and RuntimeListener HTTP completed two genuine
HTTP-created tasks through original ticks/tools/settlement, with 16 synthetic
provider invocations. Synthetic result activity inserted into those task contexts
produced PG-confirmed token totals input=11, output=7, cache_read=3, cache_write=2
and the expected worker. Foreign-task result and same-task non-result rows were
excluded. Task2 remained independently queryable. Four integer fields preserved;
no billing value or by-model substitution. The synthetic unknown worker yielded
empty sessions: populated session grouping was NOT exercised in this real-PG
run. Likewise real-PG omitted-component/archive cases were not newly exercised;
existing unit evidence must not be mislabeled as such.

All 14 deterministic token receipt probes withheld bodies after200 headers:
five immediate header revocations; four at the last postheader scope SQL query
(6th); five at the final external current callback (12th), including JWT expiry.
Original listener adapter and actual start_response callbacks were used, not a
claimed network-scheduling race. Existing intent-assets projection and its 14
receipt probes also passed. All 94 focused unit tests passed, not the full suite.

Overall frontend workflow intentionally exits1: scope, tokensByModel,
constraints, interceptRules, chatStatus, taskLLMResolution and interceptTask
still return404 in this assembly. Query-token SSE's bounded empty tail leaves
live framing/delivery/reconnect unverified. TokenStats now returns200; this scoped
success does not make the entire workflow pass. Clean listener/pool shutdown was
verified. No browser/React/Next.js or default-registration/production-readiness
claim. Private loopback namespace, temporary transcripts, synthetic data/providers
and no real credentials or external targets. Earlier188 evidence preserved.
No code changes, staging or publication in this evidence update.

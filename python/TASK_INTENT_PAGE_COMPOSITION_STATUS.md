# Frontend intent-page composition correction

Actual `web/src/lib/api.ts` intentsPage emits limit/task/before/page; sessions-tab
uses this to populate worker sessions. PG+HTTP baseline176 reproduced400 in the
explicit dynamic task read assembly even though ExplorationService supports that
contract. TaskRuntimeReads previously required exactly one task query parameter.

The correction allows only task/limit/before/page for intents, rejects duplicate
or foreign/missing task selectors, retains bounded query size/count, and delegates
numeric validation and legacy-array/page-envelope shape to ExplorationService.
Other read selectors and authorization remain unchanged.

Generic ExplorationService paging also merges inherited tasks. This capability
has no independent source grants: an observed task relation refuses503. A local
store adapter suppresses all inherited expansion even if a relation appears after
that check. No new source metadata or graph reads are authorized. Supporting
related-task paging requires a separate scoped authority design. This is not a
claim of an atomic snapshot across independent SQL reads.

Four new regressions cover exact frontend page request versus legacy list,
foreign/duplicate/unknown/malformed/bounds/auth denial related-task refusal, and populated cursor/limit/has_more semantics.
The synthetic SQL fixture fails if source metadata is read. All 16 task-runtime
read/router tests pass. Independent review cleared source `fff6be9738c005d567e34c1e292b804aed572649fb533903ba70a46f91bc63ce`.
Additional review probes covered query permutations, encoded duplicates, malformed
bounds, final-grant revocation during page fetch (403 without items), and the
local-only inherited-source boundary. Corrected QA180 confirms the pagination
request returns 200 with a populated page envelope; it does not pass the whole
frontend contract. Baseline176 failures for other routes/Stats remain separate findings;
no default empty success values or lifecycle behavior were changed.

No staging/publication. The deferred synthetic handoff manifest files are unrelated
and must not be included with this fix.


## Genuine workflow QA180 evidence
Evidence: `/workspace/shared/artex-qa/frontend-task-workflow-180/results/workflow.json`.
Frozen manifest SHA256:
`3dea2587fa757dd0ccd4ea79a7cf469a687ee233afb54b8c877c4ced38fcd36d`.
Actual isolated PostgreSQL + loopback HTTP and explicit original Python runtime
created two tasks through HTTP. Each followed four ticks with status
running/running/running/done. Synthetic provider/tool flow generated a non-origin
confirmed worker fact, met goal and done intent; original host settlement completed
the task. There were 16 synthetic provider calls total. No SQL-forced completion
or lifecycle code changes. Repeated terminal polling/task navigation, 52 HTTP
checks and clean listener shutdown were recorded. Pagination is200 with real
populated items. Tests did not execute React, Next.js or browser rendering.

The run deliberately exits 1 with 11 remaining contract violations, not a workflow
PASS: Stats200 omits required TypeScript roe_enabled/findings_confirmed; scope,
by-model tokens, constraints, intercept rules, session token stats, chat status,
task LLM resolution, task intercept approvals and intent-assets each return404
in this explicit assembly. These are unresolved DTO/composition findings, not
proof those capabilities are absent from every standalone service.

Frontend-style query-token SSE returned 200 and text/event-stream but an empty
bounded tail. The strict frame assertion therefore failed. With max_polls=1 and
no new event after the history snapshot, this is an unresolved test-coverage/
contract ambiguity, not established proof of a production stream defect. Do not
hide it by marking SSE replay/framing accepted. History/stream unauthenticated
checks and source/request/selector/current denials are included; sequential HTTP
replay does not prove Promise.all concurrency, reconnect or gap-free live tails.

Earlier176 terminal-seeded baseline and177-179 lifecycle fixture iterations are
retained separately.179 established genuine two-task completion before the latest
pagination/SSE/session additions. Frozen overlay acceptance is not exact staged
whole-tree acceptance. Other frontend failures remain open; this patch only fixes
local paginated intent composition.

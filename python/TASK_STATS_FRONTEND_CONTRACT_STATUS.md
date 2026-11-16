# Stats frontend source-contract correction

Workflow182 reported missing required TypeScript roe_enabled/findings_confirmed.
Inspection of Go server.stats (server/server.go:951 onward) shows neither field
is emitted. Neither occurs in any Go source. Outside the mock stats producer,
web/src contains only the stale required interface declarations, no UI consumer.
Python StatsService matches the actual Go response here. Therefore no backend
ROE switch or findings-count semantics were invented.

Removed the two unused required fields from web/src/lib/types.ts and removed
matching mock stats fields so the typed mock does not manufacture that obsolete
contract. Retained the mock function's existing input signature for compatibility.
No SQL, backend response, authentication, lifecycle or authorization changes.

Three Node source-contract regressions pass: required actual Go fields remain in
Stats, optional active_task remains, obsolete fields have no frontend consumers
or mock producers, and Python does not fabricate them. All 13 Python stats tests
pass. Source-contract tests are not TypeScript compilation or browser execution;
web/node_modules is unavailable, so no full Next.js build is claimed.

Independent review cleared the exact type/mock correction and independently
reran all three Node and 13 Python stats tests. Recursive frontend source scan
confirmed no remaining consumers. Prior
workflow182 failure remains accurate for its frozen old frontend type. Nine
missing assembly routes and bounded-empty SSE framing remain unresolved. This
correction does not declare the full frontend workflow passing.
No staging/publication.


## QA183 corrected source-contract evidence
Report: `/workspace/shared/artex-qa/frontend-task-workflow-183/REPORT.md`.
Manifest SHA256:
`1c1af7b9e270748574f551351e9ca17506f464d41aa139df493421674fa19370`.
Frozen types.ts SHA256:
`6031ffca581ecd944797f149b1b0bb8263567e15d18fdb4a920751fb1b5d413a`.
All manifest entries verified and source hashes matched the shared checkout.

Actual Stats200 passes the corrected required-field types, engine_mode enum and
selected active_task identity. The real response includes idle mode, false
llm_configured, zero in-flight counters and one total/met goal for the completed
fixture. No backend DTO rewriting or unsupported ROE/findings defaults were used.

Two tasks were created via authenticated HTTP and genuinely completed through
original goals/planner/worker confirmed fact/prove_goal/host settlement. Each
followed four explicit ticks with running/running/running/done status; 16 synthetic
provider invocations total. The run performed 52 frontend-shaped read, polling and
navigation checks, including correct selected-task identity and populated paged
intents. Listener/pool shutdown was clean. Three Node source-contract tests and
13 Python stats tests pass.

Overall harness exit remains 1: nine omitted assembly routes return404 (scope,
by-model tokens, constraints, intercept rules, session token stats, chat status,
task LLM resolution, task intercept approvals and intent assets). Query-token SSE
authenticates200 with text/event-stream and unauthenticated access rejects401,
but its max_polls=1/max_batches=1 empty tail fails the nonempty-frame assertion.
No malformed frame was demonstrated; live framing/delivery/reconnect remains
unverified. These ten findings remain unresolved; this is not full workflow PASS.

Evidence uses disposable PostgreSQL17, actual Python runtime/listener/pool and
loopback HTTP with temporary transcripts and synthetic providers in isolated
UID1000 namespaces. The explicit TEST runtime snapshot grounds idle/in-flight0
in completed tasks and empty handle inventory. No external providers/targets or
real credentials. Frozen index plus assigned overlays is not exact staged-tree
or full-suite acceptance. Earlier176–182 evidence is preserved. No React/browser
execution; full frontend build remains blocked by unavailable dependencies.
No code changes, staging or publication in this evidence update.

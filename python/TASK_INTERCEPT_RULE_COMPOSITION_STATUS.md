# Optional task intercept-rule metadata composition

TaskReadBuilder requires all three explicit options: intercept_rule_workspace,
intercept_rule_authorize(scope) and intercept_rule_request_authorize(request,scope).
They enable GET /api/tasks/{id}/intercept-rules (ordinary HEAD semantics) through
the authenticated dynamic overlay. Missing capability returns503; partial config
rejects. No generic task grants or mutation authority are inferred.

The read uses unchanged InterceptRuleStore and existing _dto serialization.
Persisted task_intercept_rules are selected by exact task ID and ordered by raw
action then ID. Disabled rules and both allow/block actions remain visible;
builtin is false. No global asset rules are merged. No /intercept/rules tool
approval policy, approval history, tool input, create/update/delete/toggle route,
matcher synchronization or enforcement changes are introduced. Metadata visibility
is not evidence of execution permission or active policy enforcement.

The selected task/exploration and archive/deletion state are checked by existing
store/current paths. Archive refusal is narrower than general Go task lookup.
The store owns its transaction and guard; the new service does not introduce an
extra surrounding nested guard. Configured500-row and1MiB pattern+note bounds
refuse overflow, never top-N success; serialized response has an independent1MiB
bound. This is not a database execution-cost limit.

Initial independent grants precede observations. Receipt-aware delivery runs
current/guard callbacks before raw request/source grants and final selected-task
selector/JWT with pure binding checks. Postheader failures release no body, though
200 headers may have been sent. No arbitrary mutating-policy fixed point, atomic
cross-system snapshot or network-race guarantee. The host must supply cooperative
lifecycle coordination; persisted rows are an observed snapshot, not reread on
transmission. Unique rule_* closure locals avoid cross-family policy capture.

Ten new tests cover optional policies, exact route/task navigation, stored disabled
allow rule projection, bounds/empty output, before/afterSQL and postheader/final
current revocation, policy pins and opposite coverage/constraint/rule callback
families.11 constraint-composition and30 existing intercept-rule tests also pass.
Independent review cleared144 focused tests and additional final postheader
lifecycle-guard-exit source/request/selector/JWT revocations, each withholding the
body with only one rule query. Denied source/request readers made no task-rule
query or parent-lock query. Unique family captures and opposite-policy tests were
verified. Separately, the current-checkout full suite passed3940 tests; this is not
an exact staged-tree acceptance claim.

Scoped PostgreSQL + HTTP QA199 PASS:
`/workspace/shared/artex-qa/frontend-rules-199/REPORT.md`
Manifest `3071355f2b8fba63289ca4aa315dacaa8114de71e56e1520edc80b5287702a60`.
Frozen index plus explicit candidate overlays; all manifest entries and three
candidate source hashes verified. QA198's fixture error remains preserved.

Disposable PostgreSQL17/private loopback HTTP verified two genuinely completed
tasks through the original runtime,16 inert provider calls and clean shutdown.
Synthetic own-task disabled allow and enabled block rules retained action/id order;
foreign-task and separately seeded global asset rules were excluded. Existing DTO
has no task_id field. Real HTTP refused503 above500 rows and for490 individually
valid notes totaling over1MiB, then recovered200 after cleanup. These are result
bounds, not database execution-cost guarantees.

All60 real HTTP cross-family combinations passed: source/request independently,
rule allowed/other denied, reverse and both allowed, against coverage, constraints,
scope, model usage, token usage, intent assets, goals, main sessions, findings and
activity. This does not establish every possible interaction or legacy-family
postheader protection.

All14 deterministic actual-header probes withheld bodies:5 immediate revocations,
4 final scope-SQL revocations (third observation),5 final external-current
revocations (sixth observation, including JWT). These exact listener-adapter
checks are not real-network race or arbitrary callback fixed-point proofs.
142 focused tests passed, not the full suite. Prior cumulative probes also passed.

The52-check frontend workflow still exits1: interceptRules now200, but three
unrelated404 routes and bounded empty SSE framing remain. No default wiring,
frontend build/browser execution, production readiness, staging or publication.

Three frontend families remain: chat status, task LLM resolution and task intercept
history. Bounded empty SSE framing is still unverified.

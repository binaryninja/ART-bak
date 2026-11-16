# Optional task constraint metadata composition

## Corrected cross-family integration evidence

Exact-tree validation at4b4a73a3 failed and exposed an authorization mix-up:
TaskReadBuilder constraint locals overwrote coverage source/request closure cells.
Coverage could invoke permissive constraint policies instead of denied coverage
policies, or fail when constraints were absent. QA195 remains historical/nonfinal;
its scoped checks did not establish combined-family correctness.

The corrected builder uses distinct constraint_source/constraint_request locals
(SHA256349f117a365f8bd8b2ff7c46d94e6d16ecca9c36f3aac0611713eef630f4fbfb).
Cross-family regressions exercise actual coverage/constraint handlers with absent,
opposing and permitted grants, checking responses and which policy family ran.
Independent review instantiated all optional families with25 uniquely tagged
policies and confirmed every callback invoked only its intended original policy.
The corrected current-checkout full suite passed3930 tests; this is separate from
exact-tree or frozen QA acceptance.

Fresh scoped combined PostgreSQL/HTTP QA196 PASS:
`/workspace/shared/artex-qa/frontend-cross-grants-196/REPORT.md`
Manifest `0452effabdac628df7b1a2379adc39d8ea49d762be74327dc5d9727a2efce60c`.
Six actual HTTP combinations tested source and request independently: denied
coverage/allowed constraints returned403/200; the reverse200/403; both allowed
200/200. Original constraint DTO/isolation checks, all four bounds and14 actual-
header disclosure probes passed again.149 focused tests passed.

Fresh absent-capability QA197 PASS on identical product source:
`/workspace/shared/artex-qa/frontend-cross-grants-197/REPORT.md`
Manifest `9c0adce52cf691425a7df67127f41c37ae1fd605d58ab79bfac450f7a7068385`.
With constraint opt-in omitted, constraints consistently returned503 while
coverage independently returned200 or403 for its own source/request policies.
All six combinations passed. Constraint-specific DTO/receipt probes were skipped
in197 because capability was absent;149 unit tests ran in196, not again in197.

Both runs used fresh disposable PostgreSQL17/private loopback HTTP and genuine
two-task original runtime completion with16 inert provider calls and clean stop.
Manifests were verified; snapshots are frozen index plus explicit overlays, not
exact staged-tree acceptance. Neither run proves legacy coverage postheader
fencing. Constraint probes remain deterministic callback tests, not arbitrary
policy fixed points or network-race proof. Both frontend workflows intentionally
exit1 with four unrelated404s and bounded empty SSE framing still unverified.

Explicit paired TaskReadBuilder policies constraint_authorize(scope) and
constraint_request_authorize(request,scope) enable authenticated
GET /api/tasks/{id}/constraints (and ordinary HEAD semantics). Omission returns503;
partial configuration rejects. No generic task/activity read grant is reused.
No POST/PATCH/DELETE routes or mutation approvals are introduced. Viewing a deny
constraint does not confer execution rights or prove runtime enforcement.

The new TaskConstraintRead uses unchanged ConstraintStore.list_bounded against
the selected exploration. Source semantics are exploration-only, not inherited
constraints: allow before deny, then ID. DTO uses string id/kind/text, optional
origin and second-resolution timestamp, matching existing TaskConstraintService.
It reads persisted metadata, not planner memory, injected prompts or settings.
Parent task/exploration identity is checked under SQL lock and live/current
checks. Archive/deletion refusal is deliberately stricter than Go's general
lookup; no fabricated empty archived result or ambient active-task fallback.

SQL pre-LIMIT windows and sentinels cap500 rows by default,16384 text bytes,
128 origin bytes and1MiB aggregate text/origin; serialized output is independently
bounded by1MiB. Overflow refuses rather than returning a truncated success.
These are result bounds, not database execution-time or cost limits. Existing
unbounded trusted callers and mutation store paths remain unchanged.

Receipt-aware delivery gates initial observations with independent grants, then
runs current/lifecycle-guard callbacks before final raw request/source grants,
selected-task selector/JWT and pure bindings. Actual-header failure withholds the
body even when200 headers are already sent. No arbitrary mutually mutating grant
fixed point, atomic cross-system snapshot or instantaneous network revocation is
promised. Host lifecycle coordination remains required; this is an observed
metadata snapshot, not a fresh reread of every constraint at transmission.

Ten new tests cover DTO/task navigation, timestamp/origin, bounds/empty results,
optional and independent grants, malformed selectors, SQL/header/final-current
revocation and policy rebinding.26 task-constraint tests,11 scope composition and
16 dynamic read/router tests pass. Independent review confirmed Go exploration-only
semantics, projection/order and bounded-store SQL. Review corrections removed a
redundant consecutive authority call and made the policy-pin regression target
_constraint_policies rather than an unrelated optional capability.

Scoped PostgreSQL + HTTP QA195 PASS:
`/workspace/shared/artex-qa/frontend-constraints-195/REPORT.md`
Manifest SHA256:
`3f53e99471ea05cfa741c40ad8723034908040c1d5b1bfc70d86031f500a2c79`.
Frozen index plus explicit candidate overlays, not exact staged-tree acceptance;
all manifest entries and three source hashes were verified. QA194 is preserved
as superseded/nonfinal evidence preceding the duplicate-call/test-pin cleanup.

Disposable PostgreSQL17 and private loopback RuntimeListener HTTP verified
own-exploration DTOs, allow-before-deny ordering, string IDs, optional origin and
second-precision timestamp; other-task exclusion and task/exploration mismatch404.
Two genuine tasks completed through original runtime settlement with16 inert
provider calls, then clean shutdown. Only synthetic constraint data was used.

Actual HTTP independently refused503 for501 rows against configured500,16385
text bytes against16384,129 origin bytes against128, and aggregate data above1MiB
using490 individually valid2200-byte texts. Cleanup restored200. No truncation
or fabricated success; these tests do not establish database-cost containment.

All14 deterministic actual-header listener probes withheld bodies:5 immediate
revocations,4 final scope-SQL revocations (third observation), and5 final external
current revocations (sixth observation, including JWT). These are not real-network
race proofs or arbitrary callback fixed points. Prior asset/token/model/scope
checks also passed.148 focused tests passed, not the full suite.

The52-check frontend workflow still exits1: constraints now200, but four unrelated
404 routes plus bounded empty SSE framing remain. No full frontend build/browser
execution, default installation, production readiness, constraint mutation or
runtime enforcement acceptance claim.

Four unrelated frontend families remain: intercept rules, chat status, task LLM
resolution and task intercept history. Bounded empty SSE framing is unverified.
No default CLI registration, staging or publication.

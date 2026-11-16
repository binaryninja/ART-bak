# Dynamic intent-assets composition and frontend gap inventory

All nine404s in workflow183 have existing HTTP implementations. They are missing
from that explicit runtime assembly, not nine absent Python implementations:

| Frontend request | Existing implementation | Required dependency/authority |
|---|---|---|
| task scope | server/task_scope.py + db/task_scope.py | Exact task binding, independent metadata grant/current/lifecycle guard; inherited scope unsupported |
| by-model tokens | server/llm_records.py + db/llm_records.py | Explicit task usage/record authorization; llm_usage metering, not activity-derived substitutes |
| constraints | server/task_constraints.py | Exact task/exploration binding, read grant/current/guard; mutation additionally needs idle and independent approval |
| intercept rules | server/intercept_rules.py + db/intercept_rules.py | Scoped task/global rule grants; not task execution or interception readiness |
| session tokens | server/token_usage.py + db/token_usage.py | Exact task scope, activity-result accounting; distinct from by-model usage ledger |
| chat status | server/task_chat.py | Actual task chat dispatcher with running ownership; cannot invent false from lack of a dispatcher |
| LLM resolution | server/task_profiles.py | Explicit current profile/role/task-chain/environment resolution; static availability is not provider readiness |
| task intercept approvals | server/intercept_task_read.py + intercept_read_setup.py | Scoped durable history/cursor grants and explicit opt-in read composition |
| intent assets | server/task_intent_assets.py + db/task_intent_assets.py | Scoped source-context and raw anchor-metadata request grants |

## Implemented bounded composition
TaskReadBuilder now accepts the optional pair intent_assets_authorize and
intent_assets_request_authorize. Partial configuration rejects. Source callback
receives selected task scope plus each actual context task/exploration; request
callback receives request and selected scope. These are independent of generic
read, findings, coverage and execution grants.

TaskRuntimeReadRouter routes GET /api/tasks/{id}/intent-assets after JWT and exact
task resolution. Missing optional pair returns503, never fabricated empty assets.
Two tasks and newly resolved tasks use separate per-request services. Existing
TaskCoverageStore and TaskIntentAssetsStore/Service retain bounded SQL/projection,
local intents of all states, individually authorized direct-source terminal
intents, provenance and archive/refusal behavior. No source recursion/global
fallback, mutations, target discovery or testing authority. No read means an asset
is authorized for active testing.

Only this route is added to dynamic composition. Other eight routes remain
unmounted in the workflow harness until their actual dependencies are composed.
No default application setup or permission is added.

## Validation and boundaries
Seven new synthetic composition tests cover populated selected-task navigation,
omitted/partial options, JWT-before-lookup, selector/source/request denials,
inherited-source denial before asset SQL, fetch-time revocation without metadata,
invalid selector/query/mutation method, and policy-tuple replacement rejection.
All24 intent-assets-family tests and16 task-runtime read/router tests pass.
Independent review and genuine PostgreSQL/HTTP workflow validation requested.

Independent review and actual-PG probe185 reproduced a blocking header-disclosure
seam: pre-return validation was insufficient because the listener deferred actual
headers. That candidate is not accepted. The corrected candidate opts into a new
bounded DisclosureResponse/DisclosureBody receipt. WSGI retains the original
serialized representation and checks receipt on iteration. RuntimeListener's
existing authenticated wrapper additionally checks the exact guarded body after
actual start_response and before yielding its captured chunk. Other routes keep
their existing behavior; no broad receipt retrofit is claimed.

Dynamic intent-assets receipt rechecks original JWT/selector/current, original
source/service receipt and final JWT-only validation. Postheader refusal aborts
the body without sending a second status/error body after200 headers. Direct WSGI
raises a generic disclosure failure; listener terminates its iterable. HEAD emits
no metadata body. Finite ordering is not a fixed-point guarantee for arbitrary
mutually mutating callbacks or an atomic database snapshot.

Three additional regressions cover direct WSGI and the listener's actual-header
wrapper: source/request/selector revocation and existing JWT clock advancing past
expiry all withhold metadata. All27 intent-assets-family,16 task-read/router,
30 listener and10 WSGI tests pass. Independent re-review and corrected PG/HTTP
validation pending. The original185 reproduction remains preserved as nonfinal.
No staging/publication; deferred manifest prototype is unrelated.

## Final-current ordering correction (pending reacceptance)
Review also reproduced a last postheader current/SQL observer revoking source or
request permission after its last grant check. The intermediate186 receipt fix
therefore remains nonfinal even though simple header revocations were denied.
The revised source receipt completes all current/guard observers first, then
performs pure binding checks, an authority-only request check, authority-only
checks for every captured source context, and pure checks. Service performs no
current callbacks after that receipt; transport follows with JWT-only validation.
A calibrated regression counts postheader current SQL observations and revokes
source/request at the final one; both now withhold the entire body. All28
intent-assets-family,16 read/router,30 listener and10 WSGI tests pass.
This finite request→source→JWT order assumes grant observers do not arbitrarily
mutate other previously checked authorities. It is not a fixed point over hostile
mutually mutating callbacks or a snapshot across independently changing services.
Corrected independent review and PG/HTTP acceptance remain pending.

A subsequent independent probe found the final external current callback could
revoke selected-task selector permission. The final transport phase now also
reauthorizes that selected-task selector without calling current again, then
checks original JWT. A calibrated last-current regression confirms no body after
selector revocation. Final finite order is current/guard observers, request/source
authority sweep, selector authority, JWT, then pure binding checks and delivery.
All29 intent-assets-family tests pass; re-review/reacceptance pending. This does
not claim a fixed point for mutually mutating authority-only grant callbacks.


## Final scoped acceptance: PG/HTTP188
Independent re-review cleared the final selected-selector fence. Calibrated
postheader current observers revoking source, request, selector or JWT all
withheld the body. No remaining blocker was found within the finite observer
contract described above; earlier pending statements refer to nonfinal snapshots.

Report: `/workspace/shared/artex-qa/frontend-intent-assets-188/REPORT.md`.
Manifest SHA256:
`4916c613c53e11a8ea10ea1e0272c8384ec87cc8a2e827deb41743b97dcbed35`.
Final router SHA256:
`065aa9db2403525f777e4f3c0ce2b43b79ed07a45b81057b12bbfb2b2a5e9dc8`.
All manifest entries verified and listed source hashes matched the checkout.
Frozen index plus explicit overlays is not exact staged-tree/full-suite evidence.

Scoped acceptance passes actual PostgreSQL/HTTP projection and denials, 14
postheader/final-observer probes and 98 focused tests. Two genuine HTTP-created
tasks completed through original provider/tool/host settlement with 16 synthetic
provider invocations. TEST-NET assets anchored to actual intents produced local
and individually authorized direct-source terminal-intent metadata, correct IDs
and provenance; inherited open intents were excluded. Source denial withheld
inherited metadata. JWT, independent grants, unknown/malformed selectors and
wrong-exploration observations rejected. Post-SQL grant revocation withheld
metadata; unrelated goals reads remained available under their separate grants.

All 14 deterministic receipt probes withheld bodies after200 headers: five
immediate header revocations; four revocations at the calibrated last scope SQL
observation (11th); five at the final external-current callback (22nd), including
JWT clock expiry. These exercise the original listener adapter's actual
start_response callback, not a live-network timing race. Finite ordering does
not guarantee a fixed point for mutually mutating authority-only grants, atomic
DB snapshots or instantaneous network revocation. Unrelated non-receipt routes
do not acquire these protections merely because the transport supports receipts.

The overall frontend harness still exits1. Across 52 recorded frontend checks,
intentAssets now returns200 populated metadata, but eight routes remain404 in
this assembly: scope, tokensByModel, constraints, interceptRules, tokenStats,
chatStatus, taskLLMResolution and interceptTask. Query-token SSE authenticates200
but its bounded empty tail leaves live framing/delivery/reconnect unproven. These
nine remaining findings are preserved, not converted into workflow success.

185's positive disclosure leak and intermediate186/187 snapshots remain retained;
188 is the final scoped acceptance. Isolation used disposable PostgreSQL17,
original runtime/pool/listener, loopback-only HTTP, temporary transcripts and
synthetic providers/TEST-NET assets in UID1000 namespaces. No real credentials,
external targets or host workspace; database stopped and shutdown was clean.
No React/Next.js/browser/default-wiring or production-readiness claim. Metadata
read grants are never target-testing permission. No staging/publication.

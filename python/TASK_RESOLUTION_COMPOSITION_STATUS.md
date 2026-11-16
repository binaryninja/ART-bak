# Optional static task LLM resolution composition

TaskReadBuilder requires six explicit callbacks together: resolution_authorize,
resolution_request_authorize, resolution_profile_authorize,
resolution_environment_authorize, resolution_configuration_valid, and
resolution_environment. Request/profile/environment grants receive the original
request and resolved scope (profile additionally receives its ID); source receives
scope, validator receives private profile, environment resolver returns explicit
EnvironmentSelection or confirmed-absent None. No ambient os.environ discovery,
generic read grant reuse, implicit environment approval or provider construction.

GET /api/tasks/{id}/llm/resolution reuses unchanged TaskProfileService and actual
TaskStore/ProfileStore/AgentStore. Existing precedence is preserved: valid role
binding; explicit task chain (no global/environment fallback when invalid or
exhausted); valid global profile; explicitly supplied environment. available means
host-validated static configuration, never connectivity, quota, runtime-selected
provider or permission to call a model. No switch/write/default selection changes.
DTO whitelists role/source/name/format/model/id/available/reason and never emits
API keys, URLs or proxy configuration. Hosts must not put secrets into public
names/model labels. Validator is trusted private-config code, not an HTTP probe.

Every consulted profile ID is retained for final reauthorization, including invalid
profiles that caused fallback. Environment consultation requires a separate grant
before its resolver and again finally. Current and lifecycle-guard callbacks run
before final source/request/profile/environment sweep, then selector/JWT and pure
binding checks. Actual-header refusal withholds bodies, possibly after200 headers.
This finite cooperative observer order is not a mutually mutating-policy fixed
point, atomic SQL/config snapshot or network-revocation guarantee. Supplied guard
must coordinate profile/role/config edits; no reread of all config at transmission.

Live numeric task/exploration/current checks deliberately refuse unavailable or
archived scopes. Response serialization is capped by configured max_bytes (default
1MiB). Existing TaskStore hydration/profile/agent reads are reused without new
SQL transfer or execution-cost caps; response bounds do not claim otherwise.
No readiness, provider call, runtime chain replacement or secret disclosure occurs.
Unique resolution_* locals preserve independent family callback cells.

Twelve new tests cover exact tasks, private-key/URL exclusion, optional/partial
configuration, source/request/profile denials, exhausted-chain nofallback, explicit
environment and final envgrant, postSQL/header/final-current revocation and
cross-family callback separation.19 existing profile tests and11 constraint
composition tests pass. Independent review cleared156 focused tests. Its additional
invalid role-profile8 to task-profile7 fallback probe confirmed both consulted IDs
are tracked, including the invalid role profile; revoking that profile withheld
disclosure. Final postheader guard-exit source/request/profile/environment/selector/
JWT revocations withheld bodies, and environment denial prevented resolver calls.
The current-checkout full suite separately passed3963 tests; not exact staged-tree
acceptance.

Scoped PostgreSQL + HTTP QA201 PASS:
`/workspace/shared/artex-qa/frontend-resolution-201/REPORT.md`
Manifest `d8c2fa84ef624d9da3973d6c97cd6578c6d1259e972301fc1ed3d12eaf6b4bfe`.
Frozen index plus explicit overlays; all manifest entries and three source hashes
verified. Disposable PostgreSQL17/private loopback HTTP exercised two genuine
original-runtime task completions,16 inert provider calls and clean shutdown.
Only synthetic profiles/keys/URLs and explicitly supplied inert environment
metadata were used; the fixture validator deliberately trusts synthetic configs.

Actual PostgreSQL/HTTP verified role bindings, global fallback, task-chain priority,
exhausted-chain refusal to fall back, explicit environment and confirmed absent
environment. Three role DTOs excluded dummy key and URL. Missing-profile/invalid-
configuration cases were not independently added to this PostgreSQL run; unit and
independent-review evidence remain distinct. Static available is not connectivity,
quota, active runtime provider selection or execution permission.

All60 actual HTTP opposing source/request combinations passed across ten optional
families: coverage, constraints, scope, model, token, intent assets, goals, main
sessions, rules and history. All20 deterministic actual-header probes withheld
bodies:7 immediate revocations,6 final scope-SQL revocations (third observation),
7 final external-current revocations (sixth observation). Both profile and
environment paths were consulted. These do not prove arbitrary mutating-policy
fixed points or network races. Previous cumulative checks also passed.

154 focused tests passed (135 composition/transport plus19 profiles), not the full
suite. The1MiB response cap was not driven to overflow in201; unchanged hydration,
profile and agent reads have no newly established SQL/row bounds. Response caps
must not be mistaken for database cost/transfer containment.

The52-check frontend workflow still intentionally exits1: taskLLMResolution now
returns200, but chatStatus404 and bounded empty SSE framing remain. No browser/
React execution, default wiring or production-readiness claim.

Chat status remains unmounted pending genuine owned dispatcher authority. Empty
bounded SSE live framing remains unverified. No default CLI wiring, frontend
build/browser execution, staging or publication.

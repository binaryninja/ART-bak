# Explicit missing-skill statistics HTTP composition

New `MissingSkillReadService` handles GET `/api/skills/missing` with the accepted
archive-aware reader. `bind_missing_skill_reads` explicitly overlays that route
on an exact existing WSGIApplication/Router backed by the original AuthService
JWT authority. The original application's routes are not mutated. No automatic
installation or default app change occurs.

The request uses one exact MissingStatsSelection through reader/source/outer
permissions. The source limit behavior is retained: absent, malformed,
nonpositive or >200 values fall back to 20. Duplicate/unknown query parameters,
non-origin targets, fragments, invalid UTF-8 query data and bodies fail closed.
HEAD does not widen GET access: it returns 400 with no body or SQL. Other methods
return 405. Existing GET/HEAD routes matching the fixed path, including parameter
patterns, prevent binding atomically.

JWT authorization runs before SQL. Request, source-global, source-current and
outer-global permissions remain independent. Original application, methods,
router declarations, auth resources, reader/executor/scope and selection identities
are pinned. Successful response bytes are rechecked after serialization, before
start_response, and again after start_response before handing the body back. A
post-header denial aborts by raising rather than releasing the prepared body;
headers already emitted cannot be withdrawn. A transport must not interpret that
exception as a successful body delivery.

Finite fencing follows the existing cooperative-authority model. The final source
current callback is followed by original request and outer-global checks; final
authority observers must remain nonmutating and share ownership/lifecycle rules.
This is not atomic protection from arbitrary mutually mutating callbacks, hostile
monkeypatching, or uncoordinated concurrent revocation. It does not infer scope
from JWT possession, enable any mutation, or authorize skill loading.

Validation: 16 new service/overlay tests pass; all 92 skill-related tests pass.
Coverage includes default/clamped limits, query/body refusal, independent grants,
original-app inertness, JWT-before-SQL, same selection identity, parameter route
conflicts, auth/reader replacement, postfetch and serialization revocation,
post-header revocation of all relevant grants/current state, and last-source-
current revocation of outer/request permissions. Tests use real JWT/WSGI with
injected inert database fixtures, not a listening server.

Standalone reader PostgreSQL stage149 remains separate evidence. Subsequent
**stage151 HTTP-composition acceptance** passed four actual PostgreSQL groups:
1. Original app 404, JWT denial before SQL, explicit hot/cold top-N success,
   correct dimensions, same selection identity, limit/query/method behavior and
   health delegation.
2. Independent pre-SQL grant denial, postfetch/guard-exit/serialization revocation
   and original auth replacement, all withholding sensitive response data.
3. After 200 headers, outer/request/source/current revocation, genuine JWT expiry
   through a stable clock closure and auth replacement raised before body return.
4. Final source-current revocation of outer, request, source-global or JWT clock
   authority refused the body in all four cases, without pretending already-sent
   headers could be replaced.

The exact immutable snapshot passed 31 missing-skill focused tests. No fixture or
source corrections occurred during execution; both source hashes matched the
checkout afterward. Acceptance manifest SHA256:
`443f5bc5b2ac16729e27f262551ab1b2460ce5e020bb1d03d3a2e9ea496dd609`.
Service SHA256:
`e0734e8f2f3f77b2243c9b1d80ff4df2f4860fd6c506a9c6993cc26ffca25d5f`.
Setup SHA256:
`9d8af1c1f68a7cb3e76cac19ca3dfc26c6a8c35a07a49c085f6bdcf93ed365ca`.

Stage151 used fresh original-schema PostgreSQL 17, synthetic hot/cold metadata,
genuine AuthService JWT verification, and credentialless user/filesystem/network
isolation with only loopback, no external route or host workspace/home. It did
not change default routes, execute skills, contact providers/targets or perform
frontend/browser acceptance. The cooperative callback constraint remains; this
is not an arbitrary callback fixed-point or exhaustive concurrency guarantee.
No route inventory regeneration, publication, listener, frontend parity or
whole-application readiness is claimed by this slice.

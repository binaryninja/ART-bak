# Optional own-task scope metadata composition

TaskReadBuilder now accepts the explicit paired scope_authorize(scope) and
scope_request_authorize(request,scope) policies. Only authenticated
GET /api/tasks/{id}/scope is added to the dynamic read overlay; HEAD follows
existing router semantics. No query selectors, ambient active-task fallback,
write routes, insertion approval, company approval or testing authority are
inferred. Omitted capability returns503; partial configuration rejects.

The new TaskScopeRead uses unchanged TaskScopeStore.list_bounded for persisted
own-task metadata and company labels. Task ID provenance is retained. It does
not normalize or manufacture scope records. Go includes direct source-task
scope, but this bounded branch deliberately refuses any task relation with503
rather than silently presenting an incomplete inherited view. Parent identity,
exploration, deletion and archive state are checked; unavailable archived scopes
are not treated as empty successful results.

Existing SQL bounds are1000 rows,1MiB aggregate JSON and16KiB per item with
sentinel refusal. Dynamic builder limits further constrain rows (default500)
and serialized response bytes (default1MiB). These constrain result transfer and
output, not arbitrary database execution cost. No truncated-success fallback.

Accepted receipt-aware transport validates after actual headers. Initial raw request/source grants gate observation; a second grant check follows
current/guard exits before any relation inspection. All current
callbacks and lifecycle-guard exits precede a final relation-existence query,
then raw independent request/source grants, selected-task selector/JWT and pure
binding checks. A relation inserted before this final query refuses disclosure.
No body is returned after a postheader refusal; status headers may already be200.
This is a finite cooperative observer order, not an arbitrary mutually mutating
grant fixed point or atomic network/database revocation. Hosts must serialize
scope/relationship writers with the supplied lifecycle contract; metadata is a
bounded observed snapshot, not a promise to reread every row before transmission.

Eleven new composition tests cover task navigation/projection, independent and
omitted grants, malformed selectors, SQL/header/final-current revocation, policy
rebinding, denied readers making no relation queries, initial/postheader relation insertion, empty results and row/item
bounds. All46 scope tests,8 by-model composition tests and16 dynamic read/router
tests pass.

Independent review cleared the corrected reader (3d93e070):97 focused tests
passed. Denied source/request readers received uniform403 with no relation SQL;
additional final guard-exit and relation-query revocations or relation additions
withheld all response bodies. No remaining blocker under the documented finite
cooperative observation model.

Scoped PostgreSQL + HTTP QA193 PASS:
`/workspace/shared/artex-qa/frontend-scope-193/REPORT.md`
Manifest SHA256:
`702192bd66648a9e60bebb8a8c19d6e27e20e6ab5fcd1bae6c3f052965abeffc`.
Frozen index plus explicit overlays, not exact staged-tree acceptance; manifest
entries and three candidate source hashes were verified. QA192 remains preserved
and nonfinal because it preceded the relation-presence disclosure fix.

Disposable PostgreSQL17/private loopback HTTP verified persisted own-task keyword
metadata, task isolation, malformed/missing-auth rejection and independent grants.
Two genuine tasks completed through the original runtime with16 inert provider
calls. All four denied source/request × related/unrelated combinations returned
identical403 bodies with zero relation SELECTs. Authorized related scopes refused503.

Actual HTTP bounds exercised the configured500-row limit,16KiB individual-item
limit and1MiB aggregate limit (490 individually valid items below the row cap).
Removing fixture rows restored200. The underlying1000-row threshold was not
independently reachable in this500-row configuration; no separate integration
claim is made for it. Result bounds are not database execution-cost limits.

All20 deterministic actual-header transport probes withheld bodies:5 immediate
revocations,4 final scope-SQL revocations (third observation),5 final external
current revocations (sixth observation),2 real relation insertions at headers or
final current, and4 final relation-query source/request/selector/JWT revocations.
These exact listener-adapter probes are not real-network scheduling race proofs.
117 focused plus31 separate scope unit tests passed (148total), not the full suite.

The52-check frontend workflow still exits1: scope now returns200, but five
unrelated404 routes and bounded empty SSE framing remain. Clean shutdown passed.
No full frontend build, browser/React execution, default installation or
production-readiness claim.

Five unrelated frontend families remain: constraints, intercept rules, chat
status, task LLM resolution and task intercept history. Bounded empty SSE live
framing remains unverified. No default CLI registration, staging or publication.

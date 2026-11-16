# Explicit owned archive catalog listener integration

RuntimeListener accepts optional archive_catalog=ArchiveCatalogService with the
exact RuntimeResourceOwner pool. It constructs the accepted metadata overlay over
the original bootstrap AuthService, not a new JWT authority or replacement runtime.
Only archive list/single-detail GET/HEAD requests select this view. All other paths
and methods use the original runtime/optional skill composition. No default mount,
archive action, resource acquisition or provider/pump start occurs at construction.

The adapter pins original service method, executor, policy tuple, overlay/router,
authenticated transport and fallback. Listener checks validate before delegation
and after real headers through the existing _AuthenticatedView/DisclosureBody
transport. Original archive resource/request/JWT receipt checks remain active.
Existing skill transport observes actual headers unchanged. No arbitrary callback
fixed-point, atomic database/network revocation, or package verification claim.

Shutdown is not a new archive lifecycle: existing bounded HTTP workers must drain
before runtime resources and exact pool close. A blocked cooperative archive read
therefore retains resources via the existing unresolved HTTP shutdown result.
Synchronous SQL/policy callbacks are not preempted. This is a development loopback
listener with explicitly supplied existing runtime ownership, not production launch
or namespace exclusivity. Archive metadata does not authorize restoration/deletion.

Ten new unit tests exercise inert construction, exact pool/pre-bind method refusal,
original fallback, header grant/executor mutation, forged fallback refusal, existing
skill composer and its header revocation, and original clean shutdown plus unresolved HTTP drain retention. The combined
archive/runtime/skill focused set passes 56 tests. Independent review cleared adapter 346069c1 / listener 4b00b14b after correcting
mutable fallback app/check and underlying WSGI/router method bypasses. Its 50 focused
tests and independent method swaps, foreign receiver and router replacement probes
refused before forged invocation. Pins establish the tested original object/method
contract, not containment of arbitrary host code.

QA234: `/workspace/shared/artex-qa/archive-listener-234/REPORT.md`.
Manifest SHA256:
`98a65c6c4a24e75dbe0c13350515394c40ff57ed5e823e154e2cf5f71f53cd3e`.
23 actual PostgreSQL/filesystem/RuntimeListener HTTP/transport cases and 50 focused
listener/catalog tests pass. Actual loopback list/detail, five skill families,
runtime task/health/read fallback, JWT denial, HEAD and no restore route were
exercised. Exact foreign pool refuses binding; independent archive grants do not
authorize or disable skill usage. Three header source/request/JWT revocations
withhold bodies without metadata SQL requery. Direct and nested fallback app/check,
WSGI _handle and router.dispatch mutations refuse before forged calls; pre-bind
runtime _handle replacement refuses construction.

A real archive request blocked at its actual PostgreSQL SELECT boundary remains an
active HTTP worker: stop(.15) reports http_unresolved while retaining pool/resources
and not invoking cancellation/resource callbacks. Releasing the SELECT lets the
worker finish; another stop completes ordered shutdown and pool closure, with an
idempotent repeat. No providers or implicit pump ran; archive row digest stayed
unchanged. This is actual loopback drain evidence, beyond the unit mocked-quiesce
regression. QA232 and QA233 remain preserved nonfinal earlier-source evidence.

The QA snapshot is index plus explicit overlays, not exact staged-tree acceptance.
Header and direct-fallback probes use deterministic exact transport callbacks,
not socket scheduling races; nested mutation/drain probes use real loopback HTTP.
Synthetic keys, metadata and temporary skill files only, no archive packages or
actions. Finite scheduling coverage is not comprehensive race-freedom or production
ownership/launch proof. Held dashboard/router changes are untouched; no staging or
publication.

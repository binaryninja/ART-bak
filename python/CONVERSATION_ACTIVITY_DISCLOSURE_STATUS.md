# Conversation history read disclosure

Reproduced against the existing conversation runtime: activity.page revoked the
conversation grant but GET still returned200 with private user text. Independent
review also reproduced original Router revocation during SQL and actual headers.
This slice changes existing GET list/detail only; accepted POST/stop logic is
preserved.

Registration now carries the original Router/JWT checker already used by POST.
The separate history helper pins request/header values, source/service dependencies and concrete store executor identities,
conversation getter, activity methods and dispatcher.running. Exact selected
conversation ID/agent/profile is reobserved before final authority checks. Activity
list/page/detail executes once; running is observed once. Serialization is followed
by reauthorization. A DisclosureResponse retains the checks through existing WSGI
actual-header delivery; errors also reauthorize before returning sanitized status.
Conversation metadata may be queried again, not the activity data.

Final cooperative order is metadata observation, conversation grant, original
Router/JWT, conversation grant, callback-free request/dependency and original Router/JWT resource pins. The shared
POST authority closure also uses those final transport pins; a final source callback
cannot replace the transport checker before a stop action. This is not
an atomic row/configuration snapshot or revocation synchronized with the network.
A history result remains a snapshot of one data read, not a guarantee that contents
or running status stayed unchanged. No fabricated idle state or dispatcher creation.

Legacy query behavior is preserved: since takes precedence over before if both
are supplied, negative/default limits retain store conventions, and detail returns
the existing detail envelope. Existing storage caps list/page row counts at10000,
not text bytes. Detail SQL and per-row text transfer are not byte-bounded; this
authority fix does not claim bounded SQL transfer or bounded memory for those data.
No new mutation capability, default mounting or runtime ownership is introduced.

Eight new tests cover list/page/detail shapes and legacy queries, data-callback
source/Router revocation, running and final metadata callbacks, actual header
selection/request/header/method changes, error reauthorization and no activity
requery.56 related history/conversation tests pass. Independent review cleared helper
2a6875ab / service f9de39e7 with 84 combined tests and independent final-source
Router/executor/header mutation probes, plus JWT-clock and POST-stop regressions.

Fresh QA242: `/workspace/shared/artex-qa/conversation-history-242-corrected-fixture/REPORT.md`.
Manifest SHA256:
`f281d4d45b88839a5d65cab55651fb07d6f7dcbaec8b36298a0b2f7114e4f499`.
48 actual PostgreSQL/HTTP/WSGI cases and 56 focused tests pass. A fresh disposable
PG17/schema/pool with real conversation stores, two synthetic conversations and
signed JWTs exercised separation, legacy queries, HEAD, post-SQL denial, metadata
selection changes, running callback and header/final callback revocation. Four
successful reads used real isolated loopback HTTP. Deterministic header cases use
the exact WSGI/_AuthenticatedView callback, not network-scheduled race claims.

Eighteen final-source regressions cover page/list/detail crossed with replacement
of Router.authorize, AuthService.clock, metadata executor, activity executor,
original target or original headers. Every case sends200 headers then zero body
bytes, with two post-header source callbacks and exactly one activity SELECT.
Other body receipt probes also execute one activity SELECT; metadata may requery.
No private marker was released after the tested revocations. Already-sent headers
cannot be revoked. No provider, external target or transcript operation was needed.

QA241 is nonfinal due to the original missing final transport pins. The first QA242
fixture is separately retained as a harness failure: it mutated the source callback's
defensive request copy and incorrectly expected withholding. The corrected fixture
mutates the captured original request. Earlier QA143-240 evidence directories were
lost on workspace reset; descriptions in other status documents are historical,
not assertions that those artifacts remain available.

This fresh read-only acceptance does not establish send/provider behavior. Existing
row-count caps remain the only SQL sizing bound: detail and text transfer remain
unbounded in bytes. Finite cooperative observations do not establish arbitrary host
callback containment, transaction/network atomicity or production readiness. Held
dashboard and speculative prototype untouched; no staging/publication.

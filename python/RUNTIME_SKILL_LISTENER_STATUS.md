# Skill-read composition on the owned runtime listener

`RuntimeListener` now optionally accepts `skill_reads=SkillReadBundle`. This
connects the existing five-family read composition to the actual owned task
runtime transport, without replacing its custom task/read router or weakening
those routers' exact-base contracts. No new endpoints or default assembly are
introduced; absent attachment keeps existing listener behavior.

The skill bundle must be built against the exact runtime bootstrap JWT WSGI app.
Usage, missing-statistics and optional catalog-usage readers must all use the
listener owner's exact ExecutorPool. Filesystem roots, grants and cooperative
writer/lifecycle guards remain explicit host inputs, not inferred permissions.
Service-to-view relations, composer methods, adapter methods, original base and
resource identities are pinned. Invalid binding fails before socket creation,
provider execution, policy evaluation or pump start. These are ownership checks,
not an arbitrary-compromised-process security boundary.

Enabled skill paths dispatch to the original skill composer, retaining every
family's pre/post-header JWT/grant/snapshot checks. Its real `start_response` runs
inside that composer, unlike the runtime streaming wrapper's deferred header
capture: revocation or tree change in the actual response callback cannot bypass
final skill disclosure validation. Failure after headers terminates without the
prepared body; before headers the transport returns a generic unavailable error.
Unknown and disabled optional skill paths, task/control/read routes and health
remain with the existing runtime application and authenticated streaming wrapper.
No instruction execution or automatic task tick is enabled by reading skills.

Existing listener shutdown ownership remains unchanged: stop admissions, quiesce
HTTP connections/workers, stop pump, cancel/drain tasks and tracked manual work,
then close host resources and the shared pool. Skill SQL/filesystem work is inside
those HTTP workers. A blocked worker prevents confirmed quiescence/resource close;
Python cannot preempt blocking SQL or callbacks. No independent helper closes a
second pool or claims a clean shutdown while ownership is unresolved. Calls made
directly to the private adapter outside the listener are not tracked HTTP workers.

Eleven new tests and all 30 listener-focused tests pass. They cover inert
construction and five routes, no SQL/filesystem before JWT, foreign base/pool
rejection, preserved custom task-read overlay, actual header callback grant/tree
revocation, composer/adapter implementation replacement, selected-view mutation,
and ordered resource shutdown. Unit fixtures use real runtime composition and
WSGI/JWT with inert SQL; they mock socket boundaries. Independent review identified a fallback delegation path that preceded adapter
identity checks. The corrected adapter calls its pinned original checker before
all delegation and invokes the pinned original runtime fallback. Forged fallback
and checker replacements return sanitized 503 without invoking the forged handler.
Independent re-review cleared the fix, including checker/app and pattern/fallback
substitutions, post-header fallback changes and actual blocked-read drain/retry.

Corrected PG+FS+HTTP167 passed four isolated integration groups, all 249 focused
skill tests and all 30 listener tests. Actual loopback HTTP covered all five skill
families with PostgreSQL hot/cold/legacy usage and filesystem data, retained
original task/health/unknown and scoped TaskRuntimeReads behavior, JWT expiry/
denial, Host/Origin guards and independent source permissions. Mutated runtime
fallback returned 503 on unauthenticated `/api/tasks`, never invoked the forged
handler, and restored behavior returned 401 after restoring the pinned fallback.

A real HTTP catalog request blocked inside a cooperative grant. Stop(.15) closed
its socket but returned `http_unresolved` while the server worker remained active;
the exact shared pool stayed open and cancellation/resource-close callbacks had
not run. Releasing the grant let the worker unwind, but resources remained retained
until an explicit second stop closed them in order. Repeated stop was idempotent
and restart refused. This verifies active HTTP-read drain, not task/model shutdown.
Post-header content/grant/JWT mutations were separately tested by direct calls to
the actual adapter, not represented as live network races.

Report: `/workspace/shared/artex-qa/skill-runtime-listener-167/REPORT.md`.
Manifest SHA256: `5c28e6382505334bf39d9375e0defd4b2c79db413588ac1af24e302d8300efc2`.
Listener SHA256: `8206a7aa0b2cd07f3ca153e5683be0d5790c86a40ff35352c9a7a41ac474e055`.
All manifest entries and runtime/test hashes matched after testing. This was a
copied index plus explicit listener/test overlays, not exact staged-tree evidence.
Original TaskRuntimeAssembly/RuntimeListener/ExecutorPool, PostgreSQL 17 and
HTTPConnection sockets on loopback ran in isolated UID1000 namespaces with no
external route, host workspace/home or real credentials. Fixtures/JWT were
synthetic, sources read-only, and no provider was constructed or called.

PG+FS+HTTP165 is retained as nonfinal because later review found the fallback
bypass. PG+FS+HTTP166 used corrected source but its fixture wrongly expected the
client HTTP thread to stay alive after socket shutdown. PG+FS+HTTP167 changed only
that assertion to inspect the blocked server worker; 166 is preserved and also
passed 249 skill/30 listener tests. Earlier 163/164 evidence remains preserved.
Acceptance did not run the full repository suite or SQL parsing, nor prove browser
behavior, active-task/model cancellation or default application wiring.

This is the existing local development listener, not a production TLS/proxy/
exposure design or a frontend/browser acceptance claim. Existing request origin,
framing, connection bounds, explicit pump start and unresolved-drain semantics
remain. Cooperative source/writer constraints and finite observer ordering still
apply. No schema installation, credentials discovery, default configuration,
new external traffic, staging or publication is performed by this slice.

# Read-only archive overview list/detail

Actual task archive UI (tasks/page.tsx and api.taskArchives/taskArchive) has no
Python metadata endpoint. ArchiveCatalogService plus the separate opt-in
build_archive_catalog_application overlay now provides GET /api/task-archives
and GET /api/task-archives/{id}, using explicit catalog-wide workspace metadata
and request grants. No task selector implies this global permission. Original
Router authorization is pinned/repeated, requiring its original AuthService method
and unchanged store/codec/key/clock objects. Unrelated routes remain on the original
router. Conflicting read routes, including generic parameter patterns overlapping
list/detail, refuse construction before mutation. No default mount.

The reader follows source ListTaskArchives search/state/pagination/order semantics
with a single statement for coherent total and selected page. Search matches task
ID/name/description with source ILIKE behavior. page<1 normalizes1; size outside
1..100 normalizes20; maximum page100000 and512-byte query are deliberate bounds.
Out-of-range pages keep correct total, and missing detail is404 after authorization.
No archived package, filesystem path, task runtime or restoration is consulted.

This is a deliberately narrower, explicit safe overview projection, not wholesale
Archive.to_dict. SQL selects neither archive_path, error nor warnings. No fabricated
empty diagnostic arrays/messages are substituted: those optional fields are absent.
Phase is restricted to known source stage labels; unknown stages refuse503, not a
made-up phase. aggregate_stats includes only tokens/skills/tools/findings counter
maps; unknown raw diagnostic JSON is not projected. Counter maps require integer
values and bounded labels without path separators. Returned task names/descriptions/
goals/category/counter labels remain authorized user metadata; arbitrary user text
is not guaranteed secret-free. The metadata grant must cover those fields.

Stored state/progress/format/timestamps are observations only. ready does not prove
that a valid package exists, nor confer restore/delete authority. No archive queue,
claim, retry, restore, deletion or lifecycle state transition is exposed. Existing
archive mutation code is untouched. No ownership/recovery/launch inference.

SQL bounds each projected row64KiB and aggregate page1MiB before transfer. The
reader validates exact field shapes, IDs, phase/state, timestamps, counters and
coherent total/row counts; response bounded2MiB. Overflow/malformed data refuses,
not truncated success. Server scan/materialization work is not bounded by transfer
limits. Source count/query/filter semantics remain metadata-only.

A retained DisclosureResponse rechecks original request/header/resource pins,
original Router/JWT authority and independent metadata grants after actual headers
using existing WSGI adapters. Errors/missing results also reauthorize. Final order
is transport observation, raw source/request grants, pure pins; arbitrary mutating
policy fixed points or atomic network revocation are not promised. No independent
host-runtime-current callback or fake runtime lifecycle authority is invented.

Eleven new tests cover source-compatible DTOs, safe projection, pagination/empty
totals, invalid bounds/counters, independent denials, malformed filters, actual
header grant/JWT/binding changes, error reauthorization and generic route conflict/resource replacement refusal.
Independent review cleared reader f70bb36c and service 8cc73d3a after fixing generic
route overlap, original JWT resource replacement, and deeper-route interception.
The reviewer ran 33 tests and independent deeper generic GET/HEAD/POST preservation
and invalid-detail probes. Deeper unrelated routes now delegate to the original
handler rather than being intercepted by an archive namespace prefix.

QA231 scoped acceptance: `/workspace/shared/artex-qa/archive-catalog-231/REPORT.md`.
Manifest SHA256:
`487f09fc55ebd99afc29c0ee7cb68d6e3525ea3cac72480c05f59bdc35319ae4`.
46 actual PostgreSQL/HTTP/exact-transport/binding cases and 11 focused tests pass.
Signed-JWT loopback list/detail requests verify projected counters and omission of
synthetic archive paths, errors, warnings and internal aggregate strings. Real
loopback deeper-generic dispatch returns the original handler with zero archive SQL.
Filters, pagination/empty-page totals, missing detail, denied grants before SQL,
invalid queries and unavailable mutation routes were exercised. Ten header/final
JWT-clock revocations or request mutations and five AuthService resource/method
replacements withhold the body after headers. Three generic overlap configurations
refuse binding without changing original routes. Nine post-SQL success/missing/error
revocations return 403. Actual text >16KiB, row >64KiB and page >1MiB refuse 503;
the rendered 2MiB response bound was not separately stress-tested.

QA228 retains a fixture failure involving a nonexistent guard callback seam.
QA229 and QA230 remain nonfinal earlier-source evidence, not final acceptance.
QA231 uses an index snapshot plus explicit candidate overlays, not exact staged-tree
acceptance. Deterministic header probes use the exact WSGI/_AuthenticatedView seam,
not socket scheduling races. Synthetic metadata/JWT only: no archive package was
created, read, restored or deleted. Partial projection parity, finite callback
ordering and lack of package/ownership authority remain as described above.
Dashboard candidate remains untouched and held; no staging or publication.

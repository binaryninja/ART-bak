# Dynamic selected-task Markdown report composition

TaskReadBuilder now accepts an optional all-or-none triple:
report_authorize(scope), report_request_authorize(request,scope), and
report_global_assets_authorize(scope,asset_types). All must return literal True.
No general findings, coverage or JWT grant substitutes for any of these policies.
The opt-in TaskRuntimeReadRouter exposes GET /api/report?task=ID, preserving exact
canonical numeric selection, original JWT, selected task/exploration/revision and
live/unarchived scope. Absent capability returns503. No default CLI registration.

The adapter reuses unchanged TaskReportStore, TaskReportService and Markdown
renderer. Local exploration graph finding payloads are not shared/global findings.
Asset totals are global and independently authorized for six source asset types,
not local task coverage. Existing source caps remain: latest1000 findings,
100000 per-kind asset count; task text16KiB each, finding64KiB each/1MiBaggregate,
rendered body2MiB. The builder generic row/byte options do not replace these
report-specific source bounds. Overflow/unavailable values fail; no fabricated empty report.
The capped source counts are not uncapped database totals. Separate statements
are not a cross-table snapshot of unrelated concurrent writers.

The original issued PreparedReport receipt is retained inside DisclosureResponse.
After callback-bearing selection, source checks and guard exits, an authority-only
request/source/global sweep surrounds final original selector/JWT validation;
a last raw data-grant refresh follows JWT observation, then only pure request/
scope pins. This preserves the fixed-report clock-observer-to-grant revocation
contract. It does not solve mutually mutating grant callbacks that expire JWT
after its final observation. The
receipt performs no repeated report-data SQL or rendering. Real current checks
may issue identity SQL. Actual-header revocation withholds body through existing
WSGI/RuntimeListener transport; no replacement status after headers is promised.
Finite cooperative callback ordering is not an arbitrary policy fixed point or
atomic network revocation. Markdown is text, not trusted HTML: rendering clients
must sanitize it. No model/evidence URL fetch or report publishing occurs.

Nine composition tests cover two-task selection and original renderer, separate
grants/absent capability, actual-header revocation/JWT expiry, malformed selectors,
policy tuple/coverage-family isolation, final-current request/header mutations, final-JWT-clock data-grant revocation,
grant revocation and unavailable
metadata bounds. 37 composition/report/builder/router tests pass. Independent
review and scoped PostgreSQL/HTTP QA224 passed. No launch/namespace/recovery
claim, staging or publication.

## Independent review and QA224

Independent read-only review cleared the corrected adapter with 65 combined tests
and five independently recalibrated final-clock/current probes. Final target/header
pins and late JWT-clock source/request/global-grant revocation regressions passed.

Report: /workspace/shared/artex-qa/frontend-report-224/REPORT.md
Manifest SHA256:
5229a3cc3d918323b6f52f30ba335fc2ab1bfbe14cb54c529d9367e8daa10549

QA224 passed 35 actual isolated PostgreSQL/HTTP/exact-transport cases and 35 focused
report tests. Three signed-JWT loopback requests selected ALPHA/BETA/ALPHA, exposed
only each selected task's own finding and included separately authorized global
IP count3. Independent source/request/global-assets/selector denials returned403
before report data SQL; malformed selectors400 and omitted capability503.

All17 disclosure probes withheld the body after200 headers. These include header
and final-current grant/selector/JWT revocation, final-current target/header mutation,
and final JWT-clock revocation of all three report grants. The fixture calibrated
last current14 and last JWT clock3. Each request used three report data SELECTs;
disclosure did not repeat those data queries. Three postSQL grant losses denied.
Actual PostgreSQL metadata over16KiB, finding over64KiB and aggregate over1MiB
refused503. The latest1000-finding cap, global100000-per-type count cap and rendered
2MiB bound were NOT stress-tested. Cross-family closure checks are unit evidence,
not a combined-family PostgreSQL acceptance claim.

Verified source SHA256:
- adapter: 0dfa9b9d7bc83b8231350d5e1731b7de945fcd6448cf0ae3960c95fb19c59047
- builder: bcc84233f5b9b0e94b348c3d4f884b18cf03e28797eee872ab52c53521f4e49b
- router: d14280c4137827e5909ba817683ae0710183df56a26b8444a61738a81a7f1b60

QA222/223 are preserved nonfinal evidence. QA224 used frozen index plus explicit
overlays, not exact staged-tree acceptance; manifest entries remained unchanged.
Header probes use exact WSGI/_AuthenticatedView callbacks, not socket-scheduled
races. JWT uses a synthetic key/controlled clock. No real credentials, provider,
production assembly or default mounting. Multi-statement data is not an atomic
snapshot. Global counts never inherit permission from task access. Final finite
ordering and Markdown HTML-sanitization limits above remain mandatory; no arbitrary
callback fixed-point or comprehensive race-freedom claim follows. No publication.

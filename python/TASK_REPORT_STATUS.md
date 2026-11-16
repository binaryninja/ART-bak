# Explicit task graph report

Implemented standalone `db/task_report.py` and `server/task_report.py`, following
`server/server.go:getReport` and the existing pure `report.markdown` renderer.
No application factory or default route registration is added.

- Explicit `GET /api/report?task=ID` must match the supplied immutable local task /
  exploration / host read-revision scope. Missing or foreign selectors fail;
  implicit active-task resolution is not implemented.
- Report findings are the latest 1000 local exploration nodes of kind `finding`,
  not graph facts and not the shared/global findings table. Existing source
  renderer wording is not independent verification of finding truth.
- Asset totals are global, separately authorized, for the six source types. Each
  count preserves the source cap of 100000; this is not task-scoped coverage.
- Task/exploration/archive identity is checked under the original lifetime guard
  and task row lock. Independent task-report, global-count and HTTP policies are
  required. Construction performs no reads or callbacks.
- SQL bounds transferred task text (16 KiB each), selected finding payloads
  (64 KiB each, 1 MiB aggregate), rows and scalar count results. Rendered Markdown
  is bounded to 2 MiB. Oversized/unavailable data fails rather than silently
  producing a partial or empty report. Separate statements do not promise a
  single snapshot against unrelated asset writers.
- Original callback/resource/scope and request selector identities are retained.
  Grants are refreshed after SQL, transaction/guard exit and rendering; request
  authority is refreshed after callback-bearing store checks, followed by pure
  binding/selector checks. This is not an arbitrary-callback fixed-point claim.
- Output is `text/markdown; charset=utf-8`. No model, URL/evidence fetch, report
  publishing, HTML rendering, execution permission or database write occurs.
  Consumers must sanitize any later conversion of Markdown to HTML.

Nine owner tests and five independent tests pass, including unchanged late-store
callback revocation of HTTP authority, aggregate overflow, global count type /
uniqueness bounds, selectors and no global finding/fact substitution. Independent
review cleared this standalone scope. Actual PostgreSQL acceptance is pending
separately; unit tests alone do not establish it. Existing finding/evidence ZIP
export is a different feature documented in `REPORT_EXPORT_STATUS.md`.

## Actual PostgreSQL acceptance

Stage 128 passes three original-schema fixture groups and 14 focused tests.
See ISOLATED_QA.md for the immutable manifest, tested boundaries and limitations.
No default route or external action is enabled.

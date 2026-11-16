# Archive-aware skill statistics prerequisite

`artex.db.skill_stats` ports the pure merge semantics of
`db/task_archive_aggregate_stats.go:mergeArchivedSkillStats` and `mergeAgentKeys`.
It accepts already-decoded, explicitly supplied hot rows and archive projections.
It performs no SQL, archive loading, filesystem access, skill execution or routing.

Preserved behavior:
- Live grouped rows initialize by skill; later duplicate live rows replace earlier
  ones, matching the source map assignment (normal source SQL is already grouped).
- Cold calls/tasks are added, agent keys unioned in first-seen order, and the most
  recent aware timestamp retained. Counts do not globally deduplicate task IDs.
- Successful archive stats fall back to the legacy skill/count map only when the
  modern successful-stat array is empty; each legacy entry contributes one task.
- Missing-skill stats never fall back to successful legacy counts. Model-supplied
  missing names remain inert strings, not validated/executed filesystem paths.
- Output is ordered by descending calls then ascending skill name.

Intentional bounds: exact plain containers/records, nonnegative signed-64-bit
counts with overflow refusal, aware timestamps, bounded total input row count and
UTF-8 text bytes. Duplicate input rows consume the bounds. Archived containers
also have a count bound. Malformed data refuses rather than fabricating zeros.

Twelve focused unit tests pass, covering merge/order/latest, legacy/missing behavior,
input preservation, source duplicate behavior, bounds, invalid counts, overflow,
container rejection and timezone-aware comparison, including ambiguous DST folds
compared as UTC instants while preserving the selected datetime. Every direct
timestamp input must be UTC-representable, otherwise ValueError is raised even
when no comparison would have been needed. No real database acceptance
is claimed: this primitive does not query a database.

Not yet implemented by this slice: archive decoding and size/snapshot validation,
authorized global aggregate reads, live SQL aggregation, archive selection,
HTTP DTO serialization, or GET /api/skills/missing. Existing skill-usage readers
are unchanged. No new registered/live route or full skill parity is claimed.

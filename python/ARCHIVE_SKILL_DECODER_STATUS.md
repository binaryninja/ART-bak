# Bounded archive skill-summary decoder

`artex.db.archive_skill_stats.decode_archive_skill_stats` is a pure decoding
prerequisite for the archive-aware skill merge. Source structure:
`db/task_archive_aggregate_stats.go:taskArchiveAggregate` and
`db/skill_usage.go:SkillStat`.

The caller supplies UTF-8 JSON bytes/text. The decoder limits bytes and nesting
before JSON decoding, then limits total structural nodes and skill rows across
legacy/successful/missing dimensions. Unknown dimensions are ignored only after
structural validation. It refuses duplicate keys, nonfinite JSON numbers,
invalid UTF-8/surrogates, invalid count types/ranges, malformed arrays and invalid
timestamps. No skills or content are executed.

Missing/null maps/arrays and a null root use Go-like zero values. Optional count
fields omitted from stat objects default to zero; explicit null counts, empty
names and malformed values are stricter refusals. Legacy maps remain available
for the existing merge's fallback rule. Model-generated missing-skill names are
inert text, not filesystem paths.

Timestamps require RFC3339 shape, an explicit timezone and at most six fractional
digits, and must be representable as UTC instants within Python's datetime range.
Out-of-range instants uniformly raise ValueError. Higher precision is deliberately refused rather than silently truncated;
this is narrower than Go's nanosecond time support. Selected timestamp offsets
are retained, and the merge compares actual UTC instants.

Validation: 12 new decoder unit tests, and 61 skill-related tests pass together.
Cases include legacy and modern projections, ignored dimensions, duplicate keys,
counts, shape, timestamps, offset preservation, bytes/depth/nodes/rows limits,
escaped quote handling, malformed UTF-8 and numeric overflow.

Not a persisted snapshot or authorization boundary: there is no database query,
archive selection, filesystem loading, archive integrity check or HTTP endpoint.
No real PostgreSQL acceptance or new route count is claimed. Future readers must
bound SQL output before transfer and pin independent global read/disclosure
permissions and original snapshot/lifecycle authority.

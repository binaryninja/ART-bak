# Judge usage: standalone read adapter, not registered

`db/judge_usage_read.py` and `server/judge_usage_read.py` implement the DTO/read
contract of source `server/intercept.go:interceptJudgeUsage` and
`db/llm_usage.go:JudgeUsageStats`. No model invocation, settings mutation, raw
payload access, schema creation or route registration occurs.

Exact worker='judge' ledger rows contribute to all-time calls/input/output/cache
read/cache write token totals, regardless of status. Only the daily series uses
`ts >= now() - days * interval '1 day'`. UTC date grouping and ascending order
match source; there is no upper timestamp filter, timezone rewriting of the source
interval semantics, zero-filled days or inferred cost/currency. Daily fields are
date/calls/input_tokens/output_tokens only. No archive merging or proof of complete
external judge activity is claimed. Missing schema/storage errors are failures,
not fabricated zero usage; a genuinely empty ledger returns zero totals/empty daily.

A single statement returns totals plus daily rows from one snapshot (stronger
consistency than source's separate queries). days defaults to30 for missing,
invalid, overflow or nonpositive integers. Positive days above366 are rejected;
unknown/duplicate queries and request bodies are rejected. These are explicit
stricter bounds. A 368-row sentinel detects more than367 distinct daily rows,
including anomalous future-dated ledger rows, rather than silently truncating.
Returned integers must fit source's 64-bit integer shape; counts cannot be negative.
Token sums preserve source signed values, without inventing corrections.

The injected global read grant sees the original request and normalized days.
Original executor/reader/current/guard/policy identities are pinned and checked
before/after SQL, serialization and guard exit. Host authentication must eventually
be mounted independently; JWT or task read access is not global metering authority.
No arbitrary mutually mutating callback fixed-point guarantee is made.

Six inert tests pass: query semantics and empty storage, all-time versus daily
DTOs, query defaults/bounds, invalid result/overflow refusal, pre/post-SQL grants,
serialization/guard-exit revocation and callback replacement. Independent review
and isolated actual PostgreSQL acceptance remain pending. No live database or
provider was used by this implementation task.

## Verified checkpoint

Eight owner/independent tests and three exact-source PostgreSQL stage98 groups
pass, including original schema/UTC grouping, exact integer aggregates, bounds
and disclosure revocation. Frozen combined suite:2835 tests457 SQL syntax checks.
See ISOLATED_QA.md for manifest and limitations. No HTTP route registered.

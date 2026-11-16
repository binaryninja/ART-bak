# Standalone archive-aware missing-skill aggregate reader

`artex.db.missing_skill_stats.MissingSkillStatsReader` implements the read/projection
core of `db/skill_usage.go:MissingSkillStats`, using the source archive selection
from `db/task_archives_restore.go:ArchivedAggregateStats`.

One SQL statement reads grouped `NOT found` hot ledger rows and cold summaries
joined to tasks with a nonnull archived_at. It combines the complete bounded
inputs before sorting/limiting, so a cold count can change the top-N result.
Live missing task counts remain zero; archive-provided dimensions are preserved.
Successful legacy skill counts never become missing-skill counts. Duplicate hot
skill rows refuse rather than silently replace data. Timestamps use RFC3339-style
output with insignificant fractional zeros removed.

Required inputs: exact explicit global UsageScope, global-read grant, current
scope callback, lifecycle guard, executor and validated 1..200 selection. Default
selection is 20; HTTP defaulting/parsing is not implemented. Original scope,
callbacks, executor, bounds and selection remain pinned through query, projection
and guard exit. An optional disclosure callback plus validate_disclosure supports
a future serializer's final check. Guard semantics are caller supplied; this is
not a production database lock or HTTP transport authorization by itself.
The final fence runs current/disclosure/current/disclosure/global-grant, with
identity checks between callbacks. No lifecycle callback runs after the final
disclosure/global authorization observations. Final authority observers must be
nonmutating and cooperate with lifecycle ownership. This finite sequence is not
atomic protection against arbitrary callbacks mutating one another's grants or
uncoordinated concurrent revocation. A future transport must enforce its own
final disclosure check under the same authority ownership protocol.

Bounds: SQL returns at most max_rows+1 summary rows, and suppresses text if the
aggregate UTF-8 byte sum exceeds the limit. The Python reader verifies the
sentinels/types/row count and total bytes, bounds decoded summary dimensions, and
limits combined missing-stat rows before merge. Oversize is refusal, not truncated
statistics. SQL aggregation/scanning may still cover the entire ledger/archive
selection; output bounds are not a database execution-cost/statement-timeout cap.
One SQL statement provides one statement snapshot, not cross-request durability.

Validation: 15 focused tests and all 76 skill-related tests pass. Tests cover empty
results, cold-before-limit ranking, source selection text, legacy exclusion,
authority before fetch, revocation during fetch and guard exit, pin/scope mutation,
final disclosure callback, transfer/decoded-row bounds, selection types, timestamp
format and duplicate hot results, and final-current callback revocation of the
global grant or disclosure grant. These tests use injected executors only.
The available interpreter lacks pglast, so the isolated SQL parse attempt could
not run in that interpreter. Subsequent **corrected stage149** acceptance ran
against fresh original-schema PostgreSQL 17 with an immutable source snapshot.
Four actual-database groups passed:
1. Hot/cold aggregation before top-N, archived_at selection, successful modern
   and legacy count exclusion, null agents and timestamp formatting.
2. SQL byte NULL sentinel, max_rows+1 refusal, nested dimension bound and
   malformed count rejection.
3. Literal-True authorization before SQL, postfetch/guard-exit revocation,
   binding/scope mutation and request disclosure revocation.
4. Last-current revocation of global/disclosure grants: both read and
   validate_disclosure refused, covering four cases.

The frozen snapshot also passed all 76 skill-related tests. The three source
hashes matched after execution. Acceptance manifest SHA256:
`fa3b1f4b6dbd5dfb3d1b9f51e36ad2daf42511e905239f82867a87e8fca46c6e`.
Reader SHA256:
`b08a651fd0c118cea7b62e9eadb6e688419cfe6b7a809729c015165852a6bb30`.
Decoder SHA256:
`df929fbfcfa98fb8041ae786cbb97b8622be4aa0eed1428ab831af6ed7055c1d`.
Merge SHA256:
`c0e2c07e7606401d25262ee7c38b5cb77d6aef6826d82c19e7b08e7449044f15`.
Earlier stage148 evidence did not cover the late-current defect and is not final
acceptance of the corrected reader.

Stage149 used credentialless user/network/filesystem isolation, loopback only,
no external route or host workspace/home access, and inert synthetic metadata.
It did not activate an endpoint/default, publish changes or contact providers or
targets. This is component acceptance, not whole-tree or frontend parity, and
does not remove the cooperative final-observer limitations above.

No endpoint, default service, listener, schema write, archive operation, skill
load, provider call or filesystem access is added. Existing aggregate/decoder
files are unchanged. No complete API parity or route-count increase is claimed.

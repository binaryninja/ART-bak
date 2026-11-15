# Independent findings, asset and Runner review

Snapshot: 2026-11-15 working tree. Read-only review of worker implementation;
reviewer owns this note and `tests/test_finding_runner_review.py` only. No live
SQL, provider, target, tools, network or upstream Go code was executed. Authored
Python tests use inert executors/providers and bounded event-driven threads.

## Finding repaired and independently verified

**Explicit malformed store task filter can broaden to global.**
`db/findings.py:FindingStore._where` originally parsed an invalid/nonpositive
`FindingFilter.task_id` into zero and omitted its SQL predicate. Examples:
`oops`, `0`, `-1`, int64 overflow, or `1 OR 1=1`. An unscoped store then counts
and lists all findings rather than rejecting the intended scope. This is NOT SQL
injection and NOT a demonstrated HTTP bypass: `server/findings.py:_filter`
already validates HTTP scope. A bound store also keeps its constructor scope.
The problem is the direct persistence interface silently widening a caller's
filter. Desired behavior: reject malformed explicit task selectors before SQL;
retain empty selector/global and `__unassigned__` as deliberate supported choices.

Three independent tests cover this regression, scoped OR-search binds, and stale
report version rejection without UPDATE. The malformed-filter regression initially
failed for all five inputs. The owner then changed validation to accept only an
absent empty string, `__unassigned__`, or a positive integer/ASCII-digit ID.
Independent rerun after that fix: all three review tests pass, including all five
malformed inputs, with no executor calls for rejected scope. This finding is fixed
and verified at the reviewed snapshot.

## Finding write and HTTP boundary evidence

- Report mutation takes advisory evidence lock 7337741004, then task row lock /
  archive-state check, then locks the finding row and compares evidence version.
  Version conflict rolls back without report UPDATE in the inert regression.
  A versionless report remains stale when evidence version is nonzero.
- Constructor task/exploration scope is included in get/list/update/report
  selection. OR text search is parenthesized under those predicates and values
  remain bound; literal percent/underscore in search are escaped.
- Finding creation is transaction-wrapped, validates finding-node ownership and
  refuses deleted/archiving tasks. It is a low-level insert, not RecordFinding's
  graph creation, evidence preparation or notification path.
- Metadata mirrors run in the same transaction; PATCH fields are prevalidated but
  multiple fields remain sequential durable mutations, not one atomic batch.
- HTTP status changes, deletion, retention/export, live provenance and retests
  explicitly fail unavailable. Low-level set_status has no notification effects;
  it must not be advertised as the production status workflow.
- Unexpected exceptions are redacted at HTTP. Reports/evidence are intentionally
  returned to authenticated callers, not a redacted public endpoint. No new
  credential-bearing outbound transport is implemented by these modules.

## Runner lifecycle review

- The worker owns synchronous BoundedAgent execution plus the result sink.
  `wait()` joins the actual thread; a cancellation flag alone cannot mean drain.
- Completed threads do not call TaskManager.complete themselves. Explicit host
  settlement after admission forwards the exact token and consults manager
  generation/cancellation guards. Sink failures become failed, never done.
- Same-token duplicate start does not launch twice; a live/uncancelled prior
  generation blocks replacement. Pending generation capacity is bounded and
  rejected settlement remains pending for reconciliation.
- Cancellation still needs cooperative providers/tools. A hung callback may
  prevent drain indefinitely; no forced thread termination or hidden worker
  shutdown guarantee exists. Trusted callbacks must not spawn unowned work.
- Settlement's documented host-ordering requirement is real, not enforced through
  a manager-ready handshake. The host must invoke it only after start/create/
  resume returns, and must schedule settlement itself. No automatic poller or
  full planner/worker/scheduler integration is claimed.
- No additional concrete generation/start/drain defect was established here.
  The existing 13 inert Runner tests passed during review.

## Extended asset upserts

All six store types were reviewed below HTTP availability. Each mutation holds
company advisory lock and calls the guard before writes. Recursively created
root/subdomain/IP assets also pass guard/validation within the same transaction.
Main association provenance failure rolls back writes. Scope OR filters remain
parenthesized and global access remains explicit opt-in. URL subset rejects
userinfo and malformed authority/escapes; this is deliberately narrower than Go,
not complete URL canonicalization parity. Offline root-domain resolution and
company/mutation-policy adapters remain trusted required dependencies. Explicit
company reassignment/deletion are unavailable, not silently emulated.

Existing 27 asset-store tests passed. Real schema constraints, trigger provenance,
archive races, lock ordering against all other processes, filesystem evidence
retention and live provider cancellation were NOT independently validated here.
The separate integration QA stage must report its own evidence, not inherit a
production-readiness claim from these fake-contract tests.

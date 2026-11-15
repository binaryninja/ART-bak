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

## Follow-up: explicit host and exploration HTTP

Reviewed `agent/host.py` and `server/exploration.py` after delivery. Existing host
11 and exploration HTTP 13 inert tests passed. No new concrete defect was
established in this pass. Host mutations reserve a counter before manager calls;
settlement holds a condition fence and refuses same-thread reentrance. Drain
blocks admission while allowing pause and includes the result sink in actual
thread drain. This depends on every lifecycle caller using this exact host;
direct manager/runner calls and callbacks waiting for another host operation are
explicitly outside the contract. Callback/store timeouts remain necessary.

Exploration resolver ownership is checked against the requested live task before
reading. Source stores must be read-only, match task/exploration IDs and be direct
sources; inherited open/running/paused intents are filtered from history/graph.
Malformed/duplicate/unknown task selectors fail rather than selecting active/global
state. Inherited cursor nonprogress and the 10000-page budget fail explicitly.
Node-board hydration remains an explicit 503. Trusted resolver/store adapters are
not a sandbox: checks do not independently prove authorization of arbitrary host
callbacks, and no live cross-process snapshot/traversal test was performed.

## Follow-up: credential-bearing profile persistence

Reviewed `db/profiles.py` and its test contracts. A concrete public/private
round-trip hazard was found: redacted URLs produced by `public_dict()` were
accepted by save and could replace private URL credentials/query tokens with the
literal `[redacted]` placeholder. Likewise a displayed `…1234` API-key hint could
be persisted as the actual credential. No profile HTTP API is present, so this is
a persistence integration hazard, not a demonstrated remote endpoint exploit.
Two independent tests added to `test_finding_runner_review.py` require rejecting
those placeholders before any executor access. They initially failed. The owner
then rejected redaction markers in raw/decoded URL fields and masked-key values,
with explicit real replacement values required. Independent rerun: all five tests
in the review module pass, including both profile regressions. This finding is
fixed and verified at the reviewed snapshot. Public fragments now retain a
redaction marker rather than silently disappearing on a DTO round-trip.

Otherwise list selects hints rather than API keys, private profile repr omits all
fields except identity, URL userinfo/query/fragment are redacted in public DTO,
empty key update preserves the stored key, writes use bound values, and storage
exceptions are replaced by a credential-free message. Active selection clears
then sets within one transaction; schema `uq_llm_one_default` enforces uniqueness,
and a missing target or commit failure rolls back. Concurrent conflicts can fail
rather than silently elect two defaults. No delete/reference-repair parity is
claimed. The owner also suppressed hints for keys of length <=4, addressing the
review recommendation that short keys must not be fully exposed by a last-four
hint. No original credential was copied to an external service during review.

## Follow-up: scoped node-board persistence

Reviewed `db/nodeboard.py` and reran its 13 inert contract tests: all pass. No
concrete new scope or pagination defect was established. Queries bind captured
exploration identity before optional filters, and search OR predicates remain
parenthesized. Both edge endpoints must match the edge exploration, preventing
cross-exploration neighbour expansion. Inherited visibility filters apply to
page, ID, both edge endpoints and anchor lookup. Positive IDs are deduplicated
and bounded; pagination validates signed-int64 offset and caps page size at 200.
Scope is copied from the original store rather than reread after mutation.

Count/page are explicitly separate statements and may observe concurrent changes;
no snapshot guarantee is claimed. Anchors supply IDs only, not independently
authorized asset DTO hydration. The docstring's 'size 20..200' means a default of
20 and cap of 200; actual positive sizes below 20 remain accepted like upstream.
No mutation, transport, live PostgreSQL query or traversal execution occurred in
this review. No additional failing regression was necessary for this pass.

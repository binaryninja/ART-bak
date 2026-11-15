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

## Follow-up: build translations and profile HTTP

Source-only comparison against translator-provided pre-edit snapshots covered
Dockerfile, build.sh, update.sh and .github/workflows/release.yml. Dockerfile and
workflow differences are comments only. Shell changes are help/log/prompt text
and comments; commands, variable expansions, option parsing, answer defaults and
control flow remain unchanged. No build, release, install or update was executed.

Profile HTTP registers only upstream paths, using protected Router defaults.
Inputs reject duplicate JSON, invalid types, masked credentials and oversize
bodies. Blank-key handling reaches key-preserving ProfileStore SQL through an
explicit coordinator; there is no accidental direct persistence fallback. The
persisted-but-apply-pending outcome is distinguishable and redacts adapter errors.
Runtime snapshots use an allowlisted projection rather than arbitrary key fields.
Actual cache invalidation/runtime application remains coordinator-owned, not
implemented or verified merely by these route contracts.

One additional output-integrity issue was found: coordinator.save's return value
was serialized directly as id. Invalid None/bool/out-of-range IDs or accidentally
returned private structures produced a 200 response. Independent inert regression
covers seven invalid return values and requires fixed 500 without leaked content.
The owner added exact positive-int64 result validation with a fixed storage error.
Independent rerun: all six review tests pass, including all seven invalid return
values yielding redacted 500. This finding is fixed and verified.
This is a trusted-adapter integration hazard, not a claim that remote callers can
supply arbitrary coordinator implementations.

## Follow-up: node-board HTTP composition

Reviewed new `_nodeboard` HTTP composition and application-factory argument
forwarding. Pagination uses the effective capped size and validates offset before
board reads. Board exploration/read-only identity is checked against the resolved
task; neighbour edges must touch the page, neighbour IDs must be requested, anchor
keys must belong to selected nodes, and unique neighbours/assets are bounded.
Only the selected task participates in board pages, not inherited source merging.
Missing adapters fail unavailable; errors do not expose resolver exception text.

Found an incomplete-resolution gap: an asset resolver could omit requested IDs,
causing silent loss of decorations. The owner now requires exact requested-key
coverage; explicit `None` means a confirmed deleted asset. Independent tests in
`tests/test_nodeboard_http_review.py` verify missing/partial coverage ->503,
explicit deleted assets ->200, and redacted resolver exceptions ->500. All three
pass after the fix; existing exploration HTTP suite (21 tests) also passes.
Application factories forward both board adapters without silently enabling a
global asset resolver. No worker source was changed by this reviewer.

## Follow-up: opt-in provider HTTP transport and skill prose

Reviewed `providers/http.py` and ran its 15 fake-connection tests: all pass.
No concrete new origin, credential-forwarding, cancellation-boundary or cleanup
flaw was established. Origin allowlists are explicit; HTTPS certificate/hostname
verification is mandatory and rechecked before requests. Plain HTTP requires both
literal loopback IP and explicit opt-in. Redirects are not followed, environment
proxy variables are ignored, and caller Host/framing/proxy-auth headers are
rejected. Errors omit request/response secrets. Body limits detect both declared
and incremental excess; ambiguous framing and compression are rejected. Success,
error and explicit abandoned-stream close paths own and close connections.

Timeout/cancellation guarantees remain cooperative: DNS, slow headers, and blocking
platform calls may exceed the nominal total deadline. This is documented rather
than claimed as forced cancellation. Consumers must explicitly close abandoned
streaming Responses. TLS trust is hostname-based, without DNS/IP pinning. No
network, DNS, live TLS handshake, real credentials or providers were used by this
review; passing fakes do not certify those external integrations.

Source-only skill review covered `api-recon/SKILL.md`, `api-recon/reference.md`
and `scopesentry/SKILL.md`: zero raw Han, LF line endings, unchanged code-fence
counts (10, 20, 9). Changed examples are translated display labels/placeholders
and comments; scopesentry example task names remain consistent across creation
and search references. One grep example preserves exact Chinese detection terms
via Bash ANSI-C Unicode escapes rather than dropping the patterns. Authorization,
recon-only boundaries, no real credential-login testing, and destructive-action
prohibitions remain present. Requested wording correction: translate frontend
'enter shell' as 'Application shell', not OS-suggestive 'Shell access'. No skill
commands or target operations were executed. These are documentation translations,
not additional authorization or evidence that the tools were run.

## Follow-up: FIFO coordinator and company store

Two scheduler integration issues were reproduced in independent inert
`tests/test_scheduler_review.py`: LocalHostAdapter.ready propagated expected
RuntimeUnavailable (aborting the whole tick rather than deferring an unavailable
head task), and a deferred admission that retained live owned work did not refresh
capacity before admitting the next task. At limit one the fake host then owned two
workers. The owner fixed expected readiness errors to defer and refreshes host
occupancy after every admission receipt. Independent rerun: both review regressions
pass, as do the owner's 16 scheduler tests. Also flagged statically that drain
acquired the tick's non-reentrant lock, permitting callback reentrance to self-block;
the owner now rejects busy drain with nonblocking acquisition and added a passing
reentrance regression. No hung thread was created by this reviewer. Both reproduced
findings and the static drain concern are resolved at this snapshot. Local fingerprints/exact-row checks remain single-process only,
not database generation leases. Deadline settlement is explicitly unimplemented;
expired tasks are deferred, not falsely declared complete or timed out.

CompanyStore lower-priority review and its 11 inert tests passed. Constructor
company IDs are copied into a fixed tuple; empty scope permits none. Bound read
and resolver predicates retain scope, mutations use the shared company advisory
lock, and scoped upsert cannot create outside an existing permitted name-key.
Resolver priority and ties remain root-domain, longest CIDR, then normalized ICP,
with lowest company ID ties. Scope-rule changes and deletion fail explicitly
rather than silently skipping global attribution recomputation. No concrete new
issue was established; no live SQL or company/network lookup was performed.

## Follow-up: profile factory and runtime coordinator

Reviewed `providers/factory.py`; all 10 inert factory/coordinator tests pass. No
new concrete defect established. Validation precedes transport construction and
store mutation; unsupported proxy/rate/session/retry behavior is rejected instead
of silently ignored. Existing-key preservation constructs a validation candidate
with the private stored key while saving the original blank-key request through
ProfileStore's preserve-key SQL. Maximum-token caps and streaming-disabled behavior
remain explicit; unsupported Anthropic thinking configuration fails before I/O.

Coordinator mutation/reload is serialized within one instance. After persistence,
reapplication failure clears published runtime and cache rather than presenting
the old profile as newly applied. Injecting server.ProfileApplyPending is required
for the HTTP persisted-versus-applied response; that exact injection is covered by
the tests. Defaults deliberately do not import server code, discover credentials,
choose allowed network origins, or construct a runtime coordinator automatically.
Public snapshots exclude keys and redact URLs. Private factory cache/configuration
remains trusted host state, not a public DTO or a general secret-redaction layer.
Existing external provider references survive invalidation and must be separately
drained for credential revocation, as documented. Tests use construction-only fake
transports and no network calls.

The earlier skill terminology correction is now verified: api-recon uses
'Application shell', not 'Shell access', for frontend rendering completion.

## Follow-up: parent-bound side-question persistence

Reviewed `db/sidequestions.py`, its 13 inert contract tests, corresponding
`db/side_questions.go`, and the session/request schema. Independently reran all
13 tests successfully. No new concrete port defect found in this pass.

- Parent shapes reject ambiguous conversation/task identities and non-integer IDs.
  Task-main and task-worker snapshots/starts lock the live, non-archived task with
  its exact exploration; workers additionally require a same-exploration intent
  that is not stopped. Parent locks precede the session lock.
- Snapshot replacement compares `(run_id, version)` lexicographically, not version
  alone. Snapshot reads also validate the embedded parent against the bound store.
  `start` deliberately uses the supplied valid snapshot rather than requiring it
  to equal the newest persisted snapshot, matching Go; capture freshness remains
  a runtime caller responsibility, not a persistence guarantee.
- Start serializes on the session row, scopes idempotency by current generation
  and client ID, rejects changing a repeated question, and checks the one-running
  constraint before insertion. The schema independently enforces uniqueness.
- Update includes request ID, bound parent key, caller generation, current-session
  generation, running status and strictly increasing sequence. Terminal results
  cannot be overwritten by later callbacks through this API. Memory writes also
  require a currently running request in the current generation.
- Clear increments generation/resets memory and deletes only the bound parent's
  requests in one transaction. It does not recreate request rows, and update is
  UPDATE-only. Session locking orders start against clear; fake rollback tests
  verify transaction shape, not live PostgreSQL scheduling. Clear is permanent
  deletion implementation only: no real data was deleted and hosts still need
  the required user authorization before invoking it.
- History is bounded to 1..100, replay to 20, and paged replay binds generation,
  after-cursor and the current exchange's ordinal ceiling. Request lookup is
  parent-scoped (stronger than original Go's ID-only lookup).

Remaining integration boundaries, not newfound regressions: reads/history and
clear do not independently reject archived parents, matching the source; a future
HTTP/runtime adapter must establish parent access and lifecycle authorization.
There is no capture/provider/cancel orchestration or archive restore here. Fake
SQL tests cannot prove concurrent clear/update behavior or database constraints
under load; that belongs to the separately authorized PostgreSQL QA stage.

## Follow-up: company HTTP and application wiring

Reviewed `server/companies.py`, matching company handlers in `server/assets.go`,
its 13 fake tests, and the newly added application wiring. All 13 company HTTP
and 14 application tests pass independently. No concrete new defect identified.

Six routes remain authenticated, including unsupported mutation routes. Missing
storage returns 503; scope mutation, deletion and reattribution return 503 before
any store write. Nonempty initial scope is rejected before company creation,
including empty-string rule entries; metadata-only creation uses transactional
`create`, not metadata upsert. Conflict is 409 and unexpected errors use fixed
redacted messages. Unsupported fields/query parameters, duplicate JSON keys,
invalid IDs and oversized payloads fail explicitly. Exact positive int return
validation prevents a malformed creation adapter result from being serialized.
List/detail preserve source envelope, numeric IDs, nullable empty list and array
scope. Existing scoped CompanyStore remains scoped when explicitly supplied;
scoped creation stays unavailable with no transaction.

`build_application(company_store=...)` forwards the supplied repository without
startup calls; missing injection produces 503. `build_persisted_application`
explicitly constructs the documented authenticated-administrator/global company
repository, not task-inferred ownership. New application tests cover auth,
unavailable routes, empty list, fake creation and no company startup queries.
The common WSGI factory default body cap remains 1 MiB, stricter than this service's
2 MiB/upstream cap; callers must raise it if matching the full source body limit
is needed. No real company changes or external registry requests occurred.

## Follow-up: persisted worker-intent boundary

Reviewed `db/workers.py`, all 12 inert tests, the exploration node/version and
archive schema, Python ExplorationStore writers, and Go ClaimIntent/session-key
references. Independently reran 12 worker tests successfully. No concrete new
blocking defect found in this persistence-only boundary.

There is no invented workers table: identities are intent nodes, scoped by exact
exploration and a live task/exploration row. The explicit lifetime guard wraps the
transaction; parent task locks precede node update/append locks. All archive rows,
including failed archive attempts, conservatively block operations. Absent guards
or transcript adapters fail closed. Reads do not resolve inherited sources.

Claims and transitions bind intent ID, exploration, expected state and version;
version increments fence prior worker generations. Terminal reopening is explicitly
unsupported. Append locks the node and requires running state plus exact content
version before invoking its injected backend. IDs cannot become transcript path
components except as positive integers; session keys match source expN-worker-iM.
Backend exceptions propagate and roll back the SQL transaction, while guard context
exit remains structural. Unsupported archive/delete cannot accidentally perform
partial cleanup.

Integration cautions: this is not a lease or worker execution engine. Existing
`ExplorationStore.claim_intent` remains the source-compatible state-only claim;
callers must not mix it into an execution host expecting every admission to bump
this facade's generation. Existing graph mutation methods likewise do not use this
facade's lifetime guard. Transcript read takes the shared parent lock but no explicit
node lock (unlike append), so the module's broad statement that calls hold a node
lock should not be read as a consistent read snapshot against arbitrary graph
writers. The transcript backend must enforce generation/idempotency/path safety;
no backend is supplied here. A backend append can survive a later PostgreSQL commit
failure, so retry requires reconciliation, exactly as the module documents. No real
transcript, DB, provider, network or cleanup operation was executed in this review.

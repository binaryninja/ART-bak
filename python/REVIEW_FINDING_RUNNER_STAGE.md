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

## Follow-up: pull-based WSGI streaming and SSE framing

Reviewed `server/streaming.py`, WSGI integration and the 13 inert streaming tests.
Independently reran all 13 streaming tests plus the existing WSGI byte-response
suite successfully. No new blocking defect found; no listener or network used.

The iterator is created/pulled lazily after header validation/start_response;
no stream buffering or Content-Length is introduced. Invalid response metadata
closes the stream and produces a fixed 500 before headers start. Gateway header
failure closes then propagates. HEAD and no-body statuses close without pulling.
Exhaustion, invalid/oversized chunks, producer exceptions and explicit disconnect
close release iterator/source and callback once under ordinary Exception paths,
including recursive/repeated close. Distinct source and iterator objects are
released separately, identical ones only once. Existing bytes responses retain
computed framing and HEAD behavior.

Post-header producer exceptions never produce secret-bearing error bytes or a
second header block. They intentionally become EOF with local `failed=True`, not
an explicit failure frame: downstream protocols must require their own terminal
success event and must not treat EOF as completed work. The SSE helper prefixes
every normalized data line; event/ID reject CR/LF/NUL, retry is a bounded exact
integer, and framing limits use encoded byte lengths. Hop-by-hop and nominated
connection headers are removed; header values remain validated for WSGI encoding.

Remaining constraints: gateway must close on disconnect and enforce timeouts;
blocking producer code cannot be interrupted here, total stream length is not
bounded, and cleanup callbacks must not raise process-control BaseExceptions.
SSE framing checks the byte cap after constructing the frame, so this helper is
not an input-allocation bound for arbitrary giant strings. There are no streaming
routes/runtime completion contracts supplied by this primitive alone.

## Follow-up: live log SSE subscription

Reviewed `server/logs.py` against `server/server.go:streamLogs` and the Go log
subscription boundary, plus the new virtual-time/fake-gateway tests. Independently
reran `test_log*.py`: 16 tests passed (12 stream, four existing log tests). No new
blocking defect identified; no listener, network or actual logging capture ran.

Subscription precedes recent(last 1000) capture, and the captured cursor filters
queued overlap without rewinding a future caller cursor. A 256-entry FIFO drops
new arrivals when full, matching Go's nonblocking channel; sequence gaps are not
silently filled from unrelated persistence. Frames retain source JSON data-only
shape and 20-second comment heartbeats. Reconnect parsing is intentionally stricter
than Go; invalid/ambiguous cursors return 400 without subscribing.

Router authentication runs before handler/subscription. HEAD closes immediately
without polling. Snapshot construction failure and oversized/event serialization
failure unsubscribe; the latter is a failed EOF, not a successful terminal event.
Explicit close clears queued entries and wakes a blocked poll. Ring shutdown
clears subscribers under the ring lock, then closes outside it. Subscription close
releases its condition before acquiring the ring lock, avoiding inversion against
append's ring-lock then condition-lock order. Existing virtual wait tests exercise
heartbeats without sleeping; the event-controlled test exercises actual condition
wake-up with bounded thread join. No unauthorized stream data was observed.

Deployment boundaries remain explicit: queue count, not log text bytes, is bounded;
large single log frames exceed the common stream chunk limit and terminate the
stream rather than being silently truncated. Logs themselves may contain secrets
from callers and are not semantically scrubbed by this adapter; authenticated
administrative authorization is essential. Gateway disconnect close/timeouts are
still required, and one already in-flight frame can race with concurrent close.

## Follow-up: task-template persistence and HTTP

Reviewed `db/task_templates.py`, `server/task_templates.py`, the corresponding
Go modules, shared rule validators and preset schema. Independently ran 12 inert
template tests successfully; no new blocking defect found in store/HTTP. Factory
wiring was not yet present at this review snapshot and needs separate verification.

Seven rule kinds match the source: domain/URL/fuzzy forms remain free-form rather
than being incorrectly executed or parsed as commands; exact IP/CIDR validate
addresses, default action is block and omitted enabled is false. Rule arrays and
name/text lengths are bounded; unsupported profile/source-task fields are rejected.
Preset writes only bind normalized field values and JSON. Atomic CASE-based PATCH
preserves omitted values while explicit category null clears the FK, and null
rules clears only that snapshot. No read-modify-write window is introduced.
Category FK failures map to redacted 400; name conflict to 409; absent patch/delete
targets to 404; unexpected backend errors to a fixed response. Four routes use
existing administrator authentication; presets are intentionally global rather
than task-scoped. Schema confirms category deletion SET NULL and no task ownership
relation. DELETE targets only the preset row and cannot delete created tasks.
No real deletion occurred; executing this permanent deletion for a user still
requires the applicable confirmation. No rule engine/application-to-task wiring
is implied by saving rule snapshots.

Boundary notes: the stored JSON decoder trusts existing database rule shape (new
writes normalize it), and this is not a database-corruption repair layer. HTTP
body parsing uses Python JSON byte decoding, including its UTF-16/32 auto-detection;
strict UTF-8-only behavior is not claimed. No live FK/transaction race or upstream
execution was tested here.

Factory follow-up completed in the same review stage: inspected the delivered
optional template_store injection, protected registration and persisted factory's
TaskTemplateStore construction. Independently reran 16 application tests, all
passing. Missing injection is 503 after auth; no template startup queries or task
creation/execution occur. The earlier pending-wiring note is now resolved.

## Follow-up: English planner/goals/Auto prompt builders

Compared `agent/planner_prompts.py` directly with Go plannerDefaultTmpl,
plannerSystem, goalsDefaultTmpl/goalsScopeTail, goals message assembly and
promptcatalog Auto body. Independently reran eight pure-text tests, all passing.
No authorization weakening or removable code-owned tail found in this assembly.

Translation retains planner/non-executor distinction, initial read-only orientation
only before facts and capped at three probes, one recheck per negative direction,
quantitative goal acceptance with evidence, nonduplication and dependency ordering,
maximum four intents per submission, and operation constraints over route diversity.
The added explicit constraints-over-hard-minimum wording clarifies rather than
expands authorization. Goal decomposition keeps outcomes separate from steps,
requires explicit self-contained constraints and forbids invented goals. The scope
tail preserves exact host rather than expanding to root domain, explicit IP/CIDR,
no inferred targets/company scope, audit reason and scope-before-goals ordering.
Auto remains a platform operator rather than a pentest executor and reports actual
results only.

Overrides support substitution only, not functions/control flow; invalid syntax
falls back wholly and substituted input is not recursively evaluated. Planner
artifact/constraint tails are appended outside the editable body. Goal scope tail
is likewise appended after override only when the host explicitly asserts the
scope-tool capability. Description occurs only once in the user message; it is
not duplicated into an editable system variable.

These are text contracts, not model-behavior or authorization tests. The editable
body can still be contradictory, as in the source; appended wording cannot replace
host-side scope/approval enforcement. Existing source ModeBypass/tool execution is
not ported here, nor Plan execution, goal graph writes, wake state or solo pentest.
No tools, providers, probes or network were exercised.

## Follow-up: dashboard stats aggregates and runtime selection

Reviewed `db/stats.py`, `server/stats.py`, Go `server.stats`, and new application
wiring. Independently ran 12 stats plus 18 application tests successfully.

One lifecycle discrepancy is pending clarification: task mapping currently filters
`deleted_at IS NULL` but not `archived_at IS NULL`, despite describing persisted
live tasks. Archived task rows retain their exploration identity; if a stale runtime
adapter supplies one, pruned exploration data can appear as empty live counts.
Parent and HTTP owner were notified. Rejecting archived mappings would align this
boundary with its stated live-only contract; archival dashboard support would
instead need explicit archive aggregate handling. This was source inspection,
not a live database concurrency reproduction.

Otherwise explicit task selection never falls back to an unrelated active task:
missing selected task is 404, mismatched runtime task or missing dependency is 503,
and backend failures are redacted rather than replaced by zeros. Runtime state is
explicitly injected and type checked; no provider readiness/heartbeat is fabricated.
The stalled heuristic matches source: running, no active LLM call, persisted
running intent, positive last activity, and more than 120 seconds elapsed. Goal
and intent counts preserve source latest 10,000/latest 100,000 caps before filtering.
Other grouped counts cover the real scoped exploration/global asset tables.

Global assets require explicit allow_global on StatsStore; persisted factory
intentionally opts in for its authenticated administrator dashboard. Construction
forwards runtime callback without invocation or stats queries. SQL values are bound;
HTTP rejects duplicate/unknown selectors. Aggregate/runtime reads remain separate
snapshots, not one consistent transaction. No DB/provider/network action ran.

Lifecycle clarification from the owner: normal Go archive completion sets both
archived_at and deleted_at, so the existing predicate excludes normal completed
archives. The additional archive predicate is defense against inconsistent or
legacy archived-only state, not evidence of a normal-flow archive exposure. Owner
is adding this conservative guard and a fake regression; verification pending.

Stats guard verification: `task_counts` now requires both deleted_at and archived_at
NULL. Independently reran 13 stats tests including the owner's archived-only fake
regression; all pass. The lifecycle clarification above is resolved.

## Follow-up: side-question HTTP, dispatcher ownership and SSE

Reviewed `server/sidequestions.py`, changed coordinator admission/dispatch ownership,
and `sidequestion/dispatch.py`. Independently reran 19 HTTP tests and 12 dispatcher
(including independent review) tests, all passing with inert providers/stores.

All 11 routes inherit auth. Parent-family resolution validates exact conversation
or task/exploration/worker identity and store key; request lookup checks exchange
ID and same parent key. Every subsequent polling read re-resolves request access
and requires unchanged parent/key before fetching. Missing request or changed
generation emits cleared; scope mismatch fails rather than displaying another
parent's exchange. Finite poll-budget/cancellation EOF is explicitly not success.
Only terminal snapshots/cleared events carry terminal meaning. Captured model
request prompts are omitted from history metadata; stored errors are replaced by
a fixed message. Sequence-driven SSE uses JSON escaping for payloads.

Original trimmed question remains durable/idempotent; mention-expanded preparation
is separately passed as agent_question. Idempotent original requests do not admit
or dispatch again. Preparation failure precedes admission. Missing or mismatched
coordinator/dispatcher capability fails before durable start. Dispatch success 202
means ownership accepted, not inference completion. Closed/full dispatcher
rejection aborts unstarted admission durably; uncertain persistence remains 500
with retained coordinator/rejection ownership, not a false terminal success.
The added coordinator claim_dispatch fence validates handle and exchange identity
and binds exactly one executor, preventing foreign/double-dispatch consumption.

Boundary conditions: resolvers remain responsible for actual live authorization
and archive/delete barriers; type/key checks do not replace it. Cancel reports
accepted, not drained. Clear adapter must serialize admission/cancel/clear and
requires permanent-delete authorization; no real clear occurred here. Runtime
requires a thread-safe executor and host recovery for retained failed settlements.
A provider blocked forever is not preempted by dispatcher close. No default
preparer, provider, resolver or lifecycle adapter is silently invented.

Factory code observed forwarding all side adapters explicitly without starting
workers or invoking callbacks. Dedicated new factory tests still pending at this
snapshot; existing 18 application tests pass.

Factory verification completed: all 21 application tests pass independently after
new side-question cases landed. These exercise actual coordinator/dispatcher with
inert provider, accepted 202 then idempotent 200, auth before admission, explicit
adapter forwarding and bounded finally-close/drain. Pending factory-test note above
is resolved. No live provider, network or database was used.

## Follow-up: task metadata PATCH and batch pause/resume

Reviewed new `server/tasks.py` metadata/batch paths against Go task_metadata and
task_control handlers, plus manager update/control delegation. Independently ran
12 new metadata/batch tests and existing task HTTP tests successfully. No new
blocking defect found in this endpoint slice.

Metadata accepts only name/pinned, preserves source null-as-omitted behavior and
rejects no-op/blank reset. Go Unicode whitespace trimming and 200-code-point bound
are retained; invalid UTF-8, NUL, duplicate keys and unsupported fields reject
before mutation. False pin is carried explicitly and changes only pin metadata,
not lifecycle. The source 16 KiB body bound is retained. DTO preflight ensures a
known missing metrics dependency is caught before repository update. Manager
updates name/pinned cache fields under its lock, not description/goal/lifecycle.

Batch retains source 256 KiB/100 normalized-unique-ID bounds, ordered canonical
positive numeric IDs, separate dedupe of invalid inputs and per-item failures.
All body/schema validation precedes controls; only pause/resume are accepted.
Each task is resolved independently; unexpected errors are fixed/redacted and
malformed control DTOs cannot become success or leak arbitrary fields. Prior
successful actions remain durable if another item fails, as source semantics
require. Missing runner/profile admission remains manager-owned and does not
produce a successful resume; fake real-manager tests exercise this boundary.

Not an all-or-nothing batch transaction: per-item results are intentional. Metadata
preflight cannot guarantee subsequent DTO enrichment/connection success after a
write; callers must reread after an ambiguous error rather than assuming rollback.
An adapter returning malformed data after an action can likewise yield an error
although that action happened. No real task, runner, database or provider ran.

## Follow-up: fresh-database schema bootstrap

Reviewed `db/schema.py`, packaged SQL, packaging configuration and 11 inert schema
tests. All 11 pass independently. Parsed both source and packaged SQL using the
already-installed pglast: both contain 285 statements. Parsed structures match
after removing source-position metadata, comments and normalizing four U& legacy
literal spellings (including function-body strings). Existing source-equivalence
and literal-decoding tests also pass. No DDL or real database was executed.

Opt-in and non-maintenance expected database are mandatory. Advisory transaction
lock precedes exact identity/version/read-only/string/temp checks and public-schema
inventory. Existing public relations/functions/types, extra user schemas and
non-plpgsql extensions reject before scripts. Search path is then set locally;
packaged digest prevents partial edits, and the final exact 49-table manifest
must match before commit. Failures roll back through the transaction adapter and
redact unexpected database exceptions. Constructor does not connect or change
settings; it requires a borrowed idle autocommit connection. Scripts use native
multi-statement execution, not unsafe splitting or placeholder replacement.

Package-data configuration includes schema.sql. Four Unicode-escaped historical
values preserve decoded database semantics rather than changing migration matches.
Static inspection found no obvious post-PG12-only construct: helper explicitly
avoids PG16 pg_input_is_valid; trigger EXECUTE FUNCTION and used JSONB/aggregate
syntax predate PG12. This is not a demonstrated PostgreSQL12 compatibility result:
pglast is a newer parser, so minimum-version claims still need isolated versioned
DDL QA. Cursor adapters must propagate all multi-statement errors; fakes cannot
establish driver/protocol behavior.

The fresh-only guard is not a general existing-database migration or full bootstrap:
seed/catalog/configuration stages, optional usage/records tables and runtime
startup are intentionally absent. Advisory locks coordinate only cooperating code;
exclusive operational access remains mandatory against external DDL races. No real
user data, infrastructure, schema or credentials were changed by this review.

## Follow-up: Norma-compatible filesystem transcripts

Read `transcripts.py` and local Norma v0.4.3 reference transcript/types source.
Independently ran 12 existing temp-file tests and added two passing independent
`test_transcripts_review.py` checks: hardlinked lock rejection without mutation,
and post-replace directory-fsync failure leaves an uncertain committed record,
whose stable-UUID retry through WorkerTranscriptBackend does not duplicate it.
Only disposable temporary directories were used.

Main/sidechain layout, record fields, parent UUID chaining, boundary exclusion and
four cumulative usage fields match Norma. Unknown fields survive reconstruction;
compaction/working-set selection is explicitly not implemented. Unlike original
append, this store uses full-file CAS replacement and stricter sizes/permissions.
Session/agent names exclude traversal/separators; root components and all opened
file/lock/directory components use no-follow/dirfd. Regular-file and single-link
checks reject symlink/hardlink/FIFO data or lock targets. Stable session locks span
read-compare-write; temporary files use exclusive creation, private mode, fsync,
atomic same-directory replace and directory fsync. Repeated CAS writers do not
silently overwrite another turn.

Guarantees assume trusted root/ancestors and cooperating writers: permission
ownership is not itself checked, original Norma append does not share advisory
locks, and a privileged directory-renaming actor is out of scope. Reads create
lock files and, for sidechains, directories; they are not filesystem-read-only.
Existing record identity/schema is trusted by file placement: load checks JSON
objects, but not session_id/agent_id/UUID/message types. Upcoming chat adapters
must not use such embedded metadata as authorization. New commit batches validate
identity and UUID duplication, but this is not corrupted-history repair.

Worker backend requires an explicit authoritative generation guard held through
commit; no default generation check is invented. UUID-equal record retry is
idempotent, while changed content conflicts. Filesystem durability is not atomic
with database state; post-rename failure is intentionally ambiguous and must be
reconciled, not blindly retried as a new record. Deletion/export remain unavailable.
No new blocking defect found within these stated boundaries.

## Follow-up: frontend SSE fix and durable chat transcript bridge

Independently ran four frontend-shaped query-JWT WSGI tests after the reserved-token
fix. All pass. Updated FRONTEND_INTEGRATION_GAPS.md to mark this concrete blocker
resolved, without implying runtime/deployment readiness.

Reviewed `chat_transcripts.py` and ran all 14 temporary-file bridge tests, passing.
Identity/schema/version checks, user/prefix/result digests and generated-range
validation prevent malformed ordinary metadata from granting turn authority.
Loaded session/agent identity is now checked here, addressing the lower-level
TranscriptStore trust boundary noted earlier. Finalized replay requires verified
terminal metadata; unresolved duplicate/admission remains blocked. The bridge
preserves source Norma message records and adds namespaced admission metadata,
not arbitrary control messages in provider history.

Exact-byte revision CAS fences external append/clear; completed history cannot
rewrite its admitted prefix. Generated message/tool-result batches validate before
one atomic replacement, including zero-output denied turns without fabricated
messages. Terminal digest reconciliation supports retry after uncertain rename
without rerunning inference. Admission uncertainty remains pending rather than
claiming completion. Recovery requires an explicit guard asserting old writers
cannot act and persists interrupted_unknown_effects, not guessed success.
Actual ChatAgent/inert-provider tests confirm durable completion and duplicate
replay without another model/tool invocation. Sidechain paths remain separate.

Remaining boundaries: hashes are integrity checks, not cryptographic authenticity
against a writer controlling the trusted root (such a writer can recompute hashes).
The root/ancestor/cooperating-lock assumptions still apply. Pending inspection is
not permission to resume, and the host must supply authoritative recovery fencing.
History validation repeatedly reconstructs prefixes; large long-lived transcripts
need performance measurement/compaction design beyond bounded file size. Finalized
ChatOutcome reuse reports persisted=True with result=None; a future HTTP adapter
must retrieve/display existing output rather than fabricate a new response. No
conversation HTTP routes/default runtime are created by this bridge. No blocking
new defect found in the reviewed scope; only temp fixtures and inert callbacks ran.

## Follow-up: conversation metadata and factory integration

Reviewed `db/conversations.py`, `server/conversations.py`, Go conversation handlers,
new factory forwarding and associated tests. Independently ran 12 conversation
and 23 application tests, all passing; no blocking new defect found.

List/create/metadata/profile DTOs retain numeric IDs and source optional profile/
pin fields; internal session identity is exactly conv-N and is not injected into
public DTO. List running flags require exact boolean callbacks for nonempty lists,
not a default false. Empty lists need no invented running state. Source creation
status is 200, not 201. Missing explicit agent capability fails 503; unknown agent
is 404, invalid/missing selected profile is 400, and callback/storage errors are
redacted. Global authenticated administrator access matches source, not a claim of
per-user tenant isolation. No arbitrary path is accepted as a session ID.

Create inserts child then locks profile FOR KEY SHARE in one transaction; missing
profile rolls back child insertion. Profile update locks child before profile.
Atomic metadata PATCH preserves omitted title/pin, and unpin changes only pin
metadata. Default title is translated. Delete and batch delete remain 503 before
storage mutation, avoiding false FK-only cleanup without runtime/retest/sidequestion/
transcript drain. No real conversation execution or deletion occurred.

Application wires optional store plus explicit running/agent/profile callbacks;
persisted factory constructs the repository without reads or implicit agent startup.
Updated FRONTEND_INTEGRATION_GAPS.md for metadata progress: /api/agents and ordinary
message/history/detail/stop/upload routes are still missing, so metadata parity is
not a working frontend chat flow. Validation uses Python whitespace trim/JSON byte
encoding auto-detection, stricter shape checks but not exact Go byte-format parity.

## Follow-up: executor pool and explicit profile runtime assembly

Reviewed `db/pool.py` and `server/runtime_setup.py`. Existing 14 pool and five setup
tests pass independently. Added `test_pool_runtime_review.py` with three passing
inert checks: cancellation retires a transaction lease, concurrent close does not
close the same idle executor twice, and durable profile-save/apply failure recovers
only after explicit reload without invoking transport.

Concrete cancellation defect: `_acquire` catches BaseException from the factory,
restores capacity, then always raises PoolFactoryError. This swallows process/
cancellation exceptions unlike operation/transaction paths. Parent agreed the
contract should preserve these after cleanup; added a fourth failing regression
requiring cancellation propagation and subsequent capacity reuse. Owner notified
to change only ordinary Exception into the fixed redacted factory error. No
cancellation payload is logged. Fix verification pending.

Otherwise construction stays lazy; per-thread outer leases remain pinned through
commit/rollback and nested savepoints use the same executor. Caught nested errors
still retire after outer completion. Duplicate factory objects cannot be borrowed
or closed while another lease owns them. Closed pools wake blocked acquisitions;
active leases can finish and are retired, and failed closes remain inspectable for
retry. Capacity timeout does not pretend to interrupt a factory or driver close;
there is no forced SQL cancellation or mutation retry.

Runtime setup validates options before reads/construction, explicitly reloads
stored profiles and loads tasks without starting them. ProfileApplyPending is
wired correctly: durable save remains, published runtime is cleared on apply
failure, and HTTP reports persisted true/runtime_applied false. It never substitutes
provider probes or silently constructs task/sidequestion execution. Transport
factory remains trusted construction-only and host-origin constrained; the helper
cannot enforce a malicious callback's lack of network side effects. Outstanding
provider refs/transport resources still require host drain and explicit close.
All review tests used fake SQL/executors/transports; no connections were opened.

Cancellation fix verified: owner now restores capacity then re-raises non-Exception
BaseExceptions unchanged; ordinary factory exceptions remain redacted. Independently
reran all four review tests successfully. The pending cancellation finding above
is resolved; no known blocking finding remains from this pass.

## Follow-up: async conversation HTTP and dispatcher/frontend contracts

Reviewed in-progress `server/chat.py`, `chat_dispatch.py`, activity persistence and
`server/chat_setup.py` against frontend api.ts/chat polling and Go activityDTO.
Found two concrete integration/lifecycle defects, now fixed by owners: HTTP emitted
raw activity id/created_at rather than frontend seq/ts, breaking merge identity and
history cursors; dispatcher callback BaseException could terminate a worker without
settling its receipt. Added `test_async_chat_review.py` with two independent passing
regressions for frontend DTO identity and cancelled callback receipt settlement/
retained uncertainty. Independently ran 14 HTTP, 12 dispatcher, four setup tests,
all passing. Only inert providers, fake SQL and temp transcripts were exercised.

Also flagged and verified corrected dispatcher error mapping (busy/conflict 409,
closed/capacity 503, persistence uncertainty 503) and finite close-timeout validation.
Parent-found issues were fixed independently: real tools/limits preflight now
precedes durable user activity, and GET history/detail plus stop authorize separately
from provider/resource resolution, so unavailable model configuration does not
prevent reviewing history or requesting cancellation.

Source-shaped GET returns ascending item order, source seq/ts, cursor and hasMore;
reverse pages return latest bounded records in chronological order, while since
polling retains ascending progress. Detail is separately parent-scoped. POST uses
actual frontend message/attachments shape and returns 202 only after dispatcher owns
busy/cancel state and durable user activity. Attachments/mentions fail explicitly,
not silently disappear. Stop requests cancellation, not proven drain. Durable
transcript completion precedes terminal activity, and uncertain execution/projection
retains busy ownership for reconciliation rather than enabling an automatic rerun.

No full streaming parity claimed: this is conversation polling, not task/main-agent
SSE; terminal batch projection is not live per-token/tool progress. Local receipts
are bounded, not evicted into silent reruns. Frontend currently sends no durable
client key, so network retries/new POSTs without Idempotency-Key are distinct;
restart can duplicate user activity because SQL activity and JSONL are not a shared
transaction. Durable turn metadata prevents rerunning a known finalized client ID,
not automatic crash recovery. Explicit single-process authority, deletion barriers,
provider/tool gates and host drain remain required.

## Follow-up: opt-in chat application assembly and task-main core

Independently verified `test_chat_setup.py` (9), `test_task_chat.py` (8),
`test_chat_assembly_review.py` (1), and `test_task_chat_core_review.py` (3).
Only disposable JSONL, fake SQL/provider and inert callbacks; no listener/network.
No new implementation blocker identified in this pass.

Assembly: `build_chat_application` wires the same JWT router for metadata,
profile configuration and conversation execution. Explicit agent registry excludes
retester; live running status comes from the dispatcher, profile readiness uses
supported nonstreaming configuration validation. Model identity is per-bundle HMAC
of private configuration, not a public key-bearing string. Invalid tool registry
fails before dispatcher creation; registration failure cancels and drains the newly
created dispatcher. Profile transport/database resources remain host-owned even
when construction fails; the helper does not claim to close caller resources.
Startup configuration reads are explicit and no inference is performed.

Task core: external lifecycle guard and current-scope authority fence admission,
provider/tool boundaries and commit; this is not implicitly connected to AgentHost.
Independent tests invalidate scope during capture (no provider call/commit, retained
busy), inject an uncertain begin after durable admission (retained busy, no provider),
and reject invalid registry before admission (no lingering busy state). Existing
real temporary JSONL test confirms finalized duplicate reuse without inference;
existing cancellation test confirms persisted cancellation can release ownership.
Uncertain/stale results deliberately retain task ownership. No reconciliation API,
autonomous planner, activity projection or task-main HTTP readiness is implied.
Callbacks already in flight cannot be undone; deletion requires cancellation plus
verified drain under the real host lifecycle barrier.

`FRONTEND_INTEGRATION_GAPS.md` refreshed: optional runtime/profile assembly and
202/polling message/history/detail/stop service now described as implemented;
agent catalog, uploads/mentions, task-main HTTP and default deployment remain gaps.

## Follow-up: persisted agent catalog

Source review compared `db/agents.py` and `server/agents.py` with Go
`db/config.go:AgentBindingCounts/CreateAgent`, `server/dto.go:AgentDTO`,
`server/server_mgmt.go:pgCreateAgent/pgGetAgent`, `agent/promptcatalog.go` and
`agent/wrapup.go`. Seven store and eight HTTP tests pass with inert adapters.
New independent `test_agent_catalog_review.py` covers missing-key reads,
transaction rollback when current-prompt activation fails, and the real driver's
missing-row exception on conflicting creation.

Concrete blocker found: `create` uses ON CONFLICT DO NOTHING RETURNING but originally
handled only a None return. PostgresExecutor raises NoRowsError, so a duplicate
becomes HTTP500 instead of409. The independent regression reproduced this. The
catalog owner has now added the narrow NoRowsError-to-AgentConflict mapping; all
three independent tests pass on the fix. No implementation changes made by reviewer.

Other reviewed contracts: source DTO string agent ID, nullable numeric profile ID,
configuration counts and detail prompt/version fields are preserved. Binding-count
queries match source; failed reads propagate rather than inventing zero counts.
Zero counts for absent grouped keys reflect actual query results. Custom creation
and English starter prompt activation are one transaction, stronger than source
best-effort seeding. Starter translation preserves concise accurate assistance,
available-tool use only as needed, user-request scope and no invented facts.
Current prompt ID is persisted but is not part of source AgentDTO. Public profile
choices select no credential columns. Worker/planner/mainagent code-owned defaults
require explicit resolver rather than invented defaults. Execution readiness is
separate, requires enabled assistant role plus literal-True host readiness, and
explicitly rejects retester; normal builtin task roles are not assistant roles.
Configuration and stored prompt alone do not register executable tools/agents.
This review does not include app wiring, real PostgreSQL or provider execution.

## Follow-up: offline archive bundle and readonly snapshot exporter

Reviewed `archive_bundle.py` / `archive_snapshot.py` against source package layout,
format versions and SnapshotTaskArchive query scope. No extraction/restore/prune or
lifecycle publication is implemented. Optional preinstalled zstandard is required;
this is not a standard-library-only codec. Snapshot requires an explicit host-held
quiescence barrier plus same-transaction ownership/aggregate callback. All 22 table
selectors are task/exploration-bound, with source usage OR semantics and direct
source-task relations. Repeatable-read readonly transaction starts before schema
advisory lock and table reads. Row counts/serialized row bytes/total bytes are
bounded; serialization work inside PostgreSQL is not avoided. Metadata callback
is trusted host code, not independent proof of exclusive ownership. Parent tables
must match selected IDs; exported data is not a relational restore-safety audit.

Bundle validates compressed SHA256, expansion/member bounds, traversal, backslash,
absolute/colon paths, duplicates, file-parent collisions, regular-file-only types,
owner task/transcript prefixes, evidence hashes/lengths and streamed record count.
Unknown compatible fields are retained. Publication walks existing POSIX directory
components without following symlinks, writes/fsyncs an exclusive0600 temp and
hardlinks create-only; explicit directory trust remains necessary. Independent
post-link directory-fsync failure test confirms the final bytes can exist despite
an exception, temp cleanup succeeds, and retry cannot overwrite the destination.
Callers must reconcile uncertainty, never infer absence from failure.

Review issues sent to owner: real missing-task NoRowsError escaped SnapshotUnavailable;
malformed streamed data_counts=[] escaped BundleInvalid as AttributeError; decoder
stream read capped output bytes but lacked explicit max_window_size allocation cap.
New test_archive_review.py reproduces the first two using inert fixtures and tests
publication uncertainty in a disposable directory. Owner fixes independently verified:
all three regressions pass. Decoder now supplies explicit max_window_size; malformed
JSON recursion and count shapes normalize to BundleInvalid. Export requires explicit
archive_snapshot_transaction capability rather than arbitrary generic transaction.
PostgresExecutor rejects nested/nonidle use, starts its transaction, and issues the
fixed readonly repeatable-read SET directly on a cursor before yielding; DML rowcount
checks are unchanged. Pool pins one lease, rejects nested upgrade and retires on
failure. Advisory-lock SELECT uses fetch_one, not execute rowcount. NoRowsError on
missing parent normalizes to SnapshotUnavailable; updated independent test reaches
the parent lookup after a successful lock result. Live DB evidence remains QA-owned.

## Follow-up: tool catalog metadata

Reviewed new db/tool_catalog.py and server/tool_catalog.py against db/tools.go,
server/customtool.go and pgUpdateTool. Eight store/five HTTP tests and three new
independent test_tool_catalog_review.py regressions pass. No implementation blocker
identified; exec was treated only as opaque JSON throughout, never executed.

Actual source columns and DTO match, including calls aggregated from tool_usage,
not an invented approval column. Missing reads use fetch_all, duplicate INSERT
NoRowsError maps to conflict, unrelated storage errors remain redacted failures.
Writes transact; updates lock the tool before sorted referenced-agent key-share
locks. Missing bindings fail before UPDATE; the independent test verifies rollback.
Builtin schema shape permits description/default annotations but retains property
names even when a parameter is itself named description: changing its type fails
before write. Custom update/delete cannot mutate system tools; generic metadata
updates do not write kind/exec/deferred. Nested duplicate JSON and nonboolean flags
fail before any store write, independently verified. Runtime callback approval,
registration/reload and revocation/draining are absent deliberately: persisted
bindings/enabled/schema do not grant execution. A host must not infer that deleting
a catalog row stops an already-running callback. Reset/test routes remain503.

## Follow-up: archive ownership metadata and recording-schema setup

Compared archive_metadata SQL with Go archiveAssetMetadata/hostsForTaskDeletion/
taskArchiveAggregates, and recording DDL with db/llm_usage.go and db/commands.go.
Seven metadata and seven recording-schema tests pass; independent
`test_archive_metadata_review.py` adds two passing guards and one bound-gap repro.

Metadata preserves source distinctions: exclusive-host candidates consider task_ids
and graph anchors; exclusive asset candidates also include task links/finding IDs,
exclude company-owned assets and protect assets shared through other live tasks'
task_ids/graph anchors. These are source metadata rules, not generalized deletion
authorization or permission to prune. Usage aggregates retain task OR exploration
scope; exclusive IDs must be among exported assets. Invalid/ambiguous URL parsing
fails closed rather than omitting potentially shared hosts. Aggregate JSON byte
limits reject failures, not substitute counts. Review blocker sent to architecture:
protected-host query rows have a row-count limit but no byte limit before fetch_all;
a single2048-byte protected domain succeeds under max_json_bytes=1024. Independent
repro expects rejection before aggregate reads. Fixed and independently verified:
all three review tests now pass. SQL wraps the bounded host rows with cumulative
octet_length window sum, projects NULL sentinels above budget before fetch_all,
and Python also totals host bytes including snapshot fields. Oversize rows reject
rather than silently dropping shared-host protection.

Recording setup requires explicit action and expected non-maintenance DB, takes
the schema advisory lock, verifies identity/version/read-write/string/temp state,
and sets local public/pg_catalog search path in one transaction. Source SQL is
unchanged: only absent tables/indexes and five nullable legacy TEXT additions.
Catalog checks refuse unexpected columns/type/default/nullability, non-table,
partition/RLS/triggers/constraints and incompatible indexes. Independent extra-
column test verifies rollback before table DDL. Postchecks cover missing tables/
indexes. No recording is enabled, no existing data is rewritten/dropped. Adapter
must supply exclusive schema transaction authority. Sequence checks currently
validate the nextval expression and expected name, not the actual sequence's
ownership/configuration; do not call this a complete hostile-catalog audit.
No live DB, commands, provider or network execution by reviewer.


## Follow-up: explicit builtin catalog seed subset

Reviewed catalog_seed.py against Go builtinAgents/seedBuiltins and
SeedPromptIfEmpty/SavePrompt; all seven authored tests pass. Fixed six keys, roles,
creation shell/time defaults and eight prompt variables match the source subset.
English changes affect display prose only. Deliberate divergence is documented:
ON CONFLICT DO NOTHING preserves customized rows rather than repeatedly overwriting
display/variable metadata. A conflicting custom row under a builtin key aborts the
transaction. No shell-default migration, tool/MCP registration, intercept rules,
profile setup, runtime capability, prompt-body fallback or variable cleanup is
silently supplied. runtime_ready/default_prompt_bodies_seeded remain false.

Prompt seeding requires an explicit nonempty reviewed template and action. It
holds the advisory lock plus agent FOR UPDATE, preserves any existing current
pointer (including an intentionally empty user prompt), inserts the next history
version and conditionally activates it in one transaction. Pointer failure rolls
back the inserted version; missing agents distinguish NoRowsError from other
storage failures. This is offline/lifecycle-barrier metadata initialization, not
live cache refresh or full startup equivalence. No scoped blocker found.

## Follow-up: bounded streamed assistant-turn assembly

Reviewed agent/streaming.py and ChatAgent/conversation preflight/setup/task-core
integration. Normalized provider events, not raw SSE, are consumed with explicit
start/terminal sequence; incomplete EOF fails. Whole turns buffer before tool
argument validation/authorization, avoiding partial tool execution. Tool indexes,
IDs, fragment pairing, duplicate keys/nonfinite JSON, event/byte/block/output-token
budgets and sticky refusal are checked. Usage snapshots take monotonic maxima,
not fragment sums. No retry or invented nonstream profile fallback is added.
Optional progress is provisional, and not mounted as frontend durable completion.
Generation/profile/cancel fences bracket every advance and progress callback.
A blocking next/close still needs transport deadlines and cooperative cancellation.

Concrete regression in new test_streaming_review.py: a stream generator's finally
rotates the selected profile during close after message_stop. The last fence ran
before close, so the completed turn escaped and an inert tool executed under the
stale profile. Fixed by owner and independently verified: all three review regressions pass.
The successful return is now after finally/close and a final live/cancel fence.
ChatAgent also rechecks profile/transcript before and after host approval, covering
configuration changes during a permission wait. Independent early-EOF and cleanup-
failure tests pass; cleanup exceptions are
redacted and cannot return successful model completion. No live provider/tools used.


## Follow-up: explicit executable tool registry bridge

Read tools/registry.py; independently ran all16 test_tool_registry*.py tests.
No new scoped blocker found. Only explicit host Tool callbacks are eligible;
catalog rows must be enabled and bound to the selected agent, kind builtin with
empty exec, and structurally match the code schema. Stored shell/command/script/
HTTP metadata is never interpreted. Defaults are applied before callback validation
and the exact argument-bearing Action is separately authorized at dispatch.
Deferred discovery requires explicit unlock, not an execution grant. Exported
handles carry an epoch; successful reload revokes old ones and resets unlocks.
Reload checks active calls twice around the lifecycle guard. Dispatch counts an
owned active call before waiting for the external guard and releases it even on
BaseException. Approval/cancellation/revision are checked before callback; the
external guard spans callback effects. Drain pauses new admission and reports
remaining calls without pretending to preempt them. A successful reload reopens.

These are local capability/lifecycle contracts, not a sandbox, distributed lease,
automatic catalog adapter or default full application toolset. Host must supply
real policy/scope revision authority and callback containment. Process callbacks
retain their own gate and root/environment/termination checks. Parent-reported
isolated harmless process fixture results are separate evidence; this reviewer
ran only inert registry fixtures and did not independently run subprocess QA.

## Explicit inspection assembly and working-context compaction review

Independent source/fake review of `server/inspection_setup.py` and
`agent/compaction.py`: no blocking defect found within their documented opt-in
contracts. No live provider, database, listener, subprocess, or recording was
started by this review.

Inspection assembly requires all policy callbacks and exact executor identity for
tool metadata before runtime creation. Recording store identity is checked on each
resolution, with existing selection/body authorization and explicit redaction;
there is no default global resolver or recording enablement. Tool metadata policy
requires literal True and does not register commands as executable tools. Routes
are protected. The staged duplicate scan normalizes parameter names before any
mount; assembly failure cancels/closes its newly created runtime. Returned service
handles are host-level objects, not independently authenticated APIs. Hosts still
own callback trust, database shutdown, and checking normal drain receipts.

`test_inspection_setup_review.py` adds two independent regressions: a preexisting
`/api/llm/records/{other}` collision prevents all mounts and calls close(cancel=True),
and truthy non-boolean policy results cannot reach the catalog. All nine inspection
tests pass (seven implementation tests plus two review tests).

Compaction preserves a separate deep copy of raw input and never writes transcripts.
Provider serialization validates tool-result pairing before summary admission;
whole API rounds are retained even when the requested recent-message boundary
would otherwise split a tool pair. Source comparison against pinned Norma v0.4.3
confirms UTF-8 bytes/4 estimator, 4/3 fallback scaling, reservation cap and threshold
tiers. Object JSON float spelling remains the explicitly documented approximation;
exact raw JSON preserves source byte counts. This is an explicit one-shot helper,
not installed automatic compaction or full Norma history/marker/skill parity.

The exact serialized summary action is approved before the single provider call.
Cancellation/current-scope fences run after approval, after provider completion,
and before returning. Hosts must include live model/ownership validity in their
current callback; the helper cannot infer it. Refusal, partial termination, tools,
invalid usage, oversized/nonreducing output and final-budget overflow fail without
publishing a working view or retrying. Summary text is labeled lossy/untrusted,
not evidence or permission. Required verbatim policies and durable raw history
remain host responsibilities; summary accuracy is not guaranteed by these checks.

`test_compaction_review.py` independently verifies retention of a complete tool
round across the tail boundary with raw-copy isolation, and explicit refusal even
with an otherwise successful stop reason performs exactly one call and leaves raw
input unchanged. All fifteen compaction tests pass (thirteen implementation tests
plus two review tests). No implementation files were changed.

## Prompt subset renderer independent review

Reviewed `agent/prompt_render.py` without executing Go/templates/providers. Catalog
fields match source `agent/prompt.go`; built-in seed candidates reuse the existing
five English editable bodies. No pentest/reporter/custom default is invented and
nothing seeds the DB or installs this renderer into runtime builders. Code-owned
artifact/traffic/constraint tails remain a separate required host operation.
Parsing validates all conditional arms before emission; inserted values are
verbatim nonrecursive text, no HTML conversion, functions, methods, pipelines or
eval. Missing fields fail closed; invalid override syntax falls back wholly to
reviewed default, and an invalid default raises rather than emitting raw template.
Template/action/depth/output byte limits are explicit. Host-selected excessive
limits do not establish an independent process memory/stack sandbox.

`test_prompt_render_review.py` adds independent exact-output comparison of all
five seed bodies with existing English builders, inactive unsupported-directive
fallback, and strict malformed-comment rejection. Initial review found four
malformed comment forms accepted (spaces before opening /* or after closing */,
and extra spaces around trim markers), contrary to Go lexer `lexLeftDelim` and
`lexComment`. This can silently accept an override Go would reject instead of
falling back; it does not execute any directive. Owner notified with four failing
subcases; fix verification pending.

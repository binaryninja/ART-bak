# Dynamic authorized task reads

## Implemented boundary

`TaskRuntimeReadRouter` and `TaskReadBuilder`, explicitly passed as `read_router`
to `build_task_runtime_application`, resolve each authorized selector through the
actual `TaskStore`. One application can inspect newly created tasks without
rebuilding a fixed-task view. It keeps the original manager, admission controller
and explicit engine pump. There is no ambient active-task or global-task fallback.

Supported dynamic reads: task detail, stats, intents, scoped findings, activity
list/history/detail/stream, explicit main-session metadata, and separately authorized public/redacted profile
metadata. Operation, raw activity, asset provenance, global stats and profile
grants remain independent. Read-policy revision is not execution authority.

JWT authentication precedes resolution; selector permission precedes task SQL.
Each request/stream retains its original task/exploration mapping and policies.
Archived/deleted or remapped tasks fail closed. Builder callback tuples and bounds
are pinned against replacement. Original authentication identity is checked after
callbacks and projection, including before releasing activity frames. The fixed
read overlay has the corresponding final authentication fence.

## Evidence

- Independent fixed-overlay review: eight tests, including five route-specific
  authentication replacement cases.
- Independent dynamic wrapper/builder/negative review: 16 tests passed at review.
- Final stage 79 acceptance: five actual PostgreSQL 17 integration groups and 26
  focused tests passed. One application existed before either task was created;
  two tasks progressed via explicit owned ticks and inert local model responses
  to authoritative completion. JWT task/detail/stats/intents/activity/findings/
  profile requests used the same application. Denied selectors caused no task SQL.
- Activity reconnect cursors, cross-task isolation, selector revocation, mapping
  replacement and authentication replacement during serialization/frame production
  were tested. Reads triggered no inference. Findings in this execution fixture
  were genuinely empty; stage 77 separately covered nonempty synthetic findings.
- Route audit now records **180 unique method/path routes, 196 declarations and
  ten exact audited alternative groups**. All declaration locations are retained;
  unexpected duplicates still fail. These counts are not runtime parity claims.

Stage 79 final immutable manifest:
`59591807de5a902629f740670723fc4f07a27af466b136add7b5d0f5a63d20df`.
The final QA report supersedes provisional stage 79 evidence.

## Limits and frontend boot sequence

No listener, browser/frontend build, background pump, live provider or production
state was exercised. Activity SSE is finite committed replay, not provider token
streaming; EOF is not task completion. Positive runtime deadlines, recovery,
inherited activity and unrelated UI actions remain outside this slice.

Static Next source audit identified dynamic main-session metadata as a prerequisite:
`web/src/app/(main)/function/tasks/detail/_tabs/sessions-tab.tsx:942–974` first
awaits `api.mainSessions(taskId)`, then current-segment history, then activity SSE.
The resolved-task builder now constructs the actual bounded MainSessionsStore and
MainSessionsService only with two separate explicit source/request grants. The
router resolves `/api/exploration/main-sessions?task=...` under the same pinned
mapping and final authentication boundary. No reset or chat write is included.

Owner and independent tests cover the ordered two-task sequence, absent capability,
separate grants, late revocation, remapping and final authentication changes.
Disposable PostgreSQL acceptance is recorded separately in ISOLATED_QA.md.
Main chat status/message submission/reset, worker controls and other detail tabs
still need separate composition and acceptance; no whole-screen readiness is
claimed here.

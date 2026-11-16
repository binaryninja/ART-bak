# Restricted final runner: implemented subset and remaining lifecycle work

`artex.agent.final_assessment.FinalAssessmentRunner` is an explicit, one-shot
runner over the separate final-assessment journal and canonical `ChatAgent`.
Construction does not allocate a journal row, invoke policies, start a thread,
read graph data, or call a provider. `run()` is an explicit host action.

## Implemented contract

- Exact journal/resource/configuration pins and the module's fixed catalog digest.
- Independent provider resolver, model/action grants, graph-read grant and final
  transcript-persistence grant. The ordinary frozen execution token is not used
  to authorize this separate final round.
- Journal reserve/start, exact canonical scoped transcript admission, one fixed
  client ID, finalized transcript evidence and journal finalization. Existing
  attempts cannot be adopted by a new runner. The same runner cannot run twice.
- One code-owned `list_goals` native tool using actual bounded local
  `ExplorationStore.list_goals_bounded`. Read grant/current checks bracket SQL and
  guard exit. No arbitrary supplied tools, inherited/global graph fallback,
  `prove_goal`, `add_intent`, worker launch, commands, filesystem, MCP, network
  tools or task chat. The separately authorized model transport is not a tool.
- Independent finite positive wall budget and finite turn/token/tool/request
  limits from the immutable final configuration. Total-clock checks include
  resolver/grant callbacks, not just the model loop. Invalid/backward clocks fail
  closed. Timeout is not zero-as-unlimited.
- Separate explicit cancellation, with final transcript persistence subject to
  its own grant. Cancelled, refused or failed terminal outcomes are evidence of
  that outcome only, not task success. Unknown persistence/effects quarantine
  the retained owner rather than authorize retry.
- Execution callbacks are revoked immediately when ChatAgent returns, before
  journal finalization callbacks. Retained contexts cannot execute another turn
  while final evidence is being recorded. Final receipt reconciliation still
  cannot clear uncertainty or confer terminal-write permission.

Cancellation and budgets are **cooperative**. A synchronous provider callback
that ignores its context can block beyond the budget; the runner cannot forcibly
interrupt that callback or honestly report that it drained. No automatic expiry
watcher or background final-round launch is implemented.

## Verification scope

Seven owner and five independent synthetic tests currently pass. They exercise
actual canonical transcripts/journal protocol, the local read tool, grants
revoked during SQL or model callbacks, cancellation/expiry, exact retained
callbacks, and no replay. The positive task-clock pins are synthetic protocol
fixtures, not production timed-task admission. Disposable PostgreSQL acceptance
is separately coordinated; this document does not assert its outcome.

JSONL and PostgreSQL remain non-atomic. Finalized transcript/journal evidence is
not current goal truth. Later consumers must reverify exact canonical evidence,
configuration, lifetime and fresh persisted graph under their own barrier.

## Deliberately missing

No evidence-proof mutation, full source final-planner parity, terminal task write,
manager owner retirement, positive-timeout runtime admission, restart recovery,
pending takeover or automatic retry. The existing timeout rejection gates remain.
See [FINAL_ASSESSMENT_STATUS.md](FINAL_ASSESSMENT_STATUS.md) for journal details.

## Proposed next boundary: fresh-truth terminal CAS (not implemented)

Source `server/engine_timeout.go:settleTask` chooses **done** when the final planner
reports goals met, otherwise **timeout**. It uses `SetTaskStatusGuarded`, preserving
an already-terminal outcome. It does not convert timeout into `stopped`:
`stopped` can describe drained intent execution, not the timeout task status.
Source planner completion is driven by persisted evidence-linked `prove_goal`,
not an arbitrary assistant sentence (`agent/planner.go`). This restricted read-only
runner cannot newly prove a goal; it can only inspect already-persisted states.

Minimum next implementation should be a separate, explicitly granted terminal
store/coordinator, not a call to generic `set_terminal_status_guarded`:

1. Reserve a manager settlement capability for the exact assessing owner,
   finalized journal revision, clock, configuration and canonical receipt. Keep
   admissions frozen; ensure no active final invocation or outstanding owned
   write remains. Read-only lease or copied receipt is insufficient.
2. Under the original lifetime barrier, lock the exact parent task first, then
   journal/bootstrap identity, matching existing lock ordering. Refuse changed
   task/exploration/run/config/clock, deletion/archive, uncertain ownership or
   missing canonical evidence. Existing terminal state is preserved, not
   overwritten by a later timeout result.
3. Query bounded current local goals and their valid local evidence/proof links
   in that transaction. Require complete coverage and explicit graph-read/
   terminal-write authority. A model's text, end-turn reason or cached read cannot
   select `done`. An empty/unsupported goal graph must have an explicit source-
   grounded rule; do not let vacuous all-goals-met become completion. Initially
   refuse unsupported inherited/goalless configurations rather than guess.
4. For supported goal graphs, derive `done` only from fresh proven goal truth;
   otherwise derive `timeout` only with the exact expired clock and completed
   bounded assessment/drain protocol. Never derive task status from a worker's
   `stopped` state. Refusal/uncertainty must remain distinguishable: uncertainty
   cannot be silently classified as a successful timeout settlement.
5. CAS task lifecycle and a separate durable settlement receipt atomically,
   including exact journal revision and chosen status. The current final journal
   has no terminal-settlement phase/receipt: add an explicit reviewed schema/API
   rather than overload `finalized`. Reverify canonical evidence before and after
   attempted SQL; acknowledge the JSONL/SQL atomicity limit.
6. Only after confirmed commit and final ownership checks synchronize manager
   metadata and retire the exact retained owner. If commit or registry update is
   uncertain, quarantine and inspect the same settlement receipt; never rerun
   inference or issue an unqualified second terminal write.

This still would not enable positive-duration tasks: first-call stamping/context
clamps, automatic expiry observation, pause ownership and shutdown/drain behavior
need connected end-to-end acceptance before removing admission gates.

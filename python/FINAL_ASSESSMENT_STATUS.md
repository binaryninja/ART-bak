# Final assessment: journal prerequisite, not an executable final round

## Implemented

The manager's `reserve_deadline_assessment` accepts only the exact retained
clean-drained freeze, original token/handle/lease and final configuration digest.
It retains an `assessing` owner. This does not restore ordinary execution,
authorize a provider/tool, settle a task, or release a deadline owner.

`artex.db.final_assessment` provides:

- `install_final_assessment`: an explicit additive, fresh-only installation into
  an explicitly named database and `public` schema. It requires
  `allow_create=True`, compatible database settings, existing task/exploration/
  bootstrap tables, and absence of both journal table and sequence. It does not
  adopt or migrate existing objects, alter default schema installation, or run
  automatically during application construction.
- `reserve`: one database-generated attempt for the same live owner, exact task,
  exploration, bootstrap run/revision/identity, immutable stamped clock and final
  model/prompt/tool-policy/limits digest. The database clock must show expiry.
- `start`: exact retained receipt and revision CAS from `reserved` to `running`.
  This records journal phase only; it does not start inference.
- `record_finalized`: exact canonical scoped transcript turn and result digest
  recorded by owner/revision/full-identity CAS from `running` to `finalized`.
  Refusal or cancellation is a terminal transcript outcome, not proof of goals.
- `reconcile`: read-only inspection of the same retained owner's record,
  including a quarantined attempt. It cannot adopt another process's attempt,
  clear uncertainty, retry inference, or authorize terminal task writes.

The durable phases are exactly `reserved`, `running`, and `finalized`.
Uncertainty is retained by the in-process journal/manager quarantine, not an
invented fourth SQL phase. Owner tokens are private bookkeeping, not runtime
credentials or permission to call a model. Pending attempts have no automatic
restart/takeover path.

## Reviewed boundary fixes

All operations preflight pinned resources before entering the original captured
lifetime guard; a substituted guard is not invoked. Grants and binding checks
also run after transaction and guard exit, so exit-time revocation cannot return
a usable receipt. Canonical transcript store/root/agent/recovery-guard bindings
remain pinned.

Finalization revalidates the exact canonical evidence immediately before its CAS,
after attempted writes/transaction callbacks, and after guard exit. Missing or
changed evidence cannot be blessed by a previously fetched digest. A possible
write followed by failure yields redacted uncertainty and quarantines the
original owner; cleanup errors cannot turn it into a safe-to-retry result.

JSONL and PostgreSQL are **not atomic together**. Repeated checks narrow callback
windows but do not establish a distributed transaction. Any future terminal
writer must reverify the same canonical evidence and fresh persisted graph truth
under its own exact ownership barrier. A finalized journal row alone is not
terminal authority.

## Evidence and limits

The owner and independent review suites currently contain 19 passing synthetic
cases, including guard-exit revocation, SQL-callback transcript removal, resource
replacement, stale identity and write uncertainty. Their positive clock cases
explicitly inject synthetic prepared-timeout pins; they are not evidence that
production positive-duration task admission works. Separate disposable-PostgreSQL
acceptance is coordinated independently; this document does not assert its result.

There is **no restricted final ChatAgent runner, automatic expiry watcher, final
goal-truth decision, task terminal CAS, restart recovery, or positive-timeout
runtime enablement** in this slice. No provider calls, tools or real targets were
used to establish the journal protocol.

## Proposed next slice (not implemented)

A dedicated `FinalAssessmentRunner`, not `PlannerCycle`, would consume the exact
retained assessing capability and running journal receipt. It would use canonical
`ChatAgent` and the receipt's unique session/scope identity, with an independent
finite positive wall budget plus finite turn/token limits. The expired task budget
must never become zero-as-unlimited. Constructors/readiness remain inert.

Required boundaries:

1. An explicit host grant authorizes this one final invocation independently of
   ordinary execution/pause. Scope pins include task, exploration, bootstrap run,
   final attempt, full configuration and original manager/handle/lease/guard.
   A new runner cannot adopt a pending attempt or use a read lease as authority.
2. Provider binding must freshly attest the exact selected model/config without
   relying on ordinary execution-current, which is intentionally frozen. The
   final capability needs its own narrowly checked current/commit distinction;
   it must not reactivate planner or worker authority.
3. An allowlisted native registry exposes only explicitly authorized bounded
   local graph reads and, if separately granted, evidence-linked `prove_goal`.
   Each proof needs fresh evidence/asset/provenance approval and same-scope SQL
   fencing. No `add_intent`, worker launch, scope edits, task chat, shell, MCP,
   filesystem or network tools; the explicitly selected model transport is a
   separate authorized dependency. Unknown tools fail closed.
4. Exactly one canonical turn is admitted. Cancellation, timeout or persistence
   uncertainty retains ownership; no new client ID, inferred retry or replay of
   provider/tool effects. A committed transcript may be recorded only through
   the journal's exact evidence checks. Recovery remains unsupported.
5. The runner returns a verified assessment receipt/outcome, never task success.
   A later separately reviewed terminal writer must freeze all remaining writes,
   recheck fresh goal/evidence truth and perform exact task/journal CAS. A model's
   prose, end-turn reason or journal `finalized` phase cannot replace that step.

The source reference is `server/engine_timeout.go:runFinalPlannerRound`. A narrower
bounded local-tool subset should be labeled as such, not claimed to reproduce
all source final-planner behavior. Positive runtime gates stay closed until the
whole stamp/expiry/drain/assessment/terminal path has independent acceptance.

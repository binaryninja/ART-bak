# Final settlement: explicit local-proof prerequisite

`artex.db.final_settlement` connects an exact finalized assessment to a narrow
terminal SQL write and manager retirement. It does not enable positive-duration
task admission, an expiry watcher, inference, recovery or automatic retries.

## Explicit installation and ownership

`install_final_settlement` is a fresh-only additive installer requiring an
explicit expected database, `public` schema and `allow_create=True`. It refuses
an existing settlement table or missing assessment/task/exploration tables. It
never runs during application startup or alters the default schema installer.

`FinalSettlementStore` requires the actual finalized journal/receipt and an
independent policy for reserve, read and status-specific write operations.
`TaskManager.reserve_deadline_settlement` validates the canonical finalized
receipt and clean exact handle before retaining a separate opaque capability.
The registry enters `settling`, which cannot authorize ordinary execution,
admission or shutdown completion. A lease or copied receipt is not sufficient.

## Persisted decision and write

The original lifetime barrier and task-before-journal lock ordering are retained.
The store verifies the exact expired clock, prepared bootstrap identity and final
journal revision. Existing terminal task states are never overwritten.

Only complete bounded LOCAL goal/evidence graphs are supported:

- A nonempty set of goals, with known open/met/abandoned states.
- Every met goal must have a local `proves` edge from a confirmed fact or finding.
- Source/inherited task relations, goalless tasks, missing proof, unknown states
  and graph overflow fail closed. There is no silently truncated decision.
- All valid local goals met selects `done`; otherwise the supported remaining
  graph selects `timeout`. `stopped` is not substituted for a task timeout.

This is deliberately narrower than source `prove_goal`, which can use direct
source-task evidence. The source timeout coordinator uses `done` when its final
planner establishes goals met and otherwise `timeout`, via a guarded terminal
write. Here the assistant's prose/end-turn reason has no decision authority.
Persisted proof structure is verified, not independently re-proven scientifically;
the host's proof-writing policies remain essential.

The task terminal CAS and immutable settlement receipt are in the SAME SQL
transaction. The receipt binds task/exploration/run, exact final attempt/revision,
final identity, canonical result digest, graph digest, selected status and actual
completed timestamp. Graph truth and canonical evidence are rechecked after
write-policy callbacks, writes, transaction exit and guard exit.

Before registry retirement the store also rereads the full persisted task
status/completed timestamp/clock/scope and complete settlement receipt, plus the
prepared bootstrap identity and exact final journal row. A successful INSERT result alone is insufficient.
Postcommit callbacks that change status, remove the receipt or change graph
truth cannot produce a confirmed result.

Private one-shot committed evidence is consumed outside the manager registry
lock. The manager rechecks the exact retained capability/resources, synchronizes
terminal metadata and removes only that run/lease owner. It cancels the old token
outside the lock so the existing clean cancelled-handle retirement path can
remove runner inventory. A token-cancellation failure restores a quarantined
owner and reports uncertainty, not successful complete shutdown.

## Uncertainty is not rollback

A transaction error before commit may roll back both SQL writes. An error AFTER
commit may leave `done` or `timeout` and its receipt durably stored while the
manager still retains the old/quarantined generation. The API reports
`SettlementUncertain` and does NOT claim rollback, success, safe replay or a new
model run. Even a cleanly refused reserved attempt retains ownership for explicit
inspection. Automatic reconciliation/adoption/repair is not implemented.

JSONL and SQL are not a distributed atomic transaction. Repeated canonical checks
narrow known callback windows; they do not change that fact. All participating
graph writers must respect the original lifetime barrier and task-parent lock.
Mixing unfenced generic graph writers is unsupported. External privileged changes
after the last check remain outside the process-local ownership guarantee.

## Verification and remaining work

Nine owner and five independent synthetic tests currently pass, including proof
removal before CAS, committed graph/task/receipt drift, rollback, terminal
preservation and token-cancellation failure. They use actual authored registry
and canonical transcript components but synthetic positive prepared-clock pins.
They do not demonstrate production positive-timeout admission. Disposable
PostgreSQL acceptance is separately coordinated and is not asserted here.

Still absent: real timed admission, first-call/expiry coordination end to end,
forced interruption of blocking providers, inherited/goalless completion rules,
full final proof-writing parity, restart recovery, and automatic reconciliation
of committed SQL with retained registry uncertainty. Existing positive-duration
runtime gates remain closed.

## Disposable PostgreSQL acceptance

Stage90 passed five actual PostgreSQL groups and fourteen focused tests for
local-proof done, open-goal timeout, exact retirement, atomic receipt-insert failure,
and postcommit task/receipt/journal drift quarantine. Manifest:
`48c5f128b5c17611ff67d6ee6b90c0ff147b50c7c781e9ba00fed10e8e6b92fa`.
This retains the synthetic prepared-timeout limitation above; ordinary timed
runtime admission is not enabled by this evidence.

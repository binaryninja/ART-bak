# Zero-timeout same-process task continuation: work in progress

New `TaskContinuationOwner` and `ContinuationPersistence` retain a private
original prepared suspension, verify a stopped zero-timeout local task, and use
an exact paused task `updated_at`/bootstrap-identity CAS. BootstrapHandle has a
private fresh-token engine-only construction path that reuses the unchanged
original prepared receipt rather than calling goal initialization again.
Original configuration, role selection, executor, guard and canonical resource
bindings remain required. Timestamp/result decoding is strict; a SQL row or
caller-created/copy receipt alone is not resume permission.

Manager/host/runner reservation and installation are connected explicitly.
No default setup or automatic resume is enabled. The owner expects original
manager phases pausing→paused→resuming→installed. Unknown cancellation, SQL or
construction effects retain both old and new attempts; no rollback, uncancel or
terminal resurrection is asserted. First owned advance activates the private
continuation only after exact new installation and fresh persisted verification.

Scope excludes positive timeouts, clock/observer transfer, restart recovery,
queued/pre-bootstrap continuation, terminal rerun, and automatic graph-state
reopening. Existing task-pause worker cleanup returns that worker's intent to
open; explicit paused/stopped/blocked nodes are not bulk reopened. Go's failed
pause compensation is not claimed by this fail-closed subset.

Initial evidence included 47 bootstrap regression tests and six continuation
owner/review cases. Updated integrated evidence follows. Genuine PostgreSQL
acceptance remains pending; this file is not a deployment claim.

## Integrated fixture evidence (not PostgreSQL acceptance)

Manager, host and runner hooks are now connected via explicit
`host.install_continuation(owner)`. Actual owned-thread tests exercise a blocked
worker, task pause, canonical cleanup returning its intent to open, clean pause
commit, fresh-token installation, engine-only first advance retaining the exact
prepared receipt without goals replay, and a subsequent worker invocation. A
preexisting explicitly paused intent is not reopened. Unknown SQL response or
postcommit installation failures retain both references and prevent clean drain
or replay. Installation-tail execution remains fenced by the manager suspension
slot until final acknowledgement.

Current 22 continuation tests pass, including independent copied-capability,
strict DB timestamp/ID, stale timestamp, final-read grant revocation, lost install
and no-bulk-reopen cases. The final read precedes original pre-CAS grant refresh;
no hidden persistence read runs between that grant and UPDATE. Final pause and
resume evidence is reread after policy callbacks before acknowledgement. This is
a finite cooperative boundary, not an arbitrary-callback fixed point.
Independent capacity/facade review and actual PostgreSQL acceptance remain pending.

## Actual database acceptance

Stage141 passes original-schema PostgreSQL acceptance from an actual initial
zero-timeout task through worker pause, fresh-generation engine-only resume and
completion without goals replay. Separate stale paused-row CAS and injected SQL
failure retain ownership without replay. See ISOLATED_QA.md for immutable evidence
and untested cases. Optional setup helper and JWT control integration remain a
separate checkpoint; no default, queued, timed or restart continuation claim.

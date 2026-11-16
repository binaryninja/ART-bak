# Explicit local per-intent pause and resume

Working implementation, not a default HTTP route or automatic application policy.
`IntentControl(host, authorize=...)` is explicitly installed on the original
EngineRunner. The host provides `control_intent` and `inspect_intent_control`.

Pause is supported only for an already admitted canonical worker turn on the
original running, local, cleanup-enabled engine generation. The worker's own
cancellation token receives `worker_pause`; the task token remains alive. A
bounded wait can return `pending`, which retains the reservation and prevents a
new engine advance. Inspection rechecks the original grant before and after the
wait and never sends another cancellation or model request. Confirmation requires
the exact persisted, non-stale canonical cancelled turn, completed cleanup,
paused claim/version, and original task generation. Unknown effects retain the
reservation and mark the engine unresolved rather than claim rollback.

Resume requires an idle original generation and a locked paused/version row. It
performs only the exact paused-to-open CAS. It does not resume a paused task,
launch a provider, or replace the task generation. Normal owned pumping is a
separate operation. Task mutation, expiry freeze, settlement and competing
control are excluded by the host control slot; callbacks and waits run outside
that host lock.

Source scope is the pause/resume subset of `server/task_control.go` and
`Engine.ControlWork`. Delete/cancel, message intervention, rerun/admission
rollback, inherited or archived tasks, recovery, and pre-canonical claimed-worker
cancellation are not implemented by this adapter. No default grants are inferred.

## Validation and finite authority boundary

Inert tests use actual owned host, runner, bootstrap and engine worker threads
with canonical temporary transcripts. The grant boundary checks configuration
and lifetime first, then the original control grant, then pure binding checks.
It is not a fixed point for arbitrarily mutually modifying policy callbacks.
Copied capabilities, substituted coordinator methods, revoked grants and unknown
canonical/CAS outcomes retain ownership rather than invent success.

## Read-only expiry continuation

Only an existing retained pending pause can be inspected under its original
registered observer, exact freezing/drained lease and freeze, without finalization
transfer. It verifies stopped canonical worker, completed cleanup and exact paused
row/version, then releases only that control reservation. Fresh frozen pause or
resume, assessing/terminal phases, copied ownership and unknown state remain
denied. No cancellation, model invocation, task resurrection or new generation is
created by the continuation.

## Actual PostgreSQL acceptance

Stage134 first verified genuine positive task pause/resume but exposed a stranded
pending control after deadline freeze. That failed continuation is preserved.
Stage134-fix immutable manifest
`a830c8d3302bd7d226a37f56a21daa4a63c4e28d0acf619e2823273e99dcd8ca`
passes the strengthened genuine positive-duration flows:

- Worker-only pause, canonical paused version 2, idle resume open version 3,
  then ordinary planner/worker pumping. The task token remains alive; no provider
  is called by control. Resume need not dispatch a worker on the very next tick.
- Pending pause crossing the actual persisted deadline, original observer freeze,
  canonical cleanup, exact read-only pause confirmation/control release, observer
  readiness, restricted final timeout and empty runner/manual inventory. No pump
  error, repeated model call or manufactured prepared timing is involved.

Seventeen focused tests also pass on that frozen snapshot. Direct stale-version
and injected SQL-failure cases remain unit/adapter evidence rather than exhaustive
actual PostgreSQL concurrency coverage. No HTTP composition or default route is
claimed by this core checkpoint. See ISOLATED_QA.md.

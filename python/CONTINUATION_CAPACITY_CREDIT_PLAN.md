# Exact capacity-one continuation replacement credit

Design baseline: controller occupancy was `running + pending`. A clean paused continuation
retains its original cancelled runner handle, so it occupies one pending slot.
Before this change, limit-one JWT resume refused before SQL. The earlier
capacity-two demonstration did not fix that product limitation.

The implementation and acceptance evidence now satisfy the bounded replacement
path described below; see TASK_CONTINUATION_SETUP_STATUS.md and ISOLATED_QA.md.

## Narrow admission operation

Modify only original `AdmissionController.admission_guard('resume', task_id)` to
consider a privately reserved replacement credit. Other operations, different
tasks, unknown ownership and ordinary cancelled handles retain current counting.
Do not subtract continuation counts or all paused handles from host occupancy.

A new code-owned manager reservation should capture the original clean suspension
capability, cancelled old token/handle, task/config/bootstrap identity, paused
stamp, exact runner entry, owner and current admission-controller claim. Require
zero timeout, running persisted lifecycle represented by an unchanged local
paused/unqueued row, no execution run, no deadline/unstamped/cleanup/control or
quarantine ownership. Original drained worker/process proof and callbacks must be
checked outside the manager lock. A copied tuple or caller task DTO is not credit.

Implemented internal API:
`reserve_continuation_admission(controller, task_id) -> private Credit | None`,
`validate_continuation_admission(credit) -> bool`,
`release_continuation_admission(credit)`.
Credit is tied to the actual controller's active claim and current thread; it is
single-use and cannot change the suspension or invoke resume by itself. While
reserved, competing direct resume or task mutation cannot steal the credited
original generation. The existing resume path consumes only that original
same-thread reservation when creating the new run; no external permission is
inferred from a capacity observation.

The controller first reserves its ordinary global admission mutex, then polls
original occupancy. Reserve/validate the exact replacement candidate and verify
that the same old handle contributes exactly one pending (not running) entry.
The capacity check becomes `running + pending - 1 < limit` only for that private
candidate. Refresh original proof immediately before yielding the existing
admission context. All callbacks and waits stay outside controller/manager locks.
The original continuation grant, DB verification and exact paused CAS remain in
the resume implementation; credit never replaces them.

After success, poll/reconcile using normal new-generation occupancy. On refusal
before a new resume attempt, release only the transient credit; the clean paused
suspension remains available. On partial/unknown continuation effects, retain the
existing original/new owners and quarantine admission as today; never issue a
second credit to make a failed attempt look retryable. Release the transient
claim in finally without masking the original failure.

## Acceptance and exclusions

Use the actual JWT route at capacity1 with exactly one original zero-timeout
paused task: resume succeeds, new token/handle replaces old one, provider count
unchanged until explicit tick, goals never rerun, occupancy stays one. With a
second genuine execution occupying the limit, deny. Wrong task, copied/stale
credit, paused SQL/config drift, queued/timed/terminal/recovery/uncertain slots,
active old worker, replaced runner entry, concurrent credit/resume, and callback
revocation must deny without widening capacity. Exercise post-CAS/install failure
and verify retained ownership/blocked retry. Repeat on genuine PostgreSQL with
zero from first INSERT, no synthetic positive pins.

No generic cancellation exception, queue promotion bypass, shutdown release,
clock reset, terminal resurrection or constructor-default change belongs here.

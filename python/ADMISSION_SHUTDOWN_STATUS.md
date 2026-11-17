# Permanent original-host admission seal

An operator-owned runtime cannot honestly supply
`RuntimeResourceOwner.stop_admissions=lambda: True`: the previous capacity
controller had no permanent close operation, and its `drain` only rejected
admissions temporarily. This change provides an explicit irreversible seal.
No default application, listener or startup path invokes it automatically.

## Supported wiring and result

Use the original application's `bundle.admission.stop_admissions` as its
`RuntimeResourceOwner.stop_admissions` callback. The controller seals its own
reservations and the original `AgentHost`; it does not dispatch a replaced host
instance callback. Repeated observations are safe, but there is no unseal API.

`True` means the seals are installed, no controller reservation was active at its
observation and no admitted host call remained at the host observation. It does
**not** mean tasks were cancelled, runner handles retired, HTTP requests completed,
database effects rolled back, or resources safe to close. Existing listener
cancellation, drain, resource close and pool drain remain necessary.

`False` also installs the permanent seals. A controller reservation, reconciliation
or pre-seal host admission is still active; it may finish with effects. The caller
must retain resources, let that original call finish and explicitly observe again.
There is no automatic execution retry, cancellation, timeout or success inference.
Exceptions preserve any seal already installed and do not authorize cleanup.

## Linearization and covered paths

Controller reservation and sealing share its state mutex. After sealing, no new
`create`, `start`, task `resume` or queued admission reservation can enter. A seal
observed during pre-admission inventory prevents the guard from yielding. An
already yielded guard retains its original claim until exit, never silently loses
its continuation credit or in-flight ownership.

Host admission entry and the host seal share the host condition mutex. The host
counts its admission calls until their callback tails unwind, including exceptions.
This covers create/start/queued/task resume, suspended-task resume, continuation
installation, and the separate intent-resume path. A reserved controller operation
that has not yet entered the host is rejected after the host seal. A host call
that already entered is counted and can finish; sealing it returns `False`.

Controller and host mutexes are never held together. Sealing calls no user policy,
runner claim, provider, database or wait callback. The host uses callback-free
original manager/runner binding checks: its broader `_identity` helper invokes a
runner claim operation and can wait behind a blocked factory holding the runner
mutex, so that helper is deliberately not used for the seal. The initial
host-inclusive candidate exposed this lock wait in the delayed-factory test and
was corrected before acceptance.

Pause, intent pause/inspection, existing owned advancement, poll, reconciliation
and drain remain available. Reconciliation may clear quarantine but never clears
either seal. Normal drain still has its temporary barrier, whose exit cannot
reopen the permanent barrier. Sealing does not imply all activity stopped.

Direct calls to the underlying TaskManager or runner remain unsupported admission
bypasses, not newly authorized paths. Other runtimes/processes, independently
registered/manual components and arbitrary code mutating private fields are not
fenced by this API. A host with such admission channels must separately stop them;
there is no database/distributed fencing, restart ownership or general race proof.

## Verification

18 new tests cover all four controller operations before effects, all original
host admission methods, permanent state after poll/drain/reconciliation/failure,
in-flight and reentrant factories, pre-reserved-but-not-entered host calls,
intent-resume accounting and failure cleanup, retained pause/inspection, changed
ownership, replaced callback refusal, and actual listener owner composition.
The listener keeps resources on unresolved admission and closes only after an
explicit successful retry plus its ordinary cancellation/drain path.

The actual engine/manager/listener components use inert SQL and provider fixtures;
no PostgreSQL server, target or live provider is contacted. A focused 74-test run,
the full 4,180-test Python suite and 25 Node tests passed on the exact candidate.
Independent read-only review found no remaining blocker within this scope, ran
76 and 75 focused test executions in separate runs, and added six event-controlled
probes covering pre-identity host entry, pre-reserved inventory, reentrant factory
sealing, host/intent BaseException unwind and actual listener shutdown during a
blocked factory. The listener promptly retained resources with
`admission_unresolved`, then drained on an explicit retry after release. Source
bytes were unchanged through final review; the full-suite result is not a live
database/provider or deployment claim. This is a host
composition prerequisite, not an accepted executable demo, production launcher,
durable recovery implementation, deployment or merge.

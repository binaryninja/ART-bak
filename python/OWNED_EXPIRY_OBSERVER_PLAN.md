# Owned expiry observer: design and remaining final-action work

The manual tick portion is implemented as described in
OWNED_EXPIRY_OBSERVER_STATUS.md. Finalization and tracked background execution
below remain proposals. Positive runtime admission remains rejected; this
document does not install an observer, timer or final execution.

## Exact ownership and public API

Proposed new `agent/expiry_observer.py`:

```
OwnedExpiryObserver(host, generation_clock, *, authorize_observation,
                    authorize_freeze)
observer.tick() -> ExpiryObservation
observer.finalize_once(final_plan) -> SettlementReceipt
```

Require actual AgentHost, OwnedGenerationClock, the clock's original
TaskDeadlineStore, original manager/token/BootstrapHandle/executor/shared guard,
and original prepared receipt. Construction is inert and never adopts an ambient
manager task, looks up a replacement handle, stamps a clock or reads SQL. Bind a
single observer to that exact owner. Keep the exact installed lease once the
kernel has produced it; a copied lease or a later generation is not eligible.
Public observations contain phase and booleans, not owner tokens or transcript
contents. Policy callbacks and resources are pinned and checked after callbacks.

`tick()` is nonblocking with respect to another tick/finalization. It performs a
bounded read plus `host.freeze_deadline(..., timeout=0)` only. It never enters
`host.advance_owned`, so an independent caller can freeze a blocked synchronous
pump. It does not start providers, wait for a model, or construct final resources.
A call may still block on an explicitly configured SQL timeout or host policy;
there is no claim that arbitrary Python callbacks are forcibly time-bounded.

## Required narrow existing-component changes

1. Add a public immutable, lock-protected kernel observation of its original
   confirmed clock/lease and conservative monotonic ceiling. No `read_or_stamp`
   on the observer path. Unstamped returns `unstamped`, including paused-before-
   first-call. A busy stamp returns `busy`; never infer a partially installed
   receipt. No foreign clock epochs or timeout recomputation.
2. Add an exact full-owner manager validation for observer reads. It checks
   manager/token/handle/lease/original resources and optional exact freeze receipt
   in attached/suspended/freezing/drained states. It grants ONLY a clock read,
   not model, terminal, new-turn or generic lease write authority. Keep ordinary
   `validate_deadline_owner` semantics unchanged.
3. Add `TaskDeadlineStore.observe_expiry(...)` using that full owner validation
   and the original read grant/shared guard. Read exact task clock/config,
   prepared bootstrap identity and `deadline_at <= clock_timestamp()` together.
   Return a typed immutable observation, with final original-grant/resource
   checks after transaction AND guard exit. No stamp, update or row adoption.
   Preserve matching paused state, deletion/archive/terminal refusal and bounded
   rows. Frozen reads must validate the freeze capability, not broaden the old
   lease-only API.

The existing `validate_lease` only accepts attached/suspended. Reusing it after
freeze is incorrect. Conversely, calling journal.reserve to discover expiry is
also incorrect: assessment ownership has already changed by then and a normal
clock skew would become a stuck partial finalization.

## Manual tick state machine

- **unstamped/busy:** return without SQL or new reservations. Never stamp goals,
  readiness, pause, observer startup or receipt replay.
- **observing:** read exact persisted clock; sample the shared monotonic callback
  with existing finite/nondecreasing checks. Freeze when either the nonexpanding
  local ceiling elapsed or the database reports expiry. A suspended lease uses
  the same original absolute deadline; pause never resets time.
- **freezing:** reserve the exact existing manager freeze through AgentHost,
  immediately fencing new claims/dispatch. Do not cancel the manager token and
  do not mislabel timeout as task_pause or planner_kill. Existing context budget
  and cleanup paths finish already-owned work. Repeat only zero-wait drain
  checks; never rerun execution. Blocking work remains retained.
- **drained_waiting_database:** if local monotonic time expired conservatively
  before the database clock, retain clean freeze and wait. This is NOT unknown
  effects and must not quarantine or reserve a final journal. Continue exact
  owner/clock reads. No reopening execution or extending budget.
- **ready_for_assessment:** both clean drain and fresh database expiry confirmed.
  Return readiness only. A previous readiness observation is not final authority.
- **unresolved:** actual identity/config drift, unknown effects, or failed drain
  evidence retains ownership; no automatic resume, adoption or retry. Transient
  busy/normal not-yet-expired are separate states, not uncertainty.

Paused/suspended owners may be frozen using their retained exact original handle,
even after execution inventory removal. Source absolute time continues. Initial
implementation still rejects resume/lease transfer and process-restart recovery.
A paused task with no installed lease is not synthesized into timed work.

## Explicit finalization, separate from the observer tick

`FinalPlan` must be a code-owned immutable configuration of exact canonical
transcript, executor, original guard, FinalConfiguration, explicit provider
resolver and model/read/persistence/journal/settlement grants. Factories may
construct only the actual FinalAssessmentJournal, FinalAssessmentRunner and
FinalSettlementStore, with returned identities checked before use. Prefer direct
construction from these declared resources over an arbitrary execution callback.
No normal planner, add_intent, worker or command tools enter this path.

`finalize_once` reserves its own in-flight slot before doing anything, rechecks
clean drain and database expiry, then allocates exact manager assessment,
reserves/starts the actual journal via restricted runner, and settles only its
canonical finalized receipt with fresh local graph truth. Any attempted stage is
one-shot; a pending or uncertain result is inspectable, never rerun. A failure
before allocation due only to DB-not-expired returns wait. Existing source task
terminal status is never overwritten. Read-only final model text is not proof.

Finalization may block cooperatively despite a finite budget. It must run on a
separately owned invocation, not in the monitoring tick or pump. Later background
support must track/join it, refuse resource shutdown while active, and retain
journal ownership on uncertainty. The first implementation should expose manual
explicit finalize only; background threads require a separate reviewed lifecycle.

## Exclusive implementation split and acceptance

- Architecture: `agent/expiry_observer.py`, dedicated tests; narrow exact-owner
  manager read API and public kernel observation in separate reviewed patches.
- Persistence owner: `db/task_deadline.py` exact observer read and SQL tests; no
  positive gate changes or existing read-to-write escalation.
- Lifecycle reviewer: actual host/pump blocked-provider tests, same original
  handles, retained suspended inventory and listener shutdown implications.
- Independent reviewer: authority/callback/clock boundaries and one-shot final
  receipt flow. QA: real disposable PG plus inert controlled provider, canonical
  temp transcripts, explicitly labeled synthetic prepared-positive fixtures.

Required acceptance: no stamp before dispatch; blocked pump can be frozen by an
independent tick; local expiry before DB expiry waits without false quarantine;
DB expiry despite a backwards wall clock freezes; pause keeps original deadline;
unclean provider never triggers final model; canonical cleanup drains before
finalization; copied/stale handle/lease/freeze and postcallback revocation deny;
final call is not repeated after uncertainty; done requires local confirmed proof
and timeout is distinct from stopped. Existing zero-timeout behavior is unchanged.

Only after this complete connected path is reviewed may a separate opt-in assembly
change positive admission, immutable bootstrap timeout and engine SQL gates.

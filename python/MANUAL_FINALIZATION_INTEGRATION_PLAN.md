> Design record: the narrow manual path below is implemented and verified as
> described in OWNED_FINALIZATION_STATUS.md. Public positive timed admission and
> automatic execution remain disabled.

# Manual observer-to-final settlement integration (proposal only)

No source changes, positive entry, background action or terminal operation are
introduced by this plan. A `ready` observation is not execution permission.

## Minimal new module and API

New `agent/owned_finalization.py`:

```
FinalizationPlan(configuration, transcript, *, start_authorize,
    journal_authorize, provider_resolver, model_authorize, read_authorize,
    persist_authorize, settlement_authorize, clock)
OwnedFinalization(observer, plan)    # inert, exact code-owned types only
coordinator.run_once() -> FinalizationResult
coordinator.cancel() -> bool
```

The immutable plan requires actual FinalConfiguration with the restricted catalog
digest and finite limits, canonical ChatTranscriptAdapter/TranscriptStore, explicit
independent grants and provider resolver, plus a pinned final monotonic clock.
Capture configuration digest and transcript store/root/agent/recovery guard/bounds.
No arbitrary factory can substitute an ordinary planner or unrestricted tools.
The coordinator directly constructs the existing FinalAssessmentJournal,
FinalAssessmentRunner and FinalSettlementStore with the original owner resources.
The final budget is independent from the expired task budget.

## Necessary small observer handoff, not lease elevation

Add an exact one-shot handoff method on OwnedExpiryObserver, only callable by the
actual bound coordinator: `claim_finalization(coordinator, plan)`. It reserves a
nonreentrant observer phase before callbacks, pins original resources, refreshes
original observation/freeze grants and explicit start grant, checks clean drain
again, and reads exact database expiry with original token/handle/lease/freeze.
It does not trust a previous `ExpiryObservation` or accept caller-created leases.

If DB clock is now not expired, return typed `waiting_database` without assessment
allocation, journal writes or model calls. If work is not cleanly drained, return
`draining`. These normal waits are not uncertainty; a later explicit run attempt
is permitted only before any assessment reservation. No calls are made through
pump `advance_owned`, and no callback runs under a manager lock.

Once eligible, invoke existing `manager.reserve_deadline_assessment` with the exact
freeze/token/handle/lease and final configuration digest. Retain an opaque identity-
bound handoff containing that exact assessment and original resources. Bind it to
one coordinator; copies, competing coordinators and replacement plans are denied.
After transition, observer.tick must report transferred ownership without calling
its old attached/frozen-state check, which correctly rejects `assessing`. It must
not quarantine a valid finalization merely because observation is no longer the
owner of that phase. No automatic transfer back, retry or new generation exists.

This observer change is required: currently `_check()` admits only observing or
frozen states. Simply calling reserve_deadline_assessment from an external wrapper
then continuing tick would falsely quarantine the final owner.

## Actual connected call order and retained state

1. Constructor validates pure pins only, no SQL, callbacks or models.
2. Explicit `run_once` atomically reserves a coordinator invocation slot. A busy
   invocation cannot start another runner. Obtain the refreshed exact handoff.
3. Construct actual FinalAssessmentJournal with the original executor, manager,
   assessment, freeze, handle/token, lease, original guard, canonical transcript
   and explicit journal grant. Schema must have been explicitly installed already;
   never install/migrate automatically or treat missing schema as provider approval.
4. Construct actual restricted FinalAssessmentRunner and call its one-shot run.
   This reserves/starts the durable journal, performs canonical scoped ChatAgent
   work with only bounded list_goals, revokes callbacks immediately after the
   turn, and records canonical finalized evidence. A pending existing attempt is
   not adopted. Keep the runner reference before its first action for cancellation.
5. Require the actual finalized receipt returned by that runner. Construct actual
   FinalSettlementStore and call settle once. Its existing independent authority,
   fresh locked local goal/proof truth and persisted receipt rechecks decide done
   versus timeout. Model completion/text cannot decide status. Inherited/goalless/
   unsupported graph cases remain rejected, not silently converted to timeout.
6. Return only the confirmed SettlementReceipt. Its store alone consumes private
   postcommit evidence and retires manager ownership; the coordinator must not
   call manager.complete, cancel the manager token or remove inventory itself.

Retain assessment/journal/runner/outcome/store/result references as each is
created. Any exception or BaseException after allocation is sticky unresolved,
with original freeze quarantined if still owned; no inference or settlement retry.
SQL may already have committed terminal status despite local uncertainty. Never
claim rollback or release database/provider resources on that basis. Success
already confirmed by settlement must not be undone by a generic finally callback.
Post-success response uses a data-only snapshot, with no additional policy hook
that could create an unclassified late failure.

Cancellation targets only the actual restricted runner's cooperative cancel(),
not the manager token. Cancellation before runner creation latches locally and
prevents model start; after assessment allocation it retains/quarantines rather
than resetting the handoff. A blocking provider cannot be forcibly stopped: the
coordinator remains active, and shutdown must retain its resources. No background
thread lifecycle is implemented in this slice.

## Ownership split and acceptance

- Architecture: new owned_finalization module/tests and narrow observer handoff.
- Independent reviewer: exact one-shot transfer, callback/phase/pin fences,
  cancellation gaps and postcommit uncertainty; no implementation overlap.
- QA: explicit fresh journal/settlement schema, actual disposable PostgreSQL,
  canonical temporary transcript, fixed inert provider. No external targets.

Test expired+drained observer through actual restricted final transcript to local
confirmed-proof done and unproven-goal timeout; DB clock returning to unexpired
before allocation must wait without provider; blocked cleanup cannot finalize;
missing schema/provider grant must not run model; conflicting/copy handoffs and
concurrent calls denied; cancellation before/during final provider; changed
bootstrap/config/canonical evidence; settlement postcommit failure retains owner;
repeated invocation after any allocation never repeats provider/tool/usage.
Positive prepared pins in fixtures remain synthetic until separate admission work.

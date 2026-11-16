# Explicit manual finalization (working implementation)

`agent/owned_finalization.py` connects the actual manual observer, restricted
FinalAssessmentRunner and FinalSettlementStore. Constructors are inert. Only
explicit `run_once()` can request a fresh exact full-owner handoff; observer tick
never runs a model. Positive runtime admission remains disabled.

The handoff rechecks clean drain, original read/freeze grants, independent start
approval, exact prepared generation and fresh DB expiry. Normal pre-allocation
waiting/draining is repeatable. After assessment allocation, the exact coordinator
owns one attempt; copied handoffs, competing invocation and retries are denied.
Observer tick reports `transferred` after handoff instead of treating legitimate
assessing ownership as a stale observing lease.

The immutable plan explicitly supplies restricted configuration, canonical
transcript and independent journal/provider/model/read/persistence/settlement
policies. Resource/policy identity is checked around callbacks. The runner permits
only its reviewed local goal-read tool. Settlement, not model text, chooses done
versus timeout using fresh supported local graph/proof truth and exact receipts.
Unknown/inherited/goalless cases remain fail-closed. No schema is installed here.

Cancel latches before model creation or cooperatively cancels the actual active
restricted runner. It does not cancel the manager token or authorize settlement.
After allocation, error/cancellation retains or quarantines ownership with no
inference replay. SQL may already have committed a terminal status even if local
confirmation fails; no rollback or resource-release claim is made. Provider
blocking remains cooperative and requires retaining the original resources.

Six owner tests, five independent handoff tests and six independent cancellation
tests pass using actual registry/observer/journal/canonical transcript and
settlement components with inert provider and synthetic SQL. The reproduced final
observation-callback start-grant revocation defect was fixed by refreshing start
authority immediately before allocation, followed by pure binding/owner checks. Their positive
prepared timeout pins are explicitly synthetic, not production admission.
Independent ownership/cancellation review cleared27 combined tests. Exact-source
PG97 passed five integration groups: real proof-based done/open-goal timeout,
preallocation wait/revoked start, missing-schema retention, real threaded blocked
provider cancellation and postcommit acknowledgement uncertainty without replay.
The frozen suite passes2827 collected tests and456 SQL syntax checks.
See ISOLATED_QA.md for manifest, harness correction and precise fixture limits.
No background finalization, automatic expiry loop, recovery or resume transfer is
implemented by this slice.

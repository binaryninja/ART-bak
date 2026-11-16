# Deadline freeze/drain: implemented prerequisite, not timed-task support

## Implemented and independently reviewed

`AgentHost.freeze_deadline(task_id, token, handle, lease, timeout=...)` uses the
actual manager registry and exact original owner tuple. It returns an opaque
`DeadlineFreeze` receipt plus a clean-drained boolean. The manager reserves a
`freezing` state before waiting. Existing BootstrapHandle/engine current checks
consult `owns_generation`, so that state immediately denies new execution.
Cleanup identity remains distinct from execution permission.

Waiting is bounded and outside registry/host locks. A false wait retains the
same freezing reservation for explicit later checking; it does not rerun work.
Only a true wait with a non-unresolved actual handle reaches `drained`. Unknown
wait results/errors or unresolved work quarantine the owner. Both freezing and
drained owners remain inventoried and prevent claims of complete shutdown.
No manager token cancellation or database status change occurs in this API.

This is not evidence that a deadline expired. The caller is the explicit trusted
host, not a model tool or a lease reader. A copied lease/freeze receipt cannot
substitute for the registry's exact objects. Ordinary complete/retirement/resume
remain denied for these retained owners. Drained does not mean goals achieved.

## Important acceptance limitation

Protocol tests use actual authored manager, host, BootstrapHandle and clock-store
components, but explicitly inject a positive prepared timeout into synthetic
fixtures. Production constructors still reject positive task timeouts. These
fixtures do NOT demonstrate positive-duration task execution or permission to
bypass the original prepared-timeout pin. No expiry watcher, automatic freeze,
final model round, terminal write, recovery/takeover, or resume transfer is enabled.
The existing runtime and SQL timeout rejection gates remain unchanged.

## Next coherent capability: owned final assessment

Source `server/engine_timeout.go:runFinalPlannerRound` performs one final planner
round after freezing/draining, with a context independent of execution pause,
then uses a guarded terminal update. Existing Python PlannerCycle cannot be
reused as-is: it always exposes `add_intent`, checks ordinary execution authority,
and uses the ordinary planner receipt namespace.

The minimum connected next stage needs all of the following, not a generic
`settle(lease, status)` function:

1. **Manager assessment reservation.** A host-only API accepts the exact retained
   drained freeze receipt, original handle/token/lease, and an immutable final
   configuration. It grants a separate bounded assessment capability, never
   restores ordinary `owns_generation`. Only one assessment reservation per
   freeze; pending or quarantined attempts cannot be replaced or automatically
   retried. No callbacks/SQL under the registry lock.
2. **Durable final-assessment journal.** An explicit code-owned installer/API
   (not automatic schema changes) records task/exploration/run, exact bootstrap
   receipt revision/request/config, immutable stamped clock, and final
   provider/prompt/tool-policy/limits digest. A DB-generated attempt ID and
   owner-bound CAS distinguish pending/running/finalized/unresolved states.
   Archive/delete/config changes reject it. No automatic pending takeover.
3. **Actual restricted assessment runner.** Use canonical ChatTranscriptAdapter
   and ChatAgent with an independently finite positive budget, not the expired
   task budget or zero-as-unlimited. Bind transcript scope to the durable final
   attempt and full configuration. Only explicitly declared local graph reads
   and evidence-linked `prove_goal` are eligible. No add_intent, worker launch,
   scope changes, commands, network, or ordinary task chat. Explicit provider,
   evidence, asset and final-operation grants still apply. Pause must not be
   silently converted into permission for this separate model round.
4. **Verified final receipt.** After canonical transcript commit, the journal
   records exact turn ID/result digest with CAS. A returned model sentence is
   not a receipt and does not establish goal truth. Refusal/unavailability and
   interrupted/uncertain effects have distinct outcomes; unknown effects retain
   ownership rather than being called a successful timeout.
5. **Terminal CAS under the same lifetime barrier.** Consume the exact manager
   assessment reservation plus verified journal/transcript receipt. Recheck
   original task/journal/clock configuration, no outstanding owned writes, and
   fresh persisted goal/evidence truth. Preserve already terminal status. Only
   after the durable task update and final checks may the manager update its
   registry and retire the retained owner. Postcommit uncertainty quarantines;
   reconciliation may inspect the same receipt, never rerun inference.

The final assessment needs its own durable journal because the existing bootstrap
receipt certifies initialization only. Reusing its `prepared` phase as evidence
of final assessment, or treating a read-only deadline lease as write permission,
would conflate independent authorities.

## Still required before positive-timeout enablement

Owned first-real-call stamping and monotonic context clamping; expiry observation
while a synchronous call blocks; bounded cooperative drain with honest unresolved
handling; separate final assessment above; exact terminal persistence; paused
clock ownership; explicit shutdown and no-recovery behavior. Remove existing
positive-timeout gates only after connected independent and disposable-PostgreSQL
acceptance, including expiry during provider/stream/tool callbacks and pause.

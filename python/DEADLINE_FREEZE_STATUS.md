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

## Owned final assessment: journal implemented, execution still pending

The exact manager assessment reservation and separate durable journal are now
implemented. See [FINAL_ASSESSMENT_STATUS.md](FINAL_ASSESSMENT_STATUS.md) for the
actual fresh-only installer, immutable identity and phase contracts, reviewed
post-guard/evidence fences, test limits, and proposed restricted runner.

The journal records canonical transcript evidence; it does not run a final model
round or settle a task. SQL phases are exactly reserved/running/finalized;
uncertainty remains a retained quarantine, never automatic takeover or replay.
Bootstrap prepared state and a read-only deadline lease are not final execution
or terminal-write authority. JSONL and SQL remain non-atomic.

The next connected stage is a separately authorized finite final ChatAgent turn
without add_intent or worker launch, followed later by fresh graph truth and an
exact terminal CAS. Existing PlannerCycle cannot be reused unchanged because it
exposes ordinary planner tools and relies on execution authority now frozen.

## Still required before positive-timeout enablement

Owned first-real-call stamping and monotonic context clamping; expiry observation
while a synchronous call blocks; bounded cooperative drain with honest unresolved
handling; separate final assessment above; exact terminal persistence; paused
clock ownership; explicit shutdown and no-recovery behavior. Remove existing
positive-timeout gates only after connected independent and disposable-PostgreSQL
acceptance, including expiry during provider/stream/tool callbacks and pause.

## Journal prerequisite implemented after freeze review

The manager now offers `reserve_deadline_assessment` for the exact clean-drained
owner tuple and final configuration digest. The retained `assessing` state still
blocks ordinary execution/retirement/completion. It is journal identity, not a
provider or tool grant.

`db/final_assessment.py` adds an explicit fresh-only installer and a separate
journal. `reserve`, `start`, and `record_finalized` use expired task-clock/full
bootstrap identity checks and owner/revision CAS. Finalization verifies a
canonical scoped transcript turn; it records its digest regardless of whether
the terminal model outcome was successful, refused or cancelled. That distinction
must be examined by the eventual runner/settlement consumer, not guessed here.
`reconcile` reads the same owner's record only and never clears quarantine or
restarts work. Possible commit uncertainty retains manager ownership.

This does not implement the restricted final runner, fresh-goal assessment,
terminal task CAS, expiry watcher or positive-timeout runtime. Protocol tests
continue to use explicitly synthetic clock fixtures and hand-authored canonical
transcript records, not evidence that an autonomous final model round ran.

# Irreversible continuation resume sealing during shutdown

This slice implements the first shutdown protocol from
`TASK_CONTINUATION_COMPOSITION_PLAN.md`: irreversible resume admission sealing and
outer resume-invocation tracking, integrated with the owned runtime listener.
It does **not** relinquish a clean paused suspension or make paused shutdown
succeed. Such process-local capabilities still prevent host drain and resource
close until a separately verified relinquishment protocol exists.

`TaskContinuationOwner.seal_resume()` permanently seals new resume attempts.
`wait_resumes(timeout)` requires that seal and waits only for issued outer
invocations to unwind; it does not claim task/suspension ownership is settled.
A wait from its own active invocation returns false rather than self-deadlocking.
No token is reset, no paused database row is cleared and no existing owner is
forgotten. Pause/cancel and ownership validation remain possible after sealing.

Manager resume admission issues an exact attempt token under the owner lock
before registering a new run or consuming replacement credit. Already sealed
requests therefore leave clean paused state intact rather than quarantining it.
Capacity-credit issue is serialized with sealing; existing credits become invalid
and still release through their original guard. A binding-integrity failure
continues to quarantine as before, rather than treating tampering as ordinary
sealed refusal. Attempt tokens live through the manager's outer finally and are
released only by the issuing thread; they never release generation ownership.

Resume policy fences observe sealing before and after callbacks. The runner's
final handle swap is serialized under runner/owner locks with sealing, with no
SQL or policy callbacks under these locks. A seal during CAS/installation can
leave committed or uncertain storage outcomes: existing quarantine retains both
generations, blocks retry and prevents clean shutdown. Already installed work is
not implicitly rolled back. Resumed activation has a final owner-lock seal check after its SQL/identity
checks and before consuming the retained generation. Activation admitted before
that seal linearization still requires ordinary host cancellation/drain.

The listener pins the exact installed continuation owner and seal/wait methods
at construction. Installing or swapping an owner afterward refuses ownership
validation. Shutdown seals it before manual-action/admission callbacks, then uses
existing HTTP/pump/cancellation/host drain. It additionally waits for outer resume
invocations before resource close. Unresolved continuation ownership still returns
`runtime_unresolved` and retains pool/resources; seal is not reset on failed stop.
The host must install continuation support before constructing its listener.

Eleven new tests and all 55 continuation-focused plus 30 listener-focused tests
pass. Cases cover idempotent seal, no new run/paused-state mutation after seal,
blocked in-flight SQL and explicit unwind, post-CAS ambiguity retention, capacity
credit issue/refusal and invalidation, timeout validation, listener sealing and
late owner-install rejection, activation SQL sealing and pre-claim snapshot-copy
failure without an invocation leak. Final consumption also rechecks record
identity, state and failure under the owner lock so a late quarantine cannot be
overwritten as consumed; a deterministic final-observation regression covers it. The endpoint test permits its normal JWT/settings
read but no resume update after sealing. Fixtures use actual runtime classes,
synthetic SQL/provider adapters and mocked listener sockets. Independent re-review cleared the final seal/activation and quarantine fixes,
including the original final-observation reproduction and snapshot-copy fault.

Corrected PG+HTTP170 passed four genuine lifecycle scenarios, all 55 continuation
tests and all 30 listener tests:
1. Clean paused stop sealed resume before external callbacks and returned expected
   `runtime_unresolved`, retaining the old generation/pool. Later resume changed
   no persisted pause/timestamp, manager slot/run or provider state and issued no credit.
2. HTTP resume blocked on a real PostgreSQL row lock. Stop sealed and returned
   `http_unresolved`, retaining the outer invocation/pool. Unlock caused quarantine
   without resume CAS; invocation drain succeeded but task ownership still blocked close.
3. Genuine resumed activation blocked on a PostgreSQL row lock. Sealing before
   unlock prevented consumption/provider replay and retained the new handle.
4. Controlled fault injection quarantined a genuine installed capability at the
   final manager ownership observation. Activation refused and both generations
   remained retained. This injected callback case is not a PostgreSQL-lock race.

Report: `/workspace/shared/artex-qa/continuation-listener-seal-170/REPORT.md`.
Manifest SHA256: `4e5055295c7c2e96f640e2169f82f6a127085129d78c95fea1902c806ff48203`.
Owner SHA256: `c4ef975d4c77ec0fd428cb576d6013c690b7ba898d57f80b699381b06fb28b5b`.
Runner SHA256: `1cb2f5e6ac315db2bd25980d0b9d7f4cdfc896927d57c5dc00f9142b83ad5e18`.
Manager SHA256: `bf68ec469ae3e6c9c0f423b57f2afe62319570bf49a0108c2c1b785dd9003ab0`.
Listener SHA256: `54109cbcc8bf97ad3a7697cc72a6111b99759e987a8a50d13ab7397b326a47c5`.
Manifest entries and source/test hashes matched after testing. Frozen index plus
explicit overlays is not exact staged-tree acceptance. Earlier 168/169 evidence
remains preserved as nonfinal baselines.

Each scenario used fresh PostgreSQL 17, original pool/runtime/listener, actual
JWT initialization and authenticated task creation/pause/resume, real transcripts
and synthetic local provider responses. Genuine goals/planner/worker paths made
persisted receipts without forged generation fields or slot deletion. All tasks
had zero timeout with NULL clocks. Isolated UID1000 namespaces had loopback only,
read-only snapshot/setup and no external route, host workspace/home or real keys.
At unresolved outcomes retained resources were not closed or ownership cleared;
experiment teardown is not evidence of production clean shutdown. No full suite,
positive-timeout continuation, recovery or exhaustive concurrency claim follows.

This does not implement recovery, automatic retry, paused-capability consumption,
clean paused shutdown, timed resume, or new external execution. Lock ordering is
manager→owner or runner→owner for callback-free reservations/install; policy/SQL
work stays outside those shared locks. Blocking external callbacks cannot be
preempted by a timeout. Full lifecycle parity and production readiness are not
claimed. No staging/publication.

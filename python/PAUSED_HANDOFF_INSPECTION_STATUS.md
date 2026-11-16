# Read-only paused handoff prerequisite observations

`PausedHandoffInspector` observes whether a retained, exact original paused
continuation currently satisfies a narrow set of prerequisites for designing a
future handoff. It never relinquishes, consumes, adopts, resumes, cancels, seals
or changes a task, receipt, owner, runner handle or journal. No HTTP route or
listener behavior changes are included.

The result is an ephemeral diagnostic, not a capability. Its states are
`observed_prerequisites_complete`, `ineligible` and `unavailable`.
`recoverable` and `release_authorized` are always false. Even the complete state
explicitly lists `durable_handoff_protocol_absent`. No current runtime consumes
this DTO as authorization. A reconstructed SQL row, copied or foreign suspension,
unsealed owner or missing process-local evidence cannot produce a complete result.

Inputs require the exact installed TaskContinuationOwner, an original issued
Suspension and a separate inspection grant for the task ID. The owner's existing
policy must also explicitly allow the new `inspect_handoff` action. Denial fails
closed before inspection SQL; original-identity changes cannot substitute another
owner's bound methods. The public result contains bounded reason/evidence codes,
task/version and bootstrap run/revision only, never owner token, request ID,
configuration digest, prompts or transcript content. Unexpected errors map to a
generic unavailable observation; inspection-grant denial raises PermissionError.

Local checks require an idle host lifecycle, permanently sealed resume, no active
resume attempts, the exact clean paused manager reservation, original cancelled
and drained zero-timeout BootstrapHandle/engine, canonical empty cleanup, no
pending replacement credit/timed/terminal/cleanup/frontier ownership and original
runner retention. Process-backed engines are deliberately ineligible rather than
polling processes or claiming unsupported cleanup proof. Quarantine remains
ineligible. Nothing is cleared to make an observation succeed.

Two inspector-owned bounded SELECT-only observations in one transaction verify
exact task pause,
zero timeout/NULL clocks, no deletion/archive/relationship transition, original
prepared bootstrap generation, original paused updated_at and absence of running
intent rows. Each inspector query returns at most two rows; any missing/duplicate/changed
or malformed result refuses a complete observation. Final policy callbacks and
cooperative guard exit precede the final SELECT. Transaction exit is followed by an authority-only sweep of inspection then owner
grants, and pure binding/local checks. Both grants also gate negative diagnostics
before any local reason is disclosed. SQL evidence describes its observation
time; final authority observers must be cooperative, not mutually revoking
callbacks or arbitrary database writers. The inspector does not configure database timeouts
or guarantee bounded server execution time. Existing runtime/provider authority
validation also performs SELECTs; two observations is not a total-query-count cap.

These are observations, not row locks, durable leases, writer fencing or a
cross-resource atomic snapshot. SQL absence of running claims does not exclude a
new claim by an uncooperative writer. The host's existing lifecycle guard must
exclude competing writers, and the observation can become stale immediately.
It supplies no crash/restart ownership transfer. Creating a durable fenced journal
and explicit recovery protocol remains required before clean paused shutdown.

Validation: 16 new tests and all 71 continuation-focused tests pass. They verify
complete-but-never-authorized DTOs, no provider or lifecycle changes, retained
old owners/unresolved host drain, no SQL before denial, unsealed/active/quarantined/
busy/cleanup/process states, changed durable timestamp/running claims, sanitized
SQL failure, final grant quarantine/version changes and foreign owner/method
refusal, owner-denied negative diagnostics, post-SQL inspection revocation, final
inspection-callback owner revocation and handle-keyed final-retirement retention.
These use actual runtime classes with synthetic SQL/provider adapters
and disposable transcripts. Independent re-review cleared the corrected authorization fences and handle-keyed
retirement check. Original denial/revocation reproductions withhold diagnostics;
additional transaction-exit grant revocations and late cleanup/retirement
insertions also fail closed.

PG175 passed four integrated inspection groups and all 71 continuation tests:
1. Genuine prepared/worker-executed/paused/sealed zero-timeout lifecycle inspection
   passed PostgreSQL READ ONLY. Trace recorded exactly two inspector SELECTs plus
   52 existing runtime/provider authority SELECTs (54 total), with no writes or
   locking SELECT. Task/node/journal snapshots, handle/manager ownership and
   provider-call inventory stayed unchanged. Both authority flags stayed false.
2. Independent initial/final inspection and owner denial, owner-denied negative
   diagnostics, final inspection callback owner revocation, and inspection-grant
   revocation after the second actual SQL observation all withheld diagnostics.
3. Deadline/cleanup/handle-keyed retirement/busy-host conditions were ineligible
   before SQL. A real running intent row caused durable_state_mismatch.
4. Another PostgreSQL connection changed the task version at the final policy
   callback; reread caught it. Quarantine remained ineligible, ownership/pool
   stayed retained and shutdown did not claim success.

Report: `/workspace/shared/artex-qa/continuation-inspection-175/REPORT.md`.
Manifest SHA256: `786e5c8d23c28e2fac6f82cf6121d40c246952aee16e6609ae2006461dab9a8e`.
Inspector SHA256: `9d6f8a6d762dc5bcfe2667337f45d159398a3b5058a0e8e15fbe0b14bcdf8f24`.
All manifest entries and source/test hashes matched after testing. Frozen index
plus assigned overlays is not exact staged-tree evidence. Original runtime,
listener and pool used actual local HTTP auth/task creation/pause, PostgreSQL 17,
real transcripts and synthetic local provider responses in isolated UID1000
namespaces with loopback only, read-only sources and no real credentials or host
workspace/home. Inspector was invoked directly, not through a new HTTP endpoint.
Controlled revocation hooks/local-owner injections are not live race proofs.

Prior fixture failures remain preserved: 171 ordered journal snapshots by a
nonexistent id before inspector invocation; 172/173 used the affected-row-count
API for SET, which rejects SET's -1 rowcount; 174 applied read-only mode correctly
but wrongly expected only two total queries. PG175 distinguishes inspector versus
authority SELECTs. These did not require product-source changes.

This remains ephemeral read-only observation, with no release, adoption,
recoverability, durable fencing or crash-safety authority. Disposal of the test
process/database is not clean release of retained production ownership. No
full-suite or production-readiness claim follows.
No staging or publication.

# Explicit single-session timed admission

Stage130 passes the genuine positive-duration PostgreSQL pipeline described below.
This is a narrow opt-in capability, not production readiness or full Go parity.
Default absence still rejects positive timed admission.

## Supported profile

`TimedAdmissionRequest` selects an expected database and manual-observer mode.
Setup requires the original runtime/timing, a fixed supplied final plan using the
exact persisted planner adapter, the original shared monotonic clock, tracked
manual lifecycle, and an exact exclusively owned PostgresExecutor with original
connection/cursor factory pins. Generic pools, pool-only listeners, automatic
observers, recovery, inherited/seed/intercept state, process/activity providers,
legacy resolver callbacks and noncanonical tool catalogs remain unsupported.
Explicit native record_fact/list_facts/list_goals/prove_goal declarations use
recursively type-exact schemas and enabled role bindings; they are not installed
automatically. Planner add_intent is separately built in.

Preparation invokes original code-owned source and extension validators. A caller
cannot substitute a boolean, observation or validator callback. Exact database,
public schema, PG17 profile, pristine/composed source and extension manifests and
descriptors enter configuration identity before issuance. Admission repeats the
same-session observations and requires the original contract to remain unchanged.
This is a bounded schema observation, not protection against arbitrary concurrent
administrator changes. The effective FK checks are documented separately.

An original issuance record and exact runtime/method/resource pins are required;
a copied object or overridden instance validator is not authority. One-time binding
precedes any configuration selection. Positive provisional and persisted options
must share the original full bootstrap digest, timeout and fixed planner model.
No zero-timeout receipt is mutated into a positive one. The integrity contract is
in-process, not a sandbox against arbitrary Python execution.

Admission-open checks follow callback-bearing configuration/readiness work.
Cleanup structural checks deliberately remain usable after sealing. No lock is
held across external callbacks and no atomic multi-policy fixed point is claimed.

## Actual acceptance

Stage130 immutable manifest:
`bf90fe64c3000aaa7178f3accdbaebdb8196be7a83c24fda2f28022028bfda4e`.
Original schema plus five explicit receipt installers, actual owned PostgreSQL
session, canonical transcripts and inert in-process provider responses verify:

- Authenticated positive-duration INSERT, goals preparation without a clock,
  first planner dispatch stamp, actual intent/fact/goal proof, early completion
  and original runner removal.
- Both preserved genuine-capability seal regressions refuse without new writes
  or provider calls.
- An independently invoked manual observer drains a genuinely blocked worker
  after the persisted deadline; restricted final assessment settles timeout,
  without worker errors, pump quarantine, clock changes or repeated inference.
- Exact final receipt proof retires the original runner and clears pending manual
  ownership, including when admission is sealed before removal confirmation.

Earlier FK-enforcement, seal, freeze-disposition and final-removal failures remain
recorded in ISOLATED_QA.md and their immutable reports. They were corrected and
rerun, not relabeled as successes. Stage130 does not establish automatic scheduling,
process containment, restart recovery, complete final proof writes, arbitrary
plugin composition, frontend integration or exhaustive concurrency coverage.

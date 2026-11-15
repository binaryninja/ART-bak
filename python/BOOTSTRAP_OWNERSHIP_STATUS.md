# Explicit create/bootstrap ownership bridge (work in progress)

The optional EngineRunner initialization mode now constructs an inert
BootstrapHandle, installs it under the exact TaskManager token, and defers all
journal/model work until an explicit owned advance. The same outer handle remains
in inventory across initialization and its child TaskEngineHandle. Prepared goals
are not task completion; child execution begins on a later advance.

BootstrapCoordinator has separate created-only and typed admitted-running entry
points. The latter requires the exact installed manager generation, host lifetime
and phase-limited capability. The Python-only journal allocates durable run IDs
and binds task text/configuration/ownership. Installation is explicit and fresh-only;
the original schema is unchanged. Uncertain SQL/transcript/model effects retain
ownership and cannot be automatically retried or adopted by another generation.

Construction validation pins the actual coordinator/executor/configuration as
well as the handle's public identity. Pure readiness must explicitly reject
unsupported initialization options before task creation. Trusted host factories
must remain inert; Python object checks are not an isolation sandbox for arbitrary
factory code. Unsupported deadline/inherited/seed behavior is not silently enabled.

TaskManager.owns_generation is a process-local capability check, not a database
lease. A shared AdmissionController can reserve capacity for direct HTTP and queued
admission using actual host/runner inventory, including bootstrap and unresolved
handles. It is explicit and single-process only. Failed reconciliation quarantines
new admission; no hidden background scheduler or default pump is created.

Independent component regressions cover pinned bindings, retained callbacks,
owner-thread/phase checks, generation retirement, callback reentrancy, capacity,
and cleanup quarantine. See ISOLATED_QA.md for immutable acceptance evidence.
Stage 49 additionally exercised actual authenticated HTTP creation through
bootstrap, engine proof and manager completion in disposable PostgreSQL, plus
pause, uncertainty and capacity cases. This scoped acceptance does not prove
all source lifecycle options or production readiness.

The original Go implementation remains the reference. No production deployment,
automatic recovery, distributed ownership, real-provider/target testing, or full
frontend compatibility is claimed. Deadline and settlement integration is separate
work; the default engine continues to reject unsupported timed-task operation.

## Per-run bounded settlement (separate, optional)

The bounded runtime can reserve a settlement slice strictly inside existing host
limits. It shares the run's accounting and ownership; there is no extra total
budget. Entry is restricted to clean ordinary turn/time exhaustion, never a
callback failure, user cancellation, refusal, permission denial, stale scope or
uncertain tool effect. Tool exclusions are enforced at dispatch, not just in the
model-visible catalog. Default behavior and engine task deadlines are unchanged.
The original terminal reason is retained. The separate settlement_finished event
records whether settlement actually completed; future persistence integration
must inspect it rather than interpret the original reason as successful cleanup.

Frozen checkpoint: 1,915 authored tests pass; 385 captured SQL statements parse.
Static method/path registration coverage is 140/261 (121 unregistered), including
optional/unavailable contracts, not a count of complete features.

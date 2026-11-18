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

## Original host and generation bindings

These two process-local checks extend the ownership checks at baseline
`4426a2b2bd39ad0c65a0a60823127b15bfafffe1`.

- During work-slot validation, `AgentHost._identity` verifies that the manager
  and runner are the original objects and that the manager still references
  that runner. It checks the pair before resolving the optional runner claim,
  after resolving it and after calling it. A changed pair raises
  `RuntimeUnavailable`. If an inert work callback already ran, a later identity
  failure can reject its result but cannot undo its effects.
- `BootstrapHandle` requires the original `TaskManager.owns_generation` method
  at construction and when checking its bindings. An inherited original
  method is supported. An instance replacement or a changed class descriptor
  causes refusal. Ownership checks call the original predicate on the pinned
  manager with the existing task ID, token, handle and cancellation flag.
  The original manager still decides whether that generation is installed.

The focused tests use empty managers, inert callbacks and handles without an
installed generation. They check refusal after substitution, rejection of an
uninstalled handle and release of a host work slot after an exception. The
Stage 49 results above describe earlier, separately scoped acceptance. These
binding tests establish neither a positive installed-generation lifetime nor
exclusion across processes. Task startup, durable leases and live-provider
execution remain outside their evidence.

## Ownership during a hosted step

An installed bootstrap handle enters the manager's generation guard and the
original coordinator's lifecycle guard around each `advance`. The guards span
bootstrap, child construction, and child advancement, including the original
lifecycle guard's exit callback. Pause can signal cancellation immediately.
The manager retains the original token and handle while guarded work unwinds.
An exception that escapes the generation guard leaves the run uncertain.
The registry mutex is released before invoking the lifecycle guard or work.

Only writers that use the supplied lifecycle guard share its exclusion. The
manager guard covers process-local retirement. Neither guard establishes a
database lease against an independent writer.

## Terminal persistence and shutdown

Completion fences the generation before the first database call. The terminal
update requires a running, unpaused, unqueued task with no archive or deletion
timestamp. The subsequent lifecycle read includes both timestamps. A competing
update refreshes the local snapshot and leaves the run fenced for reconciliation.
A missing row or read error also leaves the run fenced. A lost acknowledgement
can leave the persisted outcome unknown. Repeated completion calls on the
fenced run return false before touching the store.

During host polling, an acknowledged terminal write leaves the original run
registered until the runner returns its receipt and the host checks that the
handle has left inventory. An interrupted callback keeps the run registered.
A run retained only by the manager counts as pending and blocks clean drain
and lifetime closure. Later independent database changes can supersede the
last observed lifecycle snapshot.

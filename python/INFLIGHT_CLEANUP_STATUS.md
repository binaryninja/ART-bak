# Exact in-flight cleanup (opt-in prerequisite)

The task runtime may explicitly configure `cleanup_authorize`; the default is
unchanged. The enabled mode is part of the durable runtime configuration digest.
BootstrapHandle binds one code-owned InflightCleanupOwner to its actual inert
engine, canonical transcript, WorkerStore and optional ActivityProjector. No
manager, generation or generic cleanup permission is inferred from a callback.

Reservation occurs only after a canonical CREATED pending turn exists, while
both installed outer BootstrapHandle and original child engine are active on the
same thread, ordinary execution is still authorized, and manager ownership is
exact. An idle installed handle with a separately invoked worker is insufficient.
The opaque capability binds the immutable scoped transcript identity and, for a
worker, the actual running claim/version/owner/token. Copied/foreign/reused caps
cannot substitute. Manager retains the cap through all declared cleanup stages.

After execution is paused or frozen, the separate policy may allow only:

1. Commit that already-created canonical turn, under original lifetime/resource
   and durable task/bootstrap checks. A model return racing revocation is recorded
   as cancelled while preserving messages/usage, not promoted to completed work.
2. CAS the predeclared claimed worker version to the source-shaped cancellation/
   error terminal result. The actual worker cancellation cause is checked, not
   accepted from an arbitrary caller. Generic freeze revocation does not invent
   a timeout cause; its cancelled outcome is blocked absent an explicit cause.
3. Project the exact canonical terminal receipt through the original projector,
   retaining its independent grant and idempotency/SQL checks. Activity describes
   the turn outcome, never proof of task goals.

No capability permits a new transcript begin, model/tool call, intent claim,
proof/fact mutation, arbitrary store callback, resource transfer or recovery.
Known cleanup stages finish before registry release. Failed persistence or stale
identity retains/quarantines ownership; no inference replay is implied. Freeze
cannot be confirmed drained while any cleanup capability remains. Pause/admission,
ordinary completion and retirement cannot silently discard pending cleanup.

Tests use actual authored host→BootstrapHandle→engine→planner/worker paths with
inert providers, synthetic SQL and canonical temporary transcripts. The freeze
case uses explicitly synthetic positive-clock pins only to exercise the existing
freeze protocol; public positive-duration admission remains disabled. It verifies
that freeze during the provider first returns pending, exact cleanup records the
cancelled result without another model call, and only a later drain check succeeds.

This is not a timer, timeout-dispatch clamp or automatic expiry coordinator.
Synchronous provider cancellation remains cooperative, and unknown effects remain
unresolved. JSONL and PostgreSQL are not atomic together. All participating writers
must share the same lifetime/version barriers. Independent review and disposable
PostgreSQL acceptance are separate from these synthetic tests.

## Exact candidate PostgreSQL acceptance

The cleanup-only snapshot passed 2698 authored test executions and parsed 454 unique
SQL statements. Exact-dependency stage91 reran five real zero-timeout cleanup
scenarios plus the intentional positive-timeout SQL refusal, with 27 focused tests:
`e729a65b98a3b93a77986773d3b291b22b9c3b2085f2c8514869e1424f0b91a3`.
No later clock/context/provider edits were overlaid. Initial active pause gates
cancellation but raises TaskConflict; only the subsequent drained retry persists
paused. Explicit task_pause cleanup returns the same claimed intent to open.
Actual positive-duration timed freeze remains unsupported; only narrower protocol
fixtures exercise synthetic positive ownership. No fabricated active flags or
inference retry is used to manufacture clean drain.

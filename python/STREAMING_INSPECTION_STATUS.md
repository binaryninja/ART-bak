# Streaming, recording and explicit execution primitives

This checkpoint extends configured chat runtimes without claiming a complete
replacement for the original platform.

- Streaming profiles use actual provider streams, not a nonstreaming fallback.
  Complete tool calls are buffered and validated before execution. EOF, refusal,
  partial JSON, scope/profile changes, cancellation and cleanup failure do not
  become false completion. Final fences run after iterator cleanup and approval.
  Provisional progress callbacks are explicit; they are not authoritative results.
- Recording table setup and builtin metadata/prompt initialization are explicit,
  bounded transactions. Current customizations survive insert-if-missing seeding.
  Supported legacy recording upgrades are narrowly catalog-validated. No provider,
  recording, tool, security-rule or runtime configuration is automatically enabled.
- Scoped record/usage persistence and authenticated inspection preserve body-free
  lists and require explicit body authorization/redaction. The optional inspection
  app composes existing protected services and rejects conflicting executors/routes.
  Provider-recording wiring and archive-aware totals are not inferred from tables.
- Archive ownership metadata now has source-grounded shared-host/asset and aggregate
  queries under the caller's snapshot/quiescence authority. This does not provide
  lifecycle drain, filesystem snapshots, extraction, restore or pruning.
- Opt-in process execution requires a fresh explicit launch Grant, fixed environment,
  bounded output/time and honest cleanup. Root is a working directory, not a sandbox.
  Process-group escape and numeric identifier reuse require external containment.
  The tool registry binds only supplied callbacks, enforces schema/bindings/gates/
  revisions and makes deferred discovery distinct from authorization. Stored exec
  strings are never evaluated or turned into default execution authority.

Independent review and isolated PostgreSQL/inert-stream evidence are in
ISOLATED_QA.md. Seven harmless real POSIX fixture checks ran only inside the
credential-free network-isolated harness. No real providers, external targets,
production databases, deployment or notification delivery occurred.

Planner/worker orchestration, complete source tools/frontend, attachments/mentions,
automatic compaction, distributed leases, cross-store recovery and production
hosting remain incomplete. New modules under review are not part of a frozen
checkpoint merely because they appear in a working tree.

Frozen checkpoint verification: 1,316 authored Python tests pass and 326 captured
SQL statements parse. Static inventory represents 116/261 source method/path
contracts (145 unregistered), including optional services and unavailable paths.
These are not 116 complete features or a production-readiness measurement.

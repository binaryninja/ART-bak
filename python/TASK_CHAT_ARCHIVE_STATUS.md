# Task-chat, catalogs and archive primitives checkpoint

This extends the explicit conversation runtime; it does not complete the Go port.

- Task-main chat can admit a real bounded turn, persist scoped JSONL and task
  activities, return202 and support status/stop. Exact task/exploration/segment/
  run/revision/model/prompt identity is durably bound to client receipts. A changed
  scope or wrong activity sink is rejected before new activity or execution.
  Host lifecycle guards, current-scope resolution, provider and permission gates
  remain explicit; no autonomous planner/worker revival is implied.
- The agent catalog supports persisted list/detail, transactional custom-agent
  plus starter-prompt creation and metadata edits. Catalog-aware authenticated
  conversation assembly requires explicit readiness and prompt rendering.
  A discovered system-prompt scalar/list mismatch was corrected; full prompts now
  reach the provider intact. Disabled/nonassistant/retester and unsupported bound
  profile/tool semantics do not silently become executable.
- Tool catalog metadata CRUD preserves bindings, builtin schema structure and
  transaction boundaries. Stored exec text is opaque data, never executed or
  registered automatically. Deleting metadata does not revoke running callbacks.
- Archive bundle validation/build/publication and bounded 22-table snapshot export
  preserve source format and shared ownership dependencies. Explicit readonly
  repeatable-read transactions are available on the product executor and pool.
  Complete ownership/aggregate metadata, host quiescence, file/evidence snapshots,
  lifecycle transitions, restore and cleanup are still separate prerequisites.
  The optional archive codec extra is zstandard0.25; none is installed implicitly.
- More original Go comments/prose and launchers are English. Legacy protocol
  labels and Unicode-sensitive fixtures are preserved as escapes, not silently
  changed. English compaction labels exposed an exact token-estimator rounding
  issue; chunk fitting now respects the actual estimator and unchanged reserves.

## Verification boundaries

See ISOLATED_QA.md for real PostgreSQL activity/catalog/archive checks and the
selected translated-Go regression. Providers and tool handlers were inert. No
real targets, notifications, shell commands, production data, deployment or
infrastructure changes were involved. Registration counts and authored test counts
are not functional parity or readiness certification.

Full main/planner/worker orchestration, source tool integrations, frontend flows,
attachments/mentions, distributed ownership and SQL/JSONL crash recovery remain
unfinished. This is a draft checkpoint, not a production replacement.

Frozen checkpoint: 1,198 authored Python tests pass; 295 generated SQL statements
parse. Source inventory represents 110/261 method/path routes (151 unregistered),
including optional services and unsupported operations, not 110 complete features.

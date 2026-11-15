# Explicit host, read-only exploration and profile persistence

This stage remains an incremental port, not a production server replacement.

- `agent.host.AgentHost` fences manager admission/control against premature
  settlement. Explicit poll/drain calls preserve exact-generation checks and
  distinguish running threads from pending persistence. No background poller is
  created. All lifecycle calls must use the host for its ordering guarantees.
- Read-only graph/frontier/intent HTTP uses an explicitly authorized task-store
  resolver and optional active selection. Store and inherited-source ownership
  are checked. Node-board pagination remains 503 until persistence is integrated.
  Both application factories accept these dependencies without guessing stores
  or performing startup exploration reads.
- Scoped node-board persistence now provides bound pagination, neighbor nodes,
  touching edges and anchor IDs. Both edge endpoints and inherited visibility
  remain scoped; HTTP node-board composition is still unavailable.
- Profile persistence preserves complete configuration/retry fields, private
  credential reads and a separate redacted public representation. No HTTP profile
  admin or provider transport is enabled. Deletion remains unavailable pending
  reference locking, failover cursor repair and retry semantics.
- Route inventory now invalidates stale literals after mutation, reassignment,
  escaping aliases and conditional writes rather than undercounting silently.
  Registration counts still do not prove behavior or adapter availability.

See ISOLATED_QA.md for real PostgreSQL smoke checks against an earlier immutable
checkpoint and original-Go baseline failures. These results do not establish
complete SQL, concurrency, service or frontend parity. The original Go source
and deployment path remain intact; this branch must not be treated as a drop-in
replacement.

Independent review caught and fixed public profile placeholders overwriting
stored credentials. Redacted URL/masked key values now fail before writes and
short keys no longer expose their whole value in a hint.

Immutable checkpoint verification: 596 authored offline tests pass; 164 distinct
generated SQL statements parse. There are 38 route registrations against 261 Go
registrations (223 unregistered); explicit 503 stubs remain incomplete.

Real PostgreSQL smoke against the previous findings checkpoint identified an
archive-state mismatch: the port used `failed` instead of persisted
`archive_failed`. This stage corrects it and adds create/report rejection and
success regressions. Unknown states still fail closed.

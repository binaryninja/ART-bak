# Dynamic task goal reads

Optional `goal_authorize(scope)` and
`goal_request_authorize(request, scope)` enable only the dynamic
`GET /api/tasks/{id}/goals` projection. Both grants are required; neither is
inferred from task visibility, JWT authentication or model approval. The actual
bounded `ExplorationStore` query runs for the resolved task/exploration. Original
selector and read lifetime checks, parent row locking and repeated grants through
serialization, transaction exit and guard exit prevent stale disclosure.

## Source and response contract

- `server/goals_api.go:18–29` lists goal nodes, newest first, with a 10,000-node
  limit. `server/dto.go:212–242` defines the exact projection.
- Response is `{"goals": [...]}`; each entry has decimal-string `id`, `text`,
  persisted `state` and RFC3339 `ts`; nonempty `vulnclass` and `origin` are optional.
  `web/src/lib/types.ts:450–457` expects precisely these fields. Empty goals remain
  an empty array. IDs are not converted to JavaScript numbers or remapped.
- Upstream GoalDTO has **no enabled flag, children, evidence links, graph edges,
  assessment result or task completion flag**. Their absence here is not a dropped
  field. Those belong to separate graph/engine contracts. Reading a persisted
  goal state of `met` does not prove evidence validity, a fully satisfied plan,
  drained workers or authoritative task completion.

## Explicit differences and exclusions

The Python read rejects a noncanonical path ID, body or query rather than using
ambient selection. Its explicit selector policies may deny before task lookup.
Absent capability returns 503; unavailable local task mapping returns 404 and
permission denial 403. Storage/projection failures are redacted 503 rather than
Go's raw database error at 500. The bounded SQL projection fails on row/byte
overflow instead of returning a silently truncated first 10,000 goals. Malformed
payload field types fail closed; Go ignores unmarshal errors when making its DTO.
These are intentional bounded-disclosure differences, not exact error parity.

Only goals of the resolved local exploration are returned. No inherited/archive
read fallback, enabled-state synthesis or evidence expansion is added. The read
uses a SHARE parent lock, not the existing CRUD service's FOR UPDATE lock. No
POST/PATCH/DELETE routes are installed by this component, no goal state changes
occur, and source planner notification/revival on manual edits is not implemented
by this read slice. Existing separate mutation services retain their own policies.

Six new synthetic tests cover two tasks in one app, exact DTO state/IDs, scoped
bounded SQL, authentication/selector ordering, late grant revocation and scope
change. Independent review and actual PostgreSQL acceptance are separate gates;
this document does not claim browser execution or whole-overview parity.

# Timed source-schema prerequisites: query-derived proposal

Read-only design inventory, not an implemented validator profile or deployment
certificate. The implemented `extension-v1` observation checks five extension
schemas and only source identity keys. Nothing here broadens that result or
changes setup/admission. Architecture confirmed this inventory has no overlapping
implementation work.

## Scope boundary

The proposed narrow follow-up profile covers prepared-generation clock stamping
and observation, ordinary frontier completion (stamped/unstamped), expired final
assessment journal reservation, restricted final `list_goals`, and terminal graph
assessment/CAS. Bootstrap journal parent/origin/goal-lineage reads are listed
separately. It does NOT cover TaskStore creation/hydration, profile resolution,
GoalPlanner/worker graph writes, claim/cleanup/activity projection, full runtime
assembly or database-wide readiness. Those execute additional queries and need
separate manifests. No single `source_ready` flag should erase those boundaries.

Important correction to the earlier draft compatibility proposal: `tasks.status`
is **TEXT with a CHECK**, not a PostgreSQL enum. Node `kind`/`state` and edge `rel`
are also TEXT with CHECK constraints. Do not invent enum types.

## Query-to-column inventory

All types below are the actual current `db/schema.sql` types. `NN` means NOT NULL;
otherwise the field is nullable. A parenthesized default is part of the source
contract, not a value to backfill or infer from missing data.

### `public.tasks`

- `id bigint NN`, primary key; `exploration_id bigint NN`, unique, FK to
  `explorations(id)` ON DELETE RESTRICT.
- `status text NN` (default `'created'`), CHECK exactly
  created/running/paused/done/failed/timeout.
- `paused boolean NN` (false), `queued boolean NN` (false).
- `timeout_seconds integer NN` (0).
- `first_run_at`, `deadline_at`, `archived_at`, `deleted_at`, `completed_at`:
  nullable timestamptz, no defaults.
- Bootstrap parent equality additionally reads `description text NN` and
  `goal text NN` (no defaults).

Consumers: `TaskDeadlineStore.validate_current/stamp/observe_expiry` and paused
read paths; `OwnedFrontierCompletion` candidate reads; `FinalAssessmentJournal`
parent; `FinalSettlementStore` persisted tuple and terminal CAS; both ordinary
completion stores' parent/CAS/readback; `BootstrapJournal` parent. The newer
`TaskEngineHandle._snapshot` additionally reads task ID plus nullable first/deadline
clock and joins the exact prepared bootstrap generation. It does not add source
columns beyond the list above.

Clock SQL uses `COALESCE(first_run_at, now())`, preserves deadline on an already
stamped row and computes an initial positive deadline with `make_interval`.
Partial NULL clocks are runtime-invalid for some operations, but the schema must
still allow NULL because an unstamped task is legitimate. There is no source
CHECK requiring timeout >0; do not impose that on the entire tasks table.

### `public.task_archives`

- `task_id bigint NN`, unique, FK `tasks(id)` ON DELETE CASCADE.

The selected queries only test `NOT EXISTS (... WHERE task_id=...)`; any archive
record excludes the task, independent of archive state. They do not read state,
archive package path or restore metadata. The narrow profile must not claim those
uninspected columns/workflows are ready. RLS/rules must not hide archive rows.

### `public.explorations`

- `id bigint NN`, primary key.

Only the referenced identity is structurally required by these selected reads.
`goal`, `status`, `round_no` and update behavior become requirements for creation,
planner/worker work, but are not silently included in this minimal profile.

### `public.exploration_nodes`

- `id bigint NN`, primary key; `exploration_id bigint NN`, FK
  `explorations(id)` ON DELETE CASCADE.
- `kind text NN` (no default); `state text NN` (default `'open'`).
- Restricted final `list_goals_bounded` additionally reads `payload jsonb NN`
  (default `'{}'`), nullable `origin text`, and `created_at timestamptz NN`
  (default `now()`). Its synthetic DTO constants are not extra stored columns.

Require validated exact source `ck_node_kind` and `ck_node_state` predicates:
kind begin/goal/intent/fact/finding/hint/digest, with states:

| Kind | Allowed states |
| --- | --- |
| begin | open |
| goal | open, met, abandoned |
| intent | open, running, paused, done, blocked, exhausted, stopped, deleted |
| fact | confirmed, dismissed, origin |
| finding | confirmed, dismissed |
| hint | active, consumed |
| digest | active, superseded |

Ordinary frontier completion reads local goal/intent IDs/kinds/states only:
no open goals and no open/running intents. It does not infer achievement from
empty/abandoned goals or require evidence proofs. Expired final settlement is a
separate deliberately narrower local-goal/confirmed-fact-or-finding proof policy.
Neither operation reads the standalone `findings` table in the selected SQL.

### `public.exploration_edges`

- `exploration_id`, `src_id`, `dst_id`: bigint NN; FKs to explorations.id and
  exploration_nodes.id, ON DELETE CASCADE.
- `rel text NN`, no default.
- PK `(exploration_id,src_id,rel,dst_id)`; `src_id <> dst_id` CHECK; rel CHECK
  spawns/derived_from/yields/proves/covers.

Final settlement reads local proves edges. Bootstrap prepared-replay validation
joins spawns edges to local origin fact and goals. The FKs do NOT prove src/dst
belong to the edge's exploration; participating query/graph-writer ownership
checks remain required. Do not invent a stronger composite FK and call it source
parity. Edge created_at is outside these selected reads.

### `public.task_relations`

- `task_id`, `source_task_id`: bigint NN, each FK tasks.id ON DELETE CASCADE.
- PK `(task_id,source_task_id)` and CHECK task_id <> source_task_id.

Only expired `FinalSettlementStore._truth` queries existence with LIMIT 1 and
refuses inherited final settlement. Ordinary local frontier completion does not
read this relation or reject inheritance on its own. No recursive source
expansion or inherited writes are certified.

## Structural and behavioral requirements for a future profile

Use exact validated source key/FK/CHECK contracts, built-in types, intended
nullability/defaults and immediate indexes. Allow unrelated source columns and
ordinary additional performance indexes rather than demanding full table equality.
Bound catalog transfer and fail closed on unknown major/deparser expressions as
in extension-v1. Do not execute CHECK/default/function expressions for testing.

The selected queries use unqualified source relation/function names. A setup
contract must separately pin the actual session search_path and resolve these
names to the checked public relation OIDs; merely inspecting public.* does not
prevent temporary or other-schema shadowing during execution. A read-only
validator may observe these bindings, not change them with SET. Connection/pool
changes and later DDL require renewed validation and deployment coordination.

RLS, policies and rewrite rules affecting these reads/writes cannot be ignored:
hidden archive/goal rows can produce false frontier readiness. User triggers can
alter a terminal CAS. Source tasks has the legitimate `trg_tasks_upd` BEFORE UPDATE
calling `set_updated_at`, which requires `updated_at timestamptz NN DEFAULT now()`
as a transitive write prerequisite. Source explorations/nodes have analogous
update triggers, but these selected operations do not write them. A future
behavior manifest must verify exact allowed trigger timing/events/function body,
function namespace/language/security-definer/search_path behavior and transitive
columns, or explicitly refuse the schema. Function name alone is not sufficient.
Do not reject every source trigger as though these were fresh extension tables.

This trigger/session-behavior design remains an unresolved prerequisite before
setup integration. Grants, readonly mode and availability can be separately
observed, but catalog readiness never constitutes operator permission, canonical
transcript proof, manager ownership or cross-process writer coordination.

## Evidence location and next decision

Inventory derived from current queries in `db/task_deadline.py`,
`db/{final_assessment,final_settlement,early_completion,unstamped_completion}.py`,
`agent/{bootstrap,frontier_completion,engine,final_assessment}.py` and
`ExplorationStore.list_goals_bounded`, compared to `db/schema.sql` definitions.
No original programs or migrations were run. Next agree the precise profile name,
operation subset and source trigger/session identity contract, then implement and
independently validate it. Do not attach this proposal to extension-v1 as a
ready-for-timed-setup claim.

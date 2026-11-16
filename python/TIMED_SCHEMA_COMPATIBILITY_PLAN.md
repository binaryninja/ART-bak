# Timed schema compatibility: proposed read-only prerequisite

Proposal only. No validator, setup hook, installation, migration or positive
admission change is implemented by this document.

## Inspected contracts

The fresh-only installers in `agent/bootstrap.py` and
`db/{final_assessment,final_settlement,early_completion,unstamped_completion}.py`
check database identity and prerequisite object existence before their explicit
DDL. They reject an already-present destination. Their result does not certify
an existing deployment's columns, constraints, defaults or runtime readiness.
The base installer `db/schema.py` similarly supports fresh bootstrap, not upgrades.

Required extension manifest, derived from each exact DDL:

| Relation | Identity and required constraints | Defaults / special columns |
| --- | --- | --- |
| artex_task_bootstrap | PK task_id; unique run_id; FKs task_id→tasks.id, exploration_id→explorations.id; phase running/prepared/unresolved | run_id bigint sequence; revision bigint default 1; goal_ids bigint[] default empty; created_at/updated_at timestamptz default now(); other columns non-null |
| artex_final_assessment | PK attempt_id; unique (task_id,run_id); task/exploration FKs; positive timeout; phase reserved/running/finalized | attempt_id bigint sequence; revision bigint default 1; only transcript_turn/result_digest nullable; timeout_seconds bigint |
| artex_final_settlement | PK attempt_id and FK→final_assessment.attempt_id; unique (task_id,run_id); task/exploration FKs; status done/timeout | all columns non-null; no defaults |
| artex_early_completion | PK (task_id,run_id); task/exploration FKs; positive timeout; status done; reason frontier_drained | all non-null; no defaults; timeout_seconds integer; first_run_at/deadline_at timestamptz |
| artex_unstamped_completion | PK (task_id,run_id); task/exploration FKs; positive timeout; clock_state unstamped; status done; reason frontier_drained | all non-null; no defaults; timeout_seconds integer; deliberately no timestamp clock columns |

The implementation manifest must enumerate **every** column in order, exact
PostgreSQL type/schema/typmod/array type, nullability and default. This table is
an overview, not a replacement for that manifest. Foreign keys are immediate,
validated, nondeferrable, MATCH SIMPLE, NO ACTION update/delete as authored.
Check predicates must be validated; a same-name disabled/unvalidated check does
not count. PK/unique indexes must be valid, ready, immediate and match exact key
columns without expressions, predicates or unexpected INCLUDE columns.

## Proposed explicit API

`validate_timed_schema(executor, *, expected_database, expected_schema='public',
profile='timed-v1') -> TimedSchemaObservation`

- Only exact nonempty database name, `public`, and an implemented immutable
  manifest profile are accepted. No default database inferred from a connection.
- Existing native-bound `Query`/Executor read interface; no connector creation,
  credentials, `execute`, scripts, SET, locks, migrations, sequence advancement,
  seeding, model calls or data-row reads. Constructor/import inert.
- One bounded catalog SELECT returns the complete identity and descriptor
  snapshot. Fully qualify `pg_catalog` relations/functions, filter requested
  schema/object names through bound parameters, and include overflow sentinels
  before large catalog expression/name transfers. Do not assemble independent
  existence/read queries under an assumed READ COMMITTED snapshot.
- Return immutable observation: expected database/schema, server major, manifest
  version/digest, observed descriptor digest and inspected relation names.
  No `runtime_ready=True`, callback authority or transferable admission permit.
  Generic errors include only manifest object/field categories, not raw defaults,
  object bodies, connection details or database contents.

Suggested catalog descriptor: `pg_namespace`, `pg_class`, `pg_attribute`,
`pg_type`, `pg_attrdef`, `pg_constraint`, `pg_index`, `pg_depend`, `pg_sequence`,
`pg_trigger`, `pg_rewrite`, and `pg_policy`. Confirm exact catalog fields and
supported-major output in independent disposable PostgreSQL tests before coding
is accepted. Do not evaluate stored expressions to test them.

## Fail-closed structural rules

For the five extension tables, require exact permanent ordinary tables, no extra
columns/constraints/defaults, RLS/policies, user triggers, rewrite rules,
inheritance, partitioning or generated/identity columns absent from authored DDL.
Do not mistake a view, foreign table, domain-typed column or similarly named
object in another schema for the expected table. Internal FK triggers are checked
through their validated FK descriptors, not prohibited as user triggers.

Resolve serial defaults to their exact owned bigint sequence in public using
catalog dependencies; verify sequence type/increment/min/max/cache/cycle and
ownership. Do not compare OIDs between databases or invoke nextval. Refuse a
custom function masquerading as the expected serial default. Normalize only
explicitly supported PostgreSQL deparser representations of known defaults and
CHECK expressions; no regex stripping or broad semantic equivalence claims.
An equivalent but unrecognized expression is unsupported, not silently accepted.
Initial supported PostgreSQL major(s) must be selected from verified fixtures,
not inherited blindly from the installers' PostgreSQL 12+ floor.

Also require a separately specified source-prerequisite profile: at minimum
actual `tasks`/`explorations` PKs, task status enum/schema, timeout integer default
0 and nullable timestamptz clock columns, lifecycle/deletion/archive columns,
plus exact graph/archive columns used by the selected lifecycle operations.
Do not claim full-schema validation from these five extensions. Source tables
have legitimate triggers and additional fields, so use an explicit required
subset/allowed-behavior manifest rather than applying extension-table restrictions
indiscriminately. This source profile is a required design input before any setup
integration, not optional guessed compatibility.

## Setup seam and non-guarantees

Architecture recommends an explicit `TimedLifecyclePolicy` /
`PreparedTimedLifecycle` preflight seam bound to the original executor and named
database. First implement standalone observation; do not wire this before that
policy API and its exact manifest requirements are agreed. Subsequent setup must
validate against its original executor, not accept a caller-constructed dataclass
as proof. Missing/incompatible schema means refusal and an operator-facing gap,
never automatic installation.

Catalog inspection is a point-in-time compatibility observation. It neither
prevents later uncooperative DDL nor proves permissions, database availability,
canonical transcript integrity, owner lifecycle, configuration approval or
execution authorization. Hosts must exclude schema changes during owned work;
revalidation is still needed before relevant admission. No cached timeless
readiness flag or enabling positive timeout gates follows from this result.

## Proposed independent acceptance matrix

Fresh disposable schemas from each reviewed installer pass the selected profile.
Negatives: wrong database/schema; missing/extra/retyped/nullable column; altered
numeric width/default; missing/wrong/unvalidated/deferred check/FK/unique key;
invalid or partial unique index; wrong serial owner/default/function/cycle;
RLS/user trigger/view/foreign table; temporary shadow object; overflow and unknown
major. Assert one catalog snapshot and zero mutating calls, zero sequences
advanced, stable descriptor digest without cross-database OIDs, and no raw
sensitive expression in error text. No production database acceptance implied.

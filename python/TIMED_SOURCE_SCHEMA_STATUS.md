# Restricted task runtime source contract (candidate, not accepted)

`db/timed_source_schema.py` observes a single bounded PG17 catalog snapshot.
It does not install anything, evaluate stored expressions/functions, read table
rows or credentials, acquire an execution grant, change search_path, or enable
positive-timeout admission. The candidate baseline is pinned in code; any missing or altered manifest fails
closed before SQL. Independent review and immutable real-PG acceptance are pending.

## Selected contract and baseline provenance

The code-owned `task-runtime-v1` baseline was captured by isolated QA from a
fresh database installed from the reviewed original packaged `schema.sql`, not
from the database being validated. The checked-in manifest records that source
SHA256 and PostgreSQL version. Runtime accepts no caller fingerprint/descriptor
and never updates its baseline. Stable schema/table/column/function identities,
not database-local OIDs, are compared. Exact canonical JSON preserves bool/int/
string distinctions; order of catalog rows is irrelevant. Unsupported PostgreSQL
majors and unrecognized deparser spellings fail closed.

Sixteen required tables include tasks, explorations, nodes, edges, anchors,
archives, relations, scope, categories, profiles, task profile chains, agents,
agent prompts, companies, assets and asset links. Category/scope/relation/chain
reads occur during TaskStore hydration even when their mutation modes are off.
Anchors reference assets; assets' source trigger reaches task_asset_links. Three
source functions (`set_updated_at`, `try_inet`, `sync_task_asset_links`) and owned
sequences complete the selected explicit dependency closure. Full columns,
defaults, keys/checks/FKs, column and index collations, database default locale/encoding, index
expressions/opclasses, user triggers,
function bodies/attributes and sequence ownership are compared. Extra behavior,
RLS/rules/inheritance, missing objects, custom expression dependencies, changed
function bodies or trigger enablement are not silently accepted.

PL/pgSQL string bodies do not expose all references through pg_depend. Their
exact reviewed bodies and the explicitly named source closure are therefore part
of the manifest, not an inferred claim that PostgreSQL proved all dynamic SQL
safe. The source functions are never called by validation. Built-in pg_catalog
objects and the PostgreSQL server are trusted; this is not a hostile-superuser
sandbox or a proof against later DDL.

## Candidate limits

Architecture's initial candidate must separately reject process tools, activity
projection, arbitrary planner/worker plugin catalogs, inherited tasks, company seeding,
and goal constraint/scope extraction. Supported shape is local goals-only
bootstrap, fixed planner add_intent, cleanup, clock and reviewed final lifecycle.
The exact native GraphTools record_fact/list_facts/list_goals/prove_goal queries
are covered by the same nodes/edges/anchors/task/archive schema closure; enabling
these still requires independently validated code-owned schemas and original
role/asset grants in the admission layer. No arbitrary tools are certified. This source observation does not validate filesystem
transcripts, prompts/grants, providers, credentials, runtime ownership or optional
settings/health persistence. It is not a general application/schema readiness
flag; `source_readiness` remains false. Separate reviewed extension-v1 validation
and actual host capability issuance are still required.

The effective schema list must be exactly pg_catalog/public; relevant temporary
shadows are refused. This conservative profile may reject a harmless extra
search_path schema. Checks describe the observed connection and instant, not all
sessions in a pool or future DDL. Deployment must coordinate schema/session
stability; no observation alone supplies that guarantee.

## Bounds and verification

Snapshot limits: 64 relations, 512 columns, 256 constraints, 256 indexes, 32
sequences, 64 user triggers and eight functions; one MiB total JSON, individual
expression/function projections capped before transfer. Overflow refuses the
whole observation, never truncates acceptance. These bound transfer, not a claim
that a maliciously huge catalog has constant CPU cost.

The initial candidate used the complete untouched sourcebaseline-v2 PG17.11 capture:
27 relations, 213 columns, 74 constraints, 72 indexes, 11 sequences, 12 user
triggers and three functions. All FK targets and user-trigger functions stay
inside the declared closure; no custom default/check/index function dependency
was observed. Database locale is the captured UTF8/libc C/C contract, not a claim
that every production locale is supported. The original source and query-module
hashes are recorded in manifest provenance. Canonical contract extraction sorts
rows only; no scalar normalization or semantic substitutions occur. The untouched
actual descriptor passes the candidate validator offline. That initial candidate had eight owner and six independent authored tests pass;
the enforcement correction below supersedes its acceptance state. Independent manifest review and immutable actual
PostgreSQL positive/hostile-DDL acceptance remain required.
No setup/runtime wiring is part of this module.

## Internal FK enforcement blocker and correction

An unchanged actual-PG diagnostic disabled all four internal FK triggers on
`task_relations`; the prior candidate still accepted and a rolled-back orphan
insert succeeded. Its baseline did not record internal enforcement. The corrected
query adds bounded internal-trigger descriptors (up to 512), stable constraint
source/target columns/actions and built-in function/event/enabled/deferrability
state, omitting generated trigger names/OIDs. It also requires observed origin
session replication role. The v3 pristine capture now replaces the baseline, retaining its provenance and
all 134 internal trigger descriptors without scalar normalization.

The footprint includes internal triggers on selected tables for incoming foreign
keys from other source tables. Their exact enforcement identity is compared;
this does not certify all columns or workflows of those external tables. Selected
runtime operations neither delete selected parent rows nor mutate their IDs.
No positive admission clearance is claimed while fresh baseline/enforcement QA
is outstanding. The original confirmed failure remains preserved separately.

### Explicit required-extension composition

`task-runtime-v1` is intended for the original source PLUS all five required
receipt extensions, not a pristine-source-only deployment. Its reviewed pristine
v3 manifest remains unchanged (134 internal triggers). Code adds precisely 20
known parent-side trigger entries on tasks/explorations, derived from the same
explicit immutable eleven-FK contract checked by extension-v1. The expected
source footprint is therefore exactly 154, not an arbitrary accepted subset.
Missing, extra, disabled or altered entries are refused. A pristine-only target
is deliberately rejected; the companion extension validator must independently
verify the receipt tables and all 44 child/parent actions. No target-discovered
rows are ignored or learned. `manifest_digest` identifies the pristine artifact;
`descriptor_digest` binds the full composed semantic contract. Admission must
retain both source observation digests and the extension observation.

The latest focused run has 45 cases: 25 extension and 20 source owner/review
cases, including unchanged independent enforcement negatives. Immutable native
PostgreSQL composition/disabled-trigger/extra-incoming/replica acceptance is still
pending; these inert descriptor tests do not replace it.

## Corrected immutable enforcement acceptance

Both source-plus-required-extensions validators pass the corrected actual PG17
acceptance: clean combined schema, disabled child/parent triggers, non-origin
replication modes and unknown incoming references. Thirty-five focused tests
passed. Earlier structural-only observations are not proof of FK enforcement.
This remains bounded profile compatibility, not runtime admission, migration or
protection against arbitrary concurrent administrator changes. See ISOLATED_QA.md.

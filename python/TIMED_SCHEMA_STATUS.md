# Standalone timed extension-schema observation

`db/timed_schema.py` implements the explicit
`validate_timed_schema(executor, expected_database=..., expected_schema='public',
profile='extension-v1')` API. Construction is unnecessary; invocation performs one
native-bound catalog SELECT. No DDL, SET, advisory/table locks, sequence calls,
provider calls or data-row reads are issued. It does not install or migrate any
object. PostgreSQL 17 only is accepted; actual PostgreSQL 17.11 acceptance and
independent review are pending. Eleven authored catalog-fixture tests currently pass; independent malformed-numeric regressions also pass after strict scalar decoding.

The five exact extension manifests come from the existing explicit installers:
bootstrap journal, final assessment, final settlement, early completion and
unstamped completion. Checks cover ordered exact columns/types/typmods/array
shape/nullability/defaults, validated immediate constraints and foreign targets,
exact primary/unique btree indexes, serial sequence ownership/default references
and parameters, and ordinary permanent nonpartitioned table identity. Extension
RLS/policies/user triggers/rules/inheritance/dropped columns are rejected. Custom
function/operator dependencies in defaults/CHECKs are rejected; expressions are
only deparsed, never evaluated. Deparser spellings are an explicit finite PG17
allowlist, not a claim of semantic equivalence for arbitrary expressions.

Referenced `public.tasks` and `public.explorations` are checked only for permanent
ordinary relation identity and an `id bigint NOT NULL` primary key backed by the
expected valid index. Their defaults, other columns, permissions, legitimate
source triggers and runtime schema are NOT certified. No graph/archive/task
lifecycle readiness or general source-schema compatibility follows from this
profile. No application setup integration or positive-timeout admission is added.

Each catalog list has a bounded row projection plus total, expression lengths
are capped, and the aggregate JSON descriptor is byte-gated before transfer.
Oversized/missing/unrecognized descriptors fail closed with categorical, redacted
errors. The observation returns the selected named DB/schema, major/profile,
manifest digest and order-independent descriptor digest; no internal OIDs,
credentials or stored task data are returned. `source_readiness` is always false.

This observation is point-in-time, not an execution capability or timeless schema
certificate. It cannot prevent later DDL or replace exact host/executor ownership,
source prerequisite validation, policy grants, canonical evidence checks or drain
protocols. Deployment must coordinate schema changes separately. The independent
review and disposable PostgreSQL tests must cover hostile DDL variants and actual
catalog/deparser output before this implementation is accepted.

Session compatibility is observed in that same catalog SELECT: every relevant
unqualified relation/sequence name must currently resolve to its checked public
object, and any same-named temporary object is refused. The bounded search_path
text is included in the descriptor, never changed. This remains an observation
of the one executor call, not a promise that a pool cannot later switch sessions
or a caller cannot alter search_path. Numeric descriptor fields reject booleans
from injected adapters rather than relying on Python equality coercion.

Initial disposable PostgreSQL17 acceptance exposed a catalog JSON representation
mismatch: aggregating `oid` collation values produced JSON strings (including
`"0"`). The projection now casts each collation OID to bigint before JSON
aggregation. The decoder remains strict and rejects string/boolean numeric
fields. This correction requires a fresh immutable real-catalog acceptance run;
the initial failed snapshot is not an accepted schema result.

The next immutable PG17 run confirmed native `connoinherit` is true for
primary/unique/foreign constraints and false for ordinary CHECK constraints.
Validation now requires that exact kind-specific shape, continuing to reject
CHECK NO INHERIT. This second failed acceptance is retained; a fresh snapshot
and real-catalog rerun are required.

## Full pristine descriptor audit after both catalog corrections

The untouched 48,169-byte PG17 descriptor captured by isolated QA from the
original schema plus all five real installers was passed through the corrected
decoder without normalization or mocked replacement fields. It contains nine
relations, 61 selected columns, 32 constraints, ten indexes and two sequences;
all validation branches pass. Exact extension defaults/types/nullability/order,
sequence parameters/ownership, index shapes/opclasses, FK targets/actions, check
expressions and flags match the manifest. Comparison with the authored fixture
also identified native CHECK column-name lists (the fixture used null); CHECK
semantics are validated by exact deparsed expression, not that redundant list.
The source task/exploration serial defaults and update triggers intentionally
differ from the minimal fixture and remain outside extension-v1 certification.
This is an offline audit of an actual catalog artifact, not a replacement for
rerunning the full immutable positive and hostile-DDL acceptance suite.

## Final immutable PostgreSQL acceptance

Stage 119 final passed three actual PG17 groups, including thirteen hostile DDL
variants and two session-resolution variants, plus nineteen focused tests. The
clean original-plus-extension baseline passed before negative mutations; one
read and no sequence advancement verified. Earlier failed snapshots remain
separate in ISOLATED_QA.md. Extension observation still reports no source readiness.

## Constraint enforcement correction (acceptance pending)

Isolated QA preserved an actual counterexample against the prior module: disabling
internal FK triggers on `artex_final_settlement` left its validated constraints
unchanged, the observation accepted, and an orphan insert succeeded in a rolled
back fixture transaction. Constraint catalog presence alone did not establish
FK enforcement. The corrected snapshot includes all internal triggers belonging
to the eleven extension FKs, including their parent-side actions on source tables.
The expected 44 entries compare exact origin-enabled state, event type, built-in
RI function, deferrability, empty arguments/condition, and stable FK source/target
column/action identity. Generated RI trigger names and OIDs are excluded.
Session replication role must also be origin; no SET is issued. Twenty focused
unit/review tests pass, but the original failure is not reclassified: fresh
immutable actual PG positive/disabled-trigger/replica tests remain required.

The corrected trigger query also includes every internal trigger physically on
an extension relation, even when its FK originates outside the five tables.
Thus unexpected incoming FK parent actions fail the exact footprint comparison;
unrelated incoming FKs on base tasks/explorations are not pulled into extension-v1.
The required parent-side actions of extension-owned FKs remain included.

## Corrected immutable enforcement acceptance

Both source-plus-required-extensions validators pass the corrected actual PG17
acceptance: clean combined schema, disabled child/parent triggers, non-origin
replication modes and unknown incoming references. Thirty-five focused tests
passed. Earlier structural-only observations are not proof of FK enforcement.
This remains bounded profile compatibility, not runtime admission, migration or
protection against arbitrary concurrent administrator changes. See ISOLATED_QA.md.

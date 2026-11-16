# Bounded read-only startup inventory diagnostics

Explicit StartupInventoryReader(executor,InventoryScope(database,'public'),
authorize=...,current=...,lifecycle_guard=...,max_references=500).read() performs
one SELECT. No HTTP route, launcher, schema installation, advisory/row lock,
transaction-setting mutation, provider call or transcript access is introduced.
The caller supplies independent namespace read/lifetime authority; a DB connection
is not authorization. Fixed qualified public relation names and exact current_database()
matching avoid search_path/DSN aliases being treated as namespace identity proof.
This does not authenticate schema definitions or prove database exclusivity.
All results explicitly carry visibility=database_role_visible_rows. RLS, views,
privileges or concurrent writers may hide work; no complete/unfiltered inventory
or schema-proof claim. Compatible views are not distinguished from tables.

Observed categories: running/paused/queued tasks (including deleted/archived rows
rather than hiding them), every bootstrap receipt including prepared, intent nodes
with running/paused state or nonempty owner (including orphan claims), and every
archive row including ready/failed states. Invalid task/intent state observations
refuse unavailable. No payload/owner token/name/prompt/goal/credential/error text
is projected: only category, task/entity IDs and fixed known state labels.

The count is category-reference count, not distinct task count; one task may
appear in several categories. One statement provides a PostgreSQL statement
snapshot. SQL globally counts before bounded output; overflow refuses without
partial references. Default max500/cap1000 references, fixed field sizes and
64-byte state sentinel bound transfer. This does not bound server execution cost.
Database mismatch, missing required relations/columns or query-incompatible schema, malformed values or failed
queries return unavailable with total=None and no references. Known rows return
observed_records. Empty returns observed_no_visible_known_work, NOT safe/clean/ready.

ownership_proven, launch_authorized and recovery_supported are immutable init=False
fields unconditionally false in every returned DTO. No locks or recovery authority
can be derived from a report. Empty means only no matching rows visible to this database role. It does not
establish absence of other tasks,
processes, external effects, unmodeled journals or transcript state. Prepared and
ready archive records are conservatively reported, not automatic launch blockers
or cleanup authorization.

Source grant precedes SQL. Current/guard callbacks and guard exit precede final
current and raw source-authority sweep, including unavailable/error paths. Denial
raises generic PermissionError (callback exception messages are suppressed) rather than exposing detailed diagnostics. Finite
cooperative observer order is not arbitrary policy fixed-point or atomic runtime
ownership. The read snapshot may be stale immediately; final authority is not a
second database inventory.

Eleven tests cover defined categories/orphan claims, overlapping task references,
empty/non-authority flags, bounded/malformed/schema/identity failures, initial and
late authority including errors/guard exit, binding mutation, failed guard-exit snapshot discard and source-query
conservatism. Latest independent review cleared corrected reader bae1423d:11 unit tests plus12
additional empty/populated guard-entry/exit and final-reauthorization variants
passed. Guard failures now discard decoded snapshots; final revoked/stale/throwing
policy emits only fixed denial. Earlier callback-error/deleted-state and guard-exit
findings are retained, not represented as absent from earlier candidates.

Scoped PostgreSQL QA209 PASS:
`/workspace/shared/artex-qa/startup-inventory-209/REPORT.md`
Manifest `2388dfe8f5944df414f61dd49745a91960b2549cf34d3d58e00d95703df4eae5`.
Frozen index plus diagnostic/test overlays; all entries verified, source matches
bae1423de3523c4765e6df90892f963fa8f09a4a1072d9b71c6af1f1b55df5be.
QA207's required-goal fixture failure and208's actual guard-exit defect remain
preserved as nonfinal evidence.

Disposable PostgreSQL17 used original schema plus bootstrap journal installed by
test setup only. Reader connection had default_transaction_read_only=on; a direct
test UPDATE was refused by the server. Reader made one WITH/SELECT per permitted
observation and zero on initial denial. No explicit row/advisory locks were added;
normal PostgreSQL read/table locks still exist. Table JSON digests remained
unchanged around reader/callback cases; fixture schema/role writes were separate.

21 recorded cases plus missing-schema/native-readonly checks passed. Actual seven-
reference data covered deleted/archived flagged tasks, prepared bootstrap, owned-
open/orphan-paused intents and ready archive; running/unresolved bootstrap also
observed. Overflow, wrong database, missing bootstrap table and guard failures
returned unavailable without partial references. Deleted unowned intents did not
poison reads; residual owners remained visible. Initial/postSQL/guard-exit/final-
current grant revocations and secret-bearing callback exceptions failed closed.

An actual nonowner role with deny-all RLS saw explicitly visible-only empty; owner
role saw records. A compatible filtering tasks view yielded a visible subset.
These tests affirm the limitation, not RLS bypass, authenticated schema or complete
inventory. Every result retains visibility=database_role_visible_rows and all three
authority flags false. No concurrent-write snapshot race was run; statement
consistency follows the single-query design and PostgreSQL semantics, not a
runtime ownership fence.

11 focused unit tests passed, not the full suite. No provider/runtime/transcript
launch by the reader. Diagnostic acceptance is not operational startup approval.
No worktree staging or publication.

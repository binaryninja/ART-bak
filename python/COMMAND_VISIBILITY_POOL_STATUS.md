# Optional inspection and policy metadata adapters

Command history uses existing PostgreSQL activity records, never a shell or
execution facility. The source `task` query denotes an exploration ID, and raw
inputs cover all tools, not just shell commands. Separate raw-content authority
is required. Result pairing is deliberately stricter than source: exploration,
worker, node and main-session segment must match, and ambiguous repeated call IDs
or duplicate results are rejected. Filters/counts, archive checks and bounded
raw projections share the relevant SQL snapshot. No historical command executes.

Visibility metadata preserves source duplicate IDs, default-false toggles,
MCP all-tool-row disable and last-write-wins semantics. These rows can influence
available MCP/skill selection upstream, but are not execution approval. The
Python adapter does not enable a runtime catalog, seed defaults or broaden tool
grants. Current scope/guard/resource pins and explicit mutation permission remain
required. Unsupported skill-name forms and source orphans are narrower subsets.

Pool-health/retry adapters use actual Registry and Settings objects. Validated
Unix timestamps become source-shaped UTC wire times; zero dates are omitted.
Raw provider diagnostics and secret-bearing profile fields are not exposed.
Retry-setting and single-profile-reset mutations require immutable approval,
idle ownership and an exact-once coordinator that completes the actual effect
and final authority checks. Merely invoking a failed callback cannot report
success. Partial/uncertain effects do not automatically retry. Reset-all is
unavailable in this checkpoint; no provider requests or implicit profiles occur.

Every service is explicit opt-in. Application factories validate retained
resource identities before startup reads and supply no missing permissions.
See ISOLATED_QA.md for scoped evidence, and integration/README.md for a reusable
fresh-local-database acceptance runner. The runner is outside normal unit-test
discovery and refuses populated databases and optimized Python.

Frozen checkpoint: 2,137 authored tests pass; 419 captured SQL statements parse.
Static registrations are 170/261 (91 absent), including optional/unavailable
contracts, not completed-feature or production-readiness counts.

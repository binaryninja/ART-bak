# Scoped planner/worker, graph tools and context checkpoint

These components extend the Python port but are not a complete autonomous engine.

- Planner cycles create scoped, transactional intents with durable replay identity.
  Exact repeated wakes reuse their persisted outcome; changed configuration does
  not silently reuse a prior turn. Fresh cycles use fresh working context while
  raw transcript history remains durable. Model claims never prove a goal.
- Intent workers CAS-claim a specific content version, run an explicitly supplied
  registry and settle only after durable transcript completion. Cancellation
  reasons map to distinct intent states. Ambiguous final SQL persistence remains
  unresolved and is not automatically retried.
- Local graph tools record facts and evidence-linked goal proofs under explicit
  scope and grants. Batches are atomic; inherited-union writes are unavailable.
  Evidence lineage is checked, not the semantic truth of model-written evidence.
- Read-only graph context and cold/hot grouping expose bounded local graph views.
  Parent SHARE locks require a writable transaction, not a read-only replica.
  They do not persist compaction decisions or supply a full inherited graph.
- Optional chat compaction keeps raw history unchanged, charges observed summary
  usage to the run budget, and rechecks generation/provider authority before
  summary and main requests. It does not implement all Norma context management.
- Explicit provider recording binds a host-attested immutable scope to the actual
  provider, requires redaction and persists known usage with normalized records.
  Persistence uncertainty is not permission to retry a provider call.
- English editable prompt defaults and a non-executing template subset preserve
  source roles, generic chat fallback and code-owned policy tail responsibilities.
  Unsupported template constructs fail explicitly rather than execute code.

Original-Go orchestration, automatic task creation/bootstrap, distributed leases,
restart reconciliation, complete native tools/routes and remaining translation
are not complete. New engine work must be reviewed and tested independently of
these cycle primitives. See ORCHESTRATION_NEXT.md and ISOLATED_QA.md.

Frozen checkpoint verification: 1,467 authored tests pass; 334 captured SQL
statements parse. Static method/path coverage remains 116/261 (145 unregistered),
not a count of complete or production-ready features.

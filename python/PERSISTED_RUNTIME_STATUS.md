# Parent-scoped runtime persistence and profile assembly

This checkpoint connects additional adapters without enabling automatic execution.

- Provider profile assembly caches validated private configurations, assembles
  pools over an explicit transport factory and exposes safe runtime snapshots.
  Save/activation preflight preserves blank keys and distinguishes durable changes
  from failed runtime application. Already handed-out providers need independent
  drain. This subset rejects unsupported features and requires retry disabled;
  full source-compatible retry integration is subsequent work.
- Side-question persistence enforces canonical parent ownership, snapshot ordering,
  idempotent admission, bounded history/replay and generation/sequence guarded
  writes. Clear is a permanent deletion operation requiring host authorization;
  no real user history was cleared. Runtime capture freshness and archive
  export/restore integration remain separate.
- Worker persistence wraps intent nodes, not an invented workers table. It requires
  an explicit lifetime guard and task/exploration scope, with claim/state version
  checks and injected transcript backend. Existing generic graph writers do not
  share these generation/lifetime guarantees. Backend transcript appends are not
  atomic with database commit and require idempotency/reconciliation.
- Protected company HTTP supports list/detail/empty-scope creation. Scope changes,
  reattribution and delete fail before writes until full recomputation is ported.
  The persisted factory explicitly supplies administrator company scope, matching
  the single-admin authentication model. No registry lookups are performed.

Independent review covers credentials, parent scope, CAS ordering and unsupported
boundaries. None of these additions proves full planner/worker, archive, provider
or frontend compatibility. See review notes and isolated QA evidence.

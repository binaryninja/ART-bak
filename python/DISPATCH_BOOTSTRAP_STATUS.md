# Dispatch, dashboard and fresh-schema checkpoint

- Side-question HTTP now supports explicit prepared submission and bounded
  dispatch as well as scoped history/events. Original questions remain separate
  from prepared model context. Cancel/clear require lifecycle adapters; no parent
  store, provider, thread pool or authorization is silently inferred by factories.
- Dispatcher ownership is bound atomically to the exact coordinator and handle.
  A reviewed foreign-owner admission defect was fixed before publication. Rejected
  queue submissions durably terminate unstarted work or retain unresolved ownership
  for recovery; shutdown reports actual live workers, not pretend cancellation.
- Dashboard aggregates require an explicit live-runtime snapshot. Archived/deleted
  task mappings fail before pruned-data aggregates. Metadata PATCH and batch control
  retain source validation and per-item durable-success semantics.
- English planner/goals/Auto prompt builders preserve scope, consent, evidence and
  probe limits. They are text builders, not an execution or permission bypass.
- Task-scoped archive job persistence includes guarded queue/claim/progress/failure
  states. It cannot publish packages, declare ready, restore or delete. Timestamp
  CAS is not a distributed lease; real lifecycle drain remains host-owned.
- Fresh schema bootstrap is explicit and fresh-database-only. It checks identity,
  emptiness, transaction conditions and advisory ownership before applying the
  packaged 49-table schema atomically. SQL comments are English; four legacy values
  are preserved using Unicode escapes. Existing databases, upgrades, database
  creation, built-in seeding and automatic application migrations are unsupported.

The bootstrap lower-version guard is not a claim of tested compatibility with
all PostgreSQL releases. Consult ISOLATED_QA.md for actual versions exercised.
Full production lifecycle, archive/recovery, concurrent hosting, tool orchestration
and frontend compatibility remain incomplete.

Frontend contract review found that authenticated EventSource URLs include a
reserved `token` query parameter. Log and side-question business query parsers
now accept that parameter after Router authentication, while rejecting duplicates
and unknown parameters. Four actual query-JWT/WSGI regressions verify delivery,
rejection before callbacks, cleanup and absence of token reflection in payloads.

This checkpoint has 915 authored offline tests and 229 parsed generated query
statements, plus the complete 285-statement packaged schema. It registers 71 of
261 upstream method/path routes (190 unregistered); some registered capabilities
still require adapters or explicitly return unavailable.

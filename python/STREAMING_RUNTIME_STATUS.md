# Streaming, retries and side-question runtime checkpoint

- Explicit bounded WSGI streaming and SSE framing preserve authentication/header
  validation and iterator cleanup. Protected log SSE replays the last 1000 lines,
  deduplicates subscribed events, bounds each subscription to 256 entries and
  sends 20-second heartbeats. There is no automatic log capture or semantic secret
  redaction. Gateways must close iterators on disconnect; failed streams end at
  EOF rather than leaking errors. Consumers must require protocol terminal success.
- The loopback development CLI remains single-threaded and is not a concurrent
  streaming deployment. Production serving requires an appropriate gateway and
  database connection ownership; these are not supplied by this checkpoint.
- Source-pinned profile retries now support normal default settings and explicit
  overrides with bounded aggregate attempts/time. Every delivered event closes
  the retry window. A reviewed buffered-refusal replay defect was fixed before
  publication; refusal remains sticky even if later metadata changes stop reason.
- Side-question runtime connects scoped snapshots/replay/memory to explicit caller
  execution, without spawning threads or constructing a provider. Profile binding
  is revalidated before every provider invocation, not just admission. Reviewed
  stale-provider reuse was fixed. Generation guards prevent cleared-history
  resurrection; partial answers/usage and persistence errors remain explicit.
- Task-template persistence and four protected routes support atomic patches and
  source rule/category semantics. Delete permanently removes the preset only;
  no real user preset was deleted in development tests. Factories perform no
  template reads or task execution during construction.
- English main/worker prompt builders retain formal-goal consent, scope and
  evidence rules with strict substitution and structured-context escaping.
  Prompt text is not an authorization boundary: code-owned gates remain mandatory.
  Planner/other builders and full orchestration remain separate work.

These additions are incremental. Full agent/tool orchestration, archive/recovery,
concurrent deployment, API coverage and frontend interoperability remain incomplete.

Immutable offline verification: 813 tests pass; 212 distinct generated SQL
statements parse. There are 57 registrations against 261 Go registrations
(204 unregistered), including explicit unavailable paths. This is not functional
or production parity.

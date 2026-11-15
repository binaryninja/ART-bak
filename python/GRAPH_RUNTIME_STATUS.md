# Scoped graph/assets and gated runtime checkpoint

**473 offline authored tests pass.** The optional SQL QA runner parsed **104
unique PostgreSQL statements constructed by those tests**, with zero syntax
failures. This is not schema, transaction, driver or live-server verification.

## Added slices

- Scoped exploration/node/edge/anchor persistence, payload/state updates,
  intent compare-and-swap/claim and read-only direct-source inheritance.
  Cross-exploration writes and inherited writes fail closed. Destructive graph
  deletion, evidence/digest/cancellation cleanup and execution fences are absent.
- Asset-store queries/counts/provenance and root-domain/IP upserts, with required
  transaction, company resolver and mutation guard. Global reads require explicit
  configuration. Other upsert types, arbitrary updates and deletion are unported.
- Four authenticated asset registrations. DELETE is only an explicit unsupported
  503 handler, not an implemented deletion operation. Malformed/duplicate/null
  scope selectors fail before writes rather than widening to global access.
- Parameter-bound task metrics/archive blockers and a real DTO-enricher adapter.
  The persisted factory can now compose stores, metrics and manager, loading
  existing tasks without resuming them. Metrics failures do not fabricate zeros.
- A bounded synchronous provider/tool loop with mandatory permission, scope and
  approval grants before every model/tool invocation. No built-in tools/network,
  discovery, bypass mode, retry after refusal or automatic TaskManager binding.
  Time limits require cooperative callbacks; reported token usage is not a hard
  billing cap. Production runner/thread ownership remains missing.
- Explicit development API CLI: `python -m artex --check-config` or `--serve`.
  It requires externally supplied `ARTEX_PG_DSN` and `ARTEX_SIGNING_KEY_HEX` (at
  least 32 decoded bytes). It never generates/stores/prints credentials. Serving
  is loopback IPv4 only, default port 8787, with no frontend, task runner, schema
  migrations or automatic task resumption. It is not a production server.

## Independent regression review

Review found and fixed pre-tool validation gaps for malformed assistant blocks,
unknown/inconsistent stop reasons, and lost provider refusal signals. The complete
assistant payload is now validated before callbacks; mixed refusal/tool outputs
cannot execute tools. Review also found duplicate/null JSON task scope ambiguity;
these inputs now fail before mutation. All reproductions use inert callbacks and
fake transports/executors, not real tools or targets.

One inherited source behavior remains: the scope CTE can reference live source
tasks for a deleted owner task. Deletion/revocation integration must resolve this
before claiming lifecycle-wide scope isolation. No live PostgreSQL lock tests
were performed. Snapshot metrics currently use per-DTO queries (N+1), with a bulk
query API available for subsequent manager optimization.

## Remaining scope

21 route registrations against 261 Go method/path registrations; 240 are still
unregistered. Registered handlers are not equivalent to operational capability,
especially explicit unsupported paths and services with missing adapters. Full
planner/goals/workers orchestration, production transport/policy bindings,
scheduler/recovery, graph/finding APIs, original Go differential tests and live
frontend/database/cryptographic interoperability remain incomplete.

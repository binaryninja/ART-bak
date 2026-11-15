# Evidence port integration contract

This module ports `evidence/store.go` plus the traffic-reference normalization and
snapshot-ID primitives from `db/finding_traffic.go`. It uses only the standard
library. It does not open PostgreSQL or execute network requests.

The `Store` accepts a database adapter with `with_evidence_tx(callback)`. It must
begin a database transaction, acquire PostgreSQL transaction advisory lock
**7337741004**, call `callback(tx)`, commit on success, and roll back on exception.
That lock coordinates filesystem changes with all other application processes;
a Python mutex is not an adequate substitute. Metadata methods fail explicitly
when no adapter is configured. `write_body` is public for testing but production
callers must keep writes under the same evidence transaction.

The transaction adapter supplies:

- `lock_task_evidence(task_id)` and `lock_finding_evidence(finding_id, version)`:
  enforce the task archive/deletion and evidence-version constraints in Go.
- `record_finding(input, prepared)`, `add_finding_traffic(id, prepared)`,
  `finding_traffic(id)`, and `finding_report(id)`: retain the database port's
  binding deduplication, ordering, evidence versions, and report behavior.
- `collect_evidence_references(now, cutoff)`: timestamp newly unreferenced
  snapshots at `now`, delete snapshots whose unreferenced timestamp is strictly
  before `cutoff` and still have no bindings, then return request and response
  hashes from **all remaining snapshots**. Inputs are UTC Unix seconds.

The traffic adapter supplies `read_evidence(ids, callback)`, invoking the callback
with exchange objects containing `id`, `ts`, `request` and `response` binary
readers plus the corresponding snapshot metadata. It must reject missing traffic
IDs or bodies instead of silently returning fewer exchanges.

`binding()` releases the transaction before returning. Use it rather than
`with_binding()` for downloads so slow clients do not hold the global lock.
`with_installed_snapshots()` keeps the lock through its restore callback; that
callback must not acquire a second evidence transaction.

The durable-write, binary-body verification, copied-not-linked export,
metadata validation, 24-hour blob grace period, and hash normalization paths are
unit tested. PostgreSQL integration and actual traffic-adapter wiring remain
integration work, not completed parity. Go cancellation contexts are represented
by adapter exceptions; periodic GC receives a threading.Event-like stop event.

# Traffic Python port status

Implemented offline only, using Python standard library:

- Compatible `_index/index.sqlite` tables, WAL and busy timeout.
- Explicit already-captured exchange ingestion, metadata-only search and filtered
  pagination, parameterized host/port/body searches, status buckets, sort whitelist.
- Inline/body split at 256 KiB, SHA-256 deduplication, 8 KiB UTF-8/binary previews,
  4 MiB text indexing cap, legacy two-level blob lookup and ranged reads.
- Raw capture reconstruction and callback-scoped streaming evidence readers.
- Exact-host archive v1 export (Go-compatible base64 byte fields) and import with
  checksums, missing-ID-only semantics and transaction rollback.
- Legacy capture reads reject paths outside the traffic root (hardening compared
  with upstream Get/ExportHosts; upstream ReadEvidence already validates paths).

Not a complete Go package replacement: no MITM listener, CA, upstream proxy
transport, connection passthrough/upgrade handlers, agent-tool SDK adapters,
staged deletion journal/recovery, blob GC or background vacuum/reclamation.
Deletion APIs are deliberately absent rather than falsely promising safe parity.
The record API consumes supplied captures, not live proxy Flow objects. Failed
blob writes raise rather than silently retaining only a preview as Go does.

FTS uses the exact upstream trigram/contentless_delete schema. When the Python
SQLite build lacks contentless_delete, FTS is disabled as in the upstream fallback;
body query reports a clear error, while Page skips unsupported body filters.
The current runtime (SQLite 3.53.1) exercised FTS successfully; tests also
exercise explicit disabled-FTS behavior.
Archive restoration indexes only inline previews, matching upstream behavior.
No Go program or target requests were executed. Tests use temporary local stores.

# Pure finding/evidence export layout

`artex.report` already ports `report/report.go` and `report/findings.go`: English
Markdown, CSV, filenames, stable severity ordering and linked evidence descriptions.
The new `artex.report_export.findings_evidence_zip` covers only the pure archive
layout from `server/finding_traffic_export.go:writeFindingsZipEntries`.

## Inputs and source semantics

Explicit finding DTOs and immutable `ExportEvidence` body/manifest bytes must
already be authorized. There are no callbacks, file reads/writes, database calls,
HTTP routes, evidence-store lookups, archive extraction or publication. The
function returns bytes; it does not claim that an authorized download exists.

Each finding receives its existing Markdown filename and text. Each binding gets
`evidence/<finding-id>/<binding-id>/manifest.json`, `request.http`, `response.http`,
`request.bin` and `response.bin`. Caller finding/binding order is preserved, as in
Go's ZIP writer, not replaced with summary report severity sorting. Manifest
binding/finding IDs remain exact decimal strings. Raw binary bodies are unchanged;
HTTP exports strip trailing CR/LF from the stored head, append two LF bytes and
then the body, matching source. These are evidence files, not transmitted HTTP
messages, so their stored header text is not used to perform requests.

Manifest body hashes/lengths, snapshot/binding identities and report-linked
method/URL/status/role/note must agree. These consistency checks do not establish
provenance, authenticity or access permission. Missing/extra evidence refuses
rather than generating dangling attachment links.

## Deliberate differences and limits

The builder uses deterministic fixed ZIP timestamps and stored entries instead of
Go's compressed output. It verifies all member CRCs before returning bytes.
Finding/binding duplicates and path collisions are rejected rather than Go's
numeric suffix strategy. Existing Python filename UTF-8/ID-preservation and CSV
hardening are unchanged. Numeric IDs must be positive int64, not booleans.

Defaults: 16 MiB total input/output budget and 10,000 archive entries; explicit
limits cannot exceed 64 MiB/10,000. Metadata JSON additionally has a 1 MiB limit,
strict duplicate-key rejection and no nonfinite constants. ZIP envelope overhead
counts toward the final output cap. Oversized/partial/mismatched inputs fail;
there is no partial archive publication or retry. Empty input yields an empty ZIP.

This is **not an HTML renderer or sanitizer**. Existing freeform report Markdown,
including raw HTML, remains text. A consumer rendering it as HTML must sanitize
HTML and unsafe link schemes. Evidence URLs stay inline text and manifest data;
the builder never fetches them. It creates only internal numeric evidence links.

## Evidence

Six new pure tests plus nine existing report tests pass: exact layout, binary/head
serialization, repeatable bytes, string identities, missing/duplicate/foreign
bindings, truncated or hash-mismatched bodies, duplicate JSON, row/byte limits,
safe filenames with preserved IDs, empty output and caller ordering. All tests
operate in memory. Independent review is pending; no external files, database,
HTTP endpoint or live provider was exercised.

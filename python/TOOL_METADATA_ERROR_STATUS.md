# Tool metadata error classification

The existing `ToolCatalogService` no longer labels generic internal exceptions
as invalid client metadata. `ValueError`, `UnicodeError`, `TypeError` and
`RecursionError` are converted to the explicit `ToolInvalid` domain error only
while decoding the bounded UTF-8 JSON request. The same generic exceptions from
store calls, DTO construction or response serialization now remain redacted 500.

Explicit `ToolInvalid` validation still returns 400. Existing 401 authentication,
413 body bound, 404 missing, 409 conflict and 503 unavailable handling are unchanged.
The existing description validator converts a failed UTF-8 encoding to that
typed error locally: JSON-escaped lone surrogates remain pre-mutation 400 rather
than becoming internal failures. Valid Unicode and the 65,536-byte description
bound are unchanged. Generic post-store UnicodeError still remains 500.
The original store validates input before writing, or raises a typed validation
error inside its transaction before rollback. This change does not reclassify all
possible exceptions from arbitrary injected adapters or add a transaction outcome
protocol; adapters must still honor their existing typed-error contracts.

An internal failure can happen before a write, during a transaction, or after
storage has returned. A 500 does not prove either commit or rollback. No retry,
reconciliation, idempotency key, execution, new route, SQL/schema change, host
capability, client behavior or error-detail disclosure is added. A caller must
not infer that retrying an uncertain mutation is safe merely from this status.
Tool configuration remains inert metadata and is not runtime authorization.

## Source contract

The Go source separates malformed input from storage errors:

- `server/server_mgmt.go:pgUpdateTool` returns 400 for decode failure and 500 for
  `GetTool`/`UpdateTool` errors.
- `server/customtool.go:pgCreateCustomTool`, `pgUpdateCustomTool` and
  `pgDeleteCustomTool` return 500 when their storage mutation returns an error.

The Python implementation keeps its stricter, explicit validation and redacted
English error messages. No claim is made that Python exceptions correspond
one-to-one to Go errors, or that the Go handlers were executed in these tests.

## Verification boundary

`test_tool_metadata_errors.py` uses the original JWT router and WSGI transport,
plus the original ToolCatalogStore over the existing inert queued-SQL fixture.
Tests cover malformed/bounded input before any query, typed validation before
INSERT, internal failures before a call, transaction-error paths, generic failures
after the original mutation returns, receipt/DTO failures, exact success receipts,
and no repeated mutation call. A test fixture's recorded `commit`/`rollback` event
is not live PostgreSQL or durability evidence. No tool is loaded or executed.

Full-suite and independent ordinary correctness-review results are recorded
against the immutable candidate outside this source document before publication.

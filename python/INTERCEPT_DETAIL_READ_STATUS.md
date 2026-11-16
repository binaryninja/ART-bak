# Standalone intercept history detail read

New `db/intercept_detail_read.py` and `server/intercept_detail_read.py` implement
only the source GET `/api/intercept/history/{id}` projection. Nothing is registered
in an application, no matcher/approval is activated, and execution navigation is
not implemented. Eight synthetic owner tests pass; independent review and actual
PostgreSQL acceptance remain pending.

Source: `server/intercept.go:interceptDetail`, `db/intercept_detail.go:GetInterceptDetail`,
and `db/intercept.go:approvalRowSelectWithAudit`. The original approval ID, nullable
rule/conversation/task IDs, raw tool input/reason, conversation labels, fallback
decision source and recorded audit are retained. No channel/config/credential
joins or reconstructed audit data are introduced. Old missing or JSON-null audit
stays null. Known audit fields use source defaults/omitempty, context remains null
when missing, and model_input remains a raw JSON value. Unknown audit fields are
ignored as in the Go struct decoder. Audit is recorded context, not hidden model
reasoning or proof of execution.

An explicit workspace scope optionally restricts the exact TEXT task key. It does
not infer a numeric task or use an active-task fallback. Separate record-specific
read and raw-diagnostic grants receive the original request, scope and ID, before
SQL and after reading, serialization and guard exit. Original callback/resource
identities are pinned. JWT authorization must be supplied by eventual routing;
this service does not replace it. Policies must remain consistent under the host
lifetime guard; finite repeated checks do not solve arbitrary callback fixed points.

One parameter-bound SQL query caps total text plus audit bytes (default/max 1 MiB)
before payload transfer using a sentinel, rather than truncating raw evidence.
Returned IDs/task scope and scalar/date fields are validated. Parsed JSON is capped at depth 64 and 20,000 values, with finite numbers.
Context is capped at 2,000 entries; malformed/oversized rows fail generically without raw payload in
errors. JSON parsing/serialization failures also fail closed. Body/query parameters,
noncanonical or encoded IDs and paths outside this exact read are rejected, a
stricter subset than generic Go routing. Missing records return 404. Database
errors are redacted rather than copying the Go error text. No files, providers,
actual user logs or database mutations are accessed by these tests.

Independent review follow-up: known audit and context struct fields now accept
case-insensitive JSON spellings as Go does, without folding raw model_input keys.
All decoded string keys and values must encode as strict UTF-8 before disclosure.
An injected adapter returning escaped unpaired surrogates is rejected rather than
Go's replacement-character normalization; this is intentional boundary hardening.
PostgreSQL JSONB already rejects such unpaired surrogates.

Audit/context objects with colliding case-insensitive aliases are rejected as a
stricter subset, including when both aliases contain valid values. This prevents
a later alias from hiding an earlier known-field type error. Source
GetInterceptDetail returns json.Unmarshal errors, and interceptDetail maps them
to HTTP 500; the Python handler likewise returns a redacted 500, never a partial
record. Raw model_input objects are not case-folded or subject to alias rejection.

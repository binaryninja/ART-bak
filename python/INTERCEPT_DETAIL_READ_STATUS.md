# Standalone intercept history detail read

New `db/intercept_detail_read.py` and `server/intercept_detail_read.py` implement
only the source GET `/api/intercept/history/{id}` projection. Nothing is registered
in an application, no matcher/approval is activated, and execution navigation is
not implemented by the standalone service. Eight synthetic owner tests plus six
independent review tests pass. Standalone PostgreSQL stage 107 passed four groups
and 21 focused tests, frozen manifest
`372dad512810e89b74d1bab677e5a28655b6cf4f9f0f0499e989393918bbca4a`
(see ISOLATED_QA.md). Optional composition acceptance is separately recorded below.

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

## Explicit optional authenticated composition

`bind_metadata_reads(..., detail_service=...)` now optionally adds the protected
GET `/api/intercept/history/{id}` overlay. Default omission adds no detail route.
Construction requires the exact original WSGI/Router/AuthService and exact reader
and service types, pins methods/resources, and performs no SQL or grants. It does
not stack onto arbitrary dynamic overlays. Existing literal or parameter-template
GET/HEAD routes intersecting the detail path family are rejected before mutation;
this conservative conflict rule can reject noncanonical literal IDs too.

A shared pure parser selects the canonical positive int64 ID once for wrapper
grants; the service verifies it against the original request and reuses that same
selection. Scope, original JWT, independent record/raw grants and current policy
are checked through serialization and after start_response. Revocation after
headers aborts body delivery instead of fabricating a new status. HEAD is not
silently widened, mutations are not registered, and `/execution` remains outside
this overlay. Original application behavior outside the selected family remains.

Five authored and four independent composition tests pass. PostgreSQL stage 110
passed four actual database/JWT groups and nine focused composition tests; see
ISOLATED_QA.md. This does
not establish browser/runtime activation or permission to execute a reviewed tool.

Composition conflict matching includes Router-supported mixed literal/parameter
segments, not only whole-segment parameters. Uncertain fourth-segment overlap is
conservatively refused, without modifying original routes.

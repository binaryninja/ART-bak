# Optional token reads, upload and frontend CAS adaptation

TokenUsageStore reads completed source activity records. It does not invent a
billing ledger or prices. Daily UTC rolling-cutoff summaries, conversation
creation dates/current nullable profile, and exact exploration session/intent
joins preserve source semantics. Counts remain integral. Explicit task/global
scope and read policies are required; archived contexts fail closed rather than
silently undercounting. The daily archive probe and aggregate share one SQL
snapshot, including an empty-result sentinel. There is no implicit active task.

Single-file workspace upload is separately opt-in. Strict bounded multipart
parsing preserves binary bytes, rejects malformed headers/duplicate metadata and
uses fresh immutable destination/content-hash/size/version approval. Existing
no-follow atomic replacement is reused; old versions are rechecked after policy
callbacks. Unknown effects do not retry. No directory creation, extraction or
source multi-file batch parity is implied. The global request-body cap is not
expanded automatically; the smaller configured cap wins.

Frontend intercept update/toggle/delete calls now send the original updated_at
string, preserving microsecond precision. Editing captures the original version,
not a freshly loaded replacement. Conflicts refresh displayed rows without
replaying mutations or adopting a new edit timestamp; users must reopen/review
changed rules. Creation does not need a CAS header. The helper checks shape only;
backend ISO/calendar validation remains authoritative. Static parsing and pure
helper/source-contract tests are not frontend build or browser verification.

See ISOLATED_QA.md for scoped PostgreSQL, temporary-file and extracted-helper
evidence. No actual user traffic/files, provider calls or default enabling.

Frozen checkpoint: 2,074 authored tests pass and 406 captured SQL statements
parse. Static registrations are 160/261 (101 absent), including optional and
unavailable contracts, not completed-feature or readiness counts.

# Fixed-task finding traffic metadata list

Standalone `db/finding_traffic_read.py` and `server/finding_traffic_read.py` port
the list shape from Go `getFindingTraffic` / `FindingTrafficTx`: standalone
`findings.id` (not exploration node ID), evidence/report versions, bindings
ordered by position and ID, and snapshot metadata with headers omitted.

The exact immutable scope fixes task, exploration, finding and revision. The
parent join requires a local finding node in that task/exploration and rejects
deleted/archived tasks or any archive record. No global/null-task, inherited or
active-task fallback. Optional `context_task` must equal that fixed task. Empty
bindings are a normal successful response. This is deliberately narrower than
Go's global/inherited read behavior.

The original evidence transaction advisory lock 7337741004 and parent row locks
are used with an explicit executor. All evidence writers must honor the same
lock; no cross-process mutation or hostile database guarantee is implied.
Independent metadata/current and request grants are checked after callbacks,
transaction/guard exit and serialization. The original scope/resources/selector
and headers remain pinned. These are finite cooperative fences, not a fixed point
for arbitrarily mutually revoking policy callbacks. No WSGI overlay is wired and
no post-header transport guarantee is claimed by this standalone service.

Limits: at most 1,000 bindings and 2 MiB total metadata response. SQL bounds
secret-bearing text before transfer with an aggregate byte sentinel; overflow
fails closed rather than silently truncating. Exact integer/hash/reference and
ordered unique binding decoding is enforced. Explicit columns never read request
or response headers/bodies, and do not load full snapshot JSON. URLs, notes and
hash references still require metadata authorization.

Lowercase 64-hex hashes are validated as references only. Neither snapshot hash
recomputation (which needs omitted headers) nor blob hash/length verification is
claimed. Existing evidence file-open code is unchanged. There are no file writes,
proxy/capture/network calls, mutation routes, tool catalog seeds or default route
registration. Detail, previews/downloads and binding mutation remain separate
work, as does a complete PostgreSQL adapter for the general evidence Store.

Eight authored inert-executor tests pass. Independent review and immutable actual
PostgreSQL acceptance are required before publication.

Independent source-parity regression preserves the original report regeneration
sentinel: `report_version` accepts exact integer -1; `version` remains nonnegative.
The owner plus unchanged independent review suite passes 15 tests after this fix.
Independent re-review and actual PostgreSQL acceptance remain separate requirements.

## Isolated acceptance

Stage 136 passed actual disposable PostgreSQL acceptance for this narrowly
supported opt-in slice. See ISOLATED_QA.md for immutable manifest, exact cases,
prior corrections and remaining limitations. This does not enable defaults or
establish full Go parity.

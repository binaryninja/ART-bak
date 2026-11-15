# Optional offline traffic reads and rule metadata

TrafficReadService provides optional source-shaped list, hosts and exchange
reads using an explicitly supplied existing SQLite Traffic store. Its scope is
an entire fixed workspace, not a task: the capture schema has no task ownership
column. Separate host grants cover metadata and raw exchange data, which may
contain secrets. Authentication alone is not a workspace read grant.

The service pins store/database/lock/root identity and rechecks authority after
reads. Conservative SQL row/byte preflights bound transfers, including SQLite
TEXT in BLOB-affinity fields; escaped response size is checked separately.
Oversized input fails closed rather than silently truncating. Unsupported FTS,
legacy file-backed exchanges and blob streaming return unavailable. There is no
capture, proxy, deletion, implicit store construction or default route enabling.
One-process writers honoring the same lock remain a host requirement, not an OS
sandbox against uncooperative writers.

Intercept rule persistence is a separate metadata-only component. Task and global
workspace stores use explicit scope, independent mutation permission and idle
ownership, source ordering and timestamp compare-and-swap. Text projections are
bounded before transfer. A timestamp CAS is not a runtime policy revision or cache
invalidation mechanism. The store does not implement matching, grant tool access,
install policies, or synchronize running agents. Optional source-shaped HTTP CRUD validates and normalizes patterns before
approval. It deliberately requires an ISO X-Rule-Updated-At timestamp for stale
edit protection, so existing frontend mutation calls require adaptation. A service
retains one fixed task OR global scope; multi-task resolver integration is not
implied. No default registration or matcher/cache activation follows from CRUD.

See ISOLATED_QA.md for immutable fixture evidence. No real captured traffic,
production rules, targets or external providers were used.

Frozen checkpoint: 1,985 authored tests pass and 398 captured SQL statements parse.
Static routes: 144/261 (117 absent), including optional/unavailable contracts,
not complete-feature or production-readiness counts.

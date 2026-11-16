# Tool-input intercept rules: unregistered read slice

`db/tool_intercept_read.py` and `server/tool_intercept_read.py` implement the
read-only DTO contract of `GET /api/intercept/rules` (server/server.go:909,
server/intercept.go:interceptListRules, db/intercept.go:ListInterceptRules).
This is not the asset/task intercept-rule table or matcher.

All fourteen source fields are returned, including raw pattern/message, disabled
rules and timeout metadata. SQL preserves priority descending then ID ordering;
empty storage returns `{"rules":[]}`. No regex compilation, interpretation,
mutation, cache invalidation or runtime activation occurs. Timestamps retain
fixed-offset RFC3339 representation and available microsecond precision; PostgreSQL
cannot supply Go's hypothetical sub-microsecond precision.

The actual injected executor receives parameter-bound finite limits. A bounded
CTE reads at most max_rows+1; a cumulative text-byte window produces NULL sentinels
before oversized text crosses the executor boundary. Overflow refuses the whole
response rather than silently omitting a security policy. Maximum 10,000 rows
and 1 MiB text; defaults 1,000 rows and 1 MiB. JSON escaping/field overhead can
increase serialized bytes beyond the text cap but remains finite under row/text
bounds. Strict primitive/aware built-in timezone or ZoneInfo datetime validation prevents arbitrary
formatter callbacks. No model/network/filesystem activity is involved.

Original reader/executor/bounds and read-policy/current/guard identities are pinned.
The host must explicitly authorize global raw-policy disclosure, independently of
JWT. Fresh fences run before SQL, after SQL, after serialization and after guard
exit. Arbitrary mutually mutating policies do not receive a fixed-point guarantee.
A host eventually mounting this handler must retain original JWT checks around
its own serialization/transport boundaries. No registration or application wiring
is included in this stage. Query/body rejection, finite bounds and redacted errors
are intentional stricter behavior than upstream's unrestricted read.

Verification: six inert owner tests cover source DTO/default/ordering query,
strict types, byte sentinel/row overflow, denied pre-read, post-read/serialization/
guard revocation, resource/callback swaps and redacted errors. Three independent
regressions also pass after correcting standard ZoneInfo compatibility. Exact-source
PG96 passed three actual isolated PostgreSQL groups; see ISOLATED_QA.md for the
manifest and observed driver mapping. Frozen suite2810 tests456 SQL syntax checks.
No production database or registered HTTP route is claimed.

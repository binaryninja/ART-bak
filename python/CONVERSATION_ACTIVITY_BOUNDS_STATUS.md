# Conversation activity SQL transfer bounds

The existing list/page row count cap did not constrain text transfer; detail read
selected arbitrarily large detail strings. This hardening adds explicit byte limits
to the real ConversationActivityStore reads, without changing writes or silently
truncating accepted data.

ActivityReadLimits defaults: 64KiB combined worker/kind/tool/tool_use_id/summary per
row, 1MiB aggregate selected-window text and 1MiB detail. Limits are UTF-8 octets,
not characters. Hosts may supply read_limits to the store or activity_read_limits
to build_conversation_runtime/build_chat_application. Positive exact integers are
required, row <= page, row <=1MiB and page/detail <=16MiB. Invalid configuration
refuses before assembly effects. Existing direct callers get explicit defaults.

One selected/sized/checked SQL statement computes row MAX and text SUM across the
whole selected window, including reverse-page lookahead. Any oversize row or page
masks every selected text field to NULL before transfer. The decoder refuses the
entire result, never returns smaller rows as partial success. Detail CASE similarly
returns a NULL overflow sentinel; actual SQL NULL detail is coalesced to empty, and
missing detail remains empty. Client-side byte/shape checks defend the same bounds.
Bounds replacement on an existing store refuses rather than silently changing it.

Original filters, ascending tail order, reverse-page display/cursor/hasMore,
nonpositive-limit defaults, signed cursors, nullable tokens and missing-detail
semantics remain. The bounded subset is deliberately narrower than unrestricted
upstream history reads. Oversize returns503 only after existing authority checks;
revoked authority still returns403. No activity data retry/requery or new writes.

These are transfer limits, not a bound on server scans, sort/materialization or
SQL deparse work. Fixed numeric/timestamp columns and row-count overhead remain.
JSON escaping can amplify text by up to six times, plus fixed per-row DTO overhead;
there is no separate encoded-response cap. Custom host activity implementations
outside ConversationActivityStore remain responsible for their own transfer bounds.
No claim of arbitrary host-code containment, atomic revocation or production launch.

## Verification

Seven new tests cover exact multibyte edges, row/page/sentinel refusal, lookahead
bytes, detail empty/missing/NULL sentinel, limits/rebinding, HTTP oversize-vs-denial
gating, and runtime configuration propagation. Independent review passed 80 focused
tests and additional five-field UTF-8 and initial source/outer-denial probes; denied
reads performed zero activity SQL. No blocker was found within this bounded scope.

Fresh QA243 report:
`/workspace/shared/artex-qa/conversation-bounds-243/REPORT.md`

Frozen manifest SHA256:
`44f0fbdb13eb8d210129a5556ddb356d9623f8261378d5bf1072d9c2117bc86f`

73 actual PostgreSQL/HTTP/transport cases passed: the prior 48 history authorization
cases plus 25 byte-bound/cursor cases. The corrected focused-test run passed 80 tests.
Actual PG17 accepted exact 64KiB combined rows and 1MiB detail/selected windows,
including multibyte text and reverse-page lookahead. One-byte overflow refused the
whole result. All selected text fields were SQL NULL on overflow, including otherwise
valid marker rows; actual pool-result text payload was zero. This is evidence of
SQL projection bounds, not merely post-transfer Python rejection or packet tracing.
Oversized reads with revoked authority returned403 rather than503. Ordering, cursors,
hasMore and NULL/missing/foreign-conversation empty-detail semantics were verified.

Four baseline success reads used isolated loopback HTTP with original signed JWT;
callback races used the actual WSGI/_AuthenticatedView header seam, not a claim of
socket-race reproduction. Custom-limit assembly propagation was tested in the
focused suite, not a separate real-PG production launch.

The initial QA243 test freeze accidentally included the old legacy activity fixture:
three failures and one error reflected old SQL-parameter and NULL-sentinel expectations.
That log is retained. The corrected author fixture was frozen separately and all 80
tests passed without changing the runtime/integration snapshot. Historical QA143–240
artifacts remain unavailable after workspace restoration; this evidence is fresh.

No server scan/materialization/memory bound, network-packet bound or JSON response
byte cap is claimed. JSON escaping can enlarge accepted multibyte detail. Finite
cooperative authorization receipts do not establish atomic revocation or arbitrary
callback fixed points. Held dashboard/private and speculative files are untouched;
this status update changes no code, staging or publication state.

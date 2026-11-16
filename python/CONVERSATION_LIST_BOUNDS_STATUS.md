# Conversation list transfer bounds

The metadata list previously selected every conversation and arbitrary title/agent
text. ConversationReadLimits now defaults to 1,000 rows, 64KiB combined UTF-8
agent_key/title per row and 1MiB aggregate text. Positive exact integers are required;
configuration caps are 10,000 rows, 1MiB per row and 16MiB aggregate, with row <= total.
Hosts can supply read_limits to ConversationStore or conversation_read_limits to
build_conversation_runtime/build_chat_application. Validation precedes assembly.

One ordered selected-window SQL statement requests at most maximum rows plus one.
COUNT/MAX/SUM window checks cover that complete selection. Count, per-row or total
overflow masks both text columns to SQL NULL for every selected row; client shape,
count and UTF-8 validation rejects the complete result. No top-N or truncated list
is returned as success. An existing store's bounds cannot be silently replaced.

Below limits, the original unpaged envelope, pinned-first order, timestamps,
optional profile and supplied running observations remain unchanged. get and write
methods are unchanged. Oversized lists return503 under the existing receipt's final
authority gates; revoked authority returns403. No independent per-conversation
permission is inferred from the original global Router authorization.

## Verification

Seven new tests cover multibyte/aggregate edges, empty lists, count/row/total and
NULL-sentinel refusal, configuration/rebinding, error reauthorization and actual
runtime plus signed-JWT metadata-route configuration propagation. Independent
review identified the initial separate-metadata-store forwarding defect; the fix
passes the assembled GET regression. The exact store-construction mock expectation
now includes default limits, preserving all no-read and no-callback assertions.

Fresh evidence: QA246.

Frozen manifest SHA256:
`5ad82a2f1f646d8ba9d3bfc283b94fc0c7d8e8d398314a0483317b030fe1bd49`

67 actual PostgreSQL/HTTP/transport cases and 60 focused tests passed. Twelve real
full-assembly loopback requests use build_chat_application, the real PG pool,
filesystem transcript root and original signed JWT. Custom one-byte ASCII and
multibyte exact/overflow thresholds, row caps and aggregate caps produce 200 or 503
as specified. Invalid JWT returns 401 with zero list SQL. Each assembled runtime
drained cleanly and no provider was invoked.

The 55 other cases include exact default 64KiB rows, 1MiB aggregate and 1,000 rows;
one-byte ASCII/multibyte overflow; 1,002 stored rows transferring at most 1,001;
whole-window text masking, zero running calls on overflow, post-SQL expiry 403,
legacy pinned/update ordering and original disclosure receipts. Each list reads
once. Overflow transfers NULL for both text fields rather than partial DTO text;
fixed numeric/timestamp columns remain. Deterministic header probes are exact
WSGI callbacks, not a network scheduling race claim.

QA245 is retained as nonfinal because custom limits originally reached the chat
store but not the separate metadata store. QA246 uses corrected source wiring.
The updated test_application.py fixture was frozen as a separate test overlay
after the manifest freeze; all 60 focused tests pass with runtime snapshot unchanged.
No source behavior was weakened to satisfy stale fixture expectations.

This intentionally narrows the unbounded upstream read: large lists fail rather
than paginate implicitly. It bounds transferred text/row count, not PostgreSQL
scanning, sorting or materialization. Fixed typed-column overhead remains; JSON
escaping can amplify accepted text up to sixfold plus DTO overhead, and no separate
encoded-response cap is claimed. Custom stores remain responsible for their own
bounds. Existing finite receipt checks are not atomic revocation or arbitrary host
code containment. No default runtime launch, merge or deployment is added.

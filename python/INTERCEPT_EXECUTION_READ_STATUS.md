# Recorded execution navigation, read-only

New db/intercept_execution_read.py and server/intercept_execution_read.py implement
standalone GET `/api/intercept/history/{id}/execution`. Eight synthetic tests pass;
Six independent tests pass. PostgreSQL stage 116 passed six actual SQL groups
and 28 focused tests; see ISOLATED_QA.md. Navigation reads
recorded activity. It never executes, approves, resumes, searches heuristically or
reconstructs a tool call. No route/default or mutation is installed.

Source: db/intercept_execution.go GetInterceptExecution, server/intercept.go
interceptExecution and server/dto.go activityDTO. The actual bounded detail reader
supplies the approval/audit. Only correlation=exact with nonempty tool_use_id is
eligible. Conversation ownership takes precedence over task TEXT ID. Task IDs
parse as positive signed64 and require a nonarchived/nondeleted task. Exactly the
same parent's tool_use/tool_result rows are selected by tool_use_id, ordered by
id, LIMIT3. No activity-page scan or command-text/run-ID matching is used.

Zero rows yields410; three matches or mismatched call/result yields409. The first
must be the recorded tool_use for the approval's tool; a second must be its
same-worker/node/segment tool_result with matching-or-empty tool name. NULL and
zero main segments compare equally. A single call without a result is navigable.
Task sessions are main:N, plan or intent:N; conversation session is empty. DTO
includes source nullable task/conversation keys and source-selected activity
fields only, not detail/metadata/tokens absent from this query.

Missing approval yields404. Only then a valid conversation hint may check existence
and yield410 for a deleted conversation. It never retargets an existing approval.
Invalid correlation or unparseable/no parent yields409; deleted/archived task or
missing original activity yields410. Storage/oversize errors are redacted500.
Duplicate/unknown queries, malformed IDs and excessive input sizes are stricter
request limits than Go's generic router. Error explanations are English.

Explicit fixed InspectionScope and original approval-ID/raw-review grants precede
reading detail. Separate immutable ExecutionParent grants precede any conversation
hint existence, task lifetime or raw activity query. Task exploration identity is
resolved from the actual task and separately authorized. JWT alone is not those
grants; eventual mounting must retain authentication. Cumulative byte sentinels
suppress worker/tool/summary payload transfer before overflow. Row types, IDs,
UTF-8, timestamps and pairing are validated. No profile/config credentials join.

Service re-reads bounded correlation/live parent and exact activity after response
serialization and after guard exit, refusing changed identity/content rather than
retargeting stale output. Original source/raw/current/request pins and parent
grants are refreshed at final disclosure. This uses finite repeated snapshots,
not a distributed atomic snapshot or arbitrary-policy fixed point; host policies
must cooperate with the lifetime guard. Three bounded reads are intentional and
perform no SQL mutation. Unrelated historical audit/context is not returned.

# Standalone exact-task intercept history

`server/intercept_task_read.py` reuses the actual `InterceptHistoryStore` for
GET `/api/intercept/task/{taskID}`. No route is registered, no decision/matcher is
activated, and execution navigation is not implemented. Eight new tests plus
17 existing history tests pass, plus five independent review cases. PostgreSQL
stage 114 passed three actual SQL groups and thirteen focused reader tests; see
ISOLATED_QA.md.

Source: server/intercept.go interceptListTaskItems and db/intercept.go
ListTaskIntercepts/ListTaskInterceptsPage. An unfiltered request with absent/empty
page and size returns `{items,total}`; pagination or filters add page/page_size.
Source numeric fallbacks, positive page, size20 fallback/max100 and exact allowed
status/decision_source filters are retained. All source row fields are included,
including nullable IDs/dates, decoded tool_input JSON and conversation/rule labels.
Legacy decision-source matching is unchanged. No audit is fabricated.

The sole store extension is explicit `task_all`: it requires a non-global exact
TEXT task scope, reads max_rows+1 and refuses overflow instead of returning partial
legacy results. Existing pending/one/paged history defaults remain unchanged.
Queries retain their single-snapshot count/page, cumulative SQL byte sentinel,
created_at DESC/id DESC ordering. The id tie-break is existing port behavior.

The task path must match the original InspectionScope.task_key exactly; there is
no active task, numeric coercion, company or global fallback. Query duplicates,
unknown fields, malformed encodings, separator/control-containing IDs and excessive
offsets are refused as documented stricter limits. Source task IDs are text, not
proof of a currently live task; the explicit current policy owns that lifetime.

Original request/source/raw/current callbacks, executor/scope/guard and finite
bounds are pinned. Request and data grants are checked before SQL and after the
store guard exits, after projection, and after response serialization. The adapter
does not nest the store's potentially non-reentrant guard. Driver JSONB primitives
are validated with finite numbers/depth64/20k values, dates are validated, and raw
errors are redacted. Eventual composition must supply JWT/body-final fences; this
standalone service is not authentication. Finite grant checks assume cooperative
read-only policies, not arbitrary mutually mutating callback fixed points.

## Optional exact typed metadata overlay

`bind_metadata_reads(..., task_history_service=...)` adds only the protected GET
`/api/intercept/task/{taskID}` when explicitly supplied. Exact TaskInterceptReadService
and InterceptHistoryStore types, the fixed non-global InspectionScope, original
executor/resources and disclosure method are pinned. No resolver or global task
fallback is created. Existing original WSGI/AuthService and shared-executor rules
remain; unsupported overlay stacking is refused rather than guessed.

The pure canonical TaskInterceptSelection is reused by the service and wrapper,
with exact field types and original request identity. Extracted validate_disclosure
performs the same original request/source/raw/current grant chain without SQL or
nested guards. Wrapper repeats that pinned chain after JWT and start_response;
late revocation withholds body bytes. Dynamic literal/mixed-parameter route
conflicts are rejected before any original route changes. No mutation, execution
navigation or default registration is added. Store code is unchanged in this slice.

Four new composition and two exact-scanner tests pass; 39 selected composition,
standalone and review tests pass together. Four independent review cases and
70 neighboring regression tests passed. PostgreSQL stage 115 passed four actual
SQL/JWT groups and 21 focused tests. Standalone acceptance remains separate.
The public route report was regenerated from the reviewed frozen snapshot.

# Standalone global skill usage read

`db/skill_usage_read.py` and `server/skill_usage_read.py` implement the source
`GET /api/skills/{name}/usage` contract without registering it. Nine authored
inert tests plus independent review and actual PostgreSQL stage142 now pass.
No skill filesystem access, loading, execution, model call, ledger append or
archive reconstruction occurs.

Source: `server/server_mgmt.go:fsSkillUsage`, `db/skill_usage.go:RecentSkillCalls`.
The exact skill name uses lowercase ASCII letters/non-ASCII Unicode letters,
digits (including Unicode Nd), single hyphens, letter first, no trailing hyphen,
and at most 64 Unicode code points. Path decoding occurs once. Invalid limit,
nonpositive or greater than 200 defaults to 50; selected rows are found=true and
ordered ts descending. Equal timestamps retain source's unspecified tie order.
Returned calls contain ts, agent_key, task_id, session_id and args_len only.
Null dimensions use source empty strings/zero. Missing skill yields empty calls;
no filesystem existence inference is made. Ledger has no parent foreign keys:
rows can remain after task/conversation deletion, and global authorization is
required rather than inferred task access.

API: exact `UsageScope(workspace, revision)` identifies a host's explicit global
catalog usage grant, not a workspace-membership-derived permission. Frozen
`UsageSelection(name, limit)` is passed unchanged through request/source grants.
`SkillUsageReader` requires authorize_global/current/lifecycle_guard; the exact
`SkillUsageReadService` adds independent request authority. Constructor is inert.
Original resource/callback identities and selection primitives are fenced;
source and request grants are rechecked after fetch, projection, guard exit and
HTTP serialization. No nested guard acquisition is required. These are finite
cooperative callback fences, not an atomic snapshot of arbitrarily mutually
revoking external policy systems. Future transport binding must preserve original
JWT and header-release checks; standalone service does not authenticate JWT.

SQL caps page rows first and gates all fetched text using window aggregate byte
size before transfer. Oversize or malformed rows fail without partial disclosure;
text is never truncated. Decoded strings, exact integer types, timestamps and row
count are checked again. Fixed-offset datetime and native ZoneInfo timestamps
are supported; custom tzinfo callbacks are not. Response size is bounded by the
text-byte cap (JSON escaping may expand it) plus at most 200 fixed-size rows.
Unknown/duplicate query keys and non-GET/body requests are intentionally rejected,
unlike source's permissive ignored fields. Errors are redacted. No default route,
archive merging, runtime observer, or new execution authority is supplied.

Independent review reproduced two disclosure defects before correction: a final
lifetime callback could revoke the request grant, and a request callback could
retarget the immutable request using Python mutation. The service now snapshots
exact method/target/body and the original plain-dict string header container and
entries, verifies them around every request grant, and refreshes the original
request grant after the complete source disclosure check. Only pure original
resource/request/selection checks follow that final grant. Custom header mappings
are intentionally unsupported in this standalone adapter. The two unchanged
regressions pass; 14 owner/reviewer tests pass with fresh independent review. As elsewhere,
this finite source-then-request ordering does not promise a fixed point for
arbitrary callbacks that mutually revoke each other's external permissions.

## Isolated acceptance

Stage142 original-schema PostgreSQL acceptance passes found-only/order/limit/null
wire shape, UTF-8 SQL sentinel bounds, and final request/source/serialization
authority regressions. Fourteen focused tests pass. See ISOLATED_QA.md for exact
immutable evidence. Standalone only; optional HTTP wrapper remains separate.

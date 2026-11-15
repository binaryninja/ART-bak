# Optional diagnostics and bulk health reset

Task-local audit reads use the actual in-memory Guard, preserving its last-2000
chronological ring and source missing-task response. Selection authorization
precedes the explicit task resolver; raw commands/reasons require separate grants.
No global audit store, synthetic sequence, implicit active task or tool execution
is introduced. Entry and attribution snapshots are separate, as in the source.
The application factory opts in only with explicit retained bindings.

Intercept inspection reads PostgreSQL durable pending requests and explicitly
paginated history. Task keys remain exact text, not inferred numeric task IDs.
The history joins and count/page use one bounded SQL snapshot. Stored pending
status is not proof of a live approval waiter. Legacy unpaged history is refused;
this component cannot approve, deny or execute a tool. Raw grants are checked
after fetch and again at response preparation. This was added after isolated
acceptance demonstrated a post-query permission-revocation defect.

Registry reset-all uses bounded actual registry IDs, including orphan IDs, not
just configured profiles. Approval is for the complete frozen inventory. Revision
checks precede atomic in-memory clear, and original pinned persistence callbacks
run off-lock with fresh authority and revision fences around each effect. Partial
persistence leaves an explicit reconciliation requirement; there is no durable
recovery journal and no automatic retry guarantee. Concurrent new state is not
silently erased. This is an optional host-controlled operation, not permission to
call providers or modify unrelated settings.

All services remain opt-in. See ISOLATED_QA.md for immutable acceptance scope and
README.md for the latest frozen test count. These adapters do not close production
SDK, timed lifecycle, frontend runtime or full route-parity gaps.

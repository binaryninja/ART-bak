# Scoped interception decision persistence candidate

New `InterceptDecisionStore` uses original `intercept_pending` columns; no schema
migration, HTTP registration, Context changes, guard installation, model or tool
execution. APIs: `create(DecisionRecord)->id`, `get(id)->copied bounded dict|None`,
`resolve(DecisionResolution)->bool`. Optional request_authorize is evaluated at
the actual read/write boundaries, after original current/write/read callbacks.
The store always requires independent read and write policies and host lifetime
guard. A guarded parent SELECT verifies/locks a single live nonarchived task+
exploration or one conversation. No task/global fallback. Task IDs stored as
canonical decimal TEXT; legacy opaque task keys are unsupported for this writer.
Mixed task+conversation source records are intentionally unsupported.

Frozen records carry bounded immutable JSON strings. Input empty string becomes
{} as source; SQL NULL audit remains NULL. Duplicate JSON keys, malformed known
audit fields, JSON-null audit, excessive depth/bytes and invalid UTF8 reject before
SQL. Audit is supplied evidence, not generated correlation: no invented run_id,
tool_use_id, model_input or execution results. The existing reviewed detail parser
validates the source audit DTO; unknown source fields remain in stored JSON and
read projection follows that parser. No evidence enrichment or result completion.

Source fidelity: rule_id/conversation/task absence becomes SQL NULL, decision_source
is rule when rule ID exists, model only for original `[模型]` reason prefix, otherwise
unknown. Initial status pending has null decided_at; allowed/denied stamps now.
Resolution uses `WHERE status='pending'` plus exact parent scope; false means
missing/foreign/already-final without leaking which. Audit NULL stays NULL; else
source effective_action/decision_reason/execution_status patch is merged. Allow
with nonexact correlation records unknown, not fabricated awaiting-result identity.
No decision HTTP or human permission is inferred by the resolve method.

Stricter than Go: scoped parent locking and archive refusal, bounded transfers,
consistent allowed/allow and denied/deny, immutable proposal rechecks, explicit
permissions, sticky instance quarantine after any attempted write ambiguity.
No retries or read-based adoption after uncertainty. Get is one bounded row with
SQL byte CASE gates; final grants repeat after transaction and guard exit. Finite
cooperative callback boundaries, not a transaction over arbitrary host policy.

## Separate future runtime seam, not implemented here

Original Go `Interceptor.Log` ignores CreateDecidedIntercept errors; Python legacy
log likewise swallows exceptions. This store does not silently change that.
Any future optional durable mode must carry a typed exact scope/evidence context,
return an original-issued persistence receipt, and explicitly fail closed on audit
failure without changing legacy default behavior. Pending-resolution races and
canonical execution/audit binding must be independently reviewed before enabling
real guards. Current module is not plugged into ToolInterceptConfigStore or
Interceptor.Store automatically. Stage135 actual PostgreSQL acceptance passes four groups and 18 focused tests,
including independent concurrent pending-resolution transactions with one winner,
scope/bound checks and committed acknowledgment-loss quarantine. This is store-only
acceptance, not runtime guard integration. See ISOLATED_QA.md.

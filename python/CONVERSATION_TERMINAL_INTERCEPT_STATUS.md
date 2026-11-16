# Conversation-only terminal durable interception candidate

Explicit `ConversationTerminalIntercept(config_owner, interceptor, decisions,
transcript, session_id=..., model=..., authorize_call=..., authorize_audit=...)`
can be supplied as `ChatAgent(..., terminal_intercept=owner)`. Default None keeps
the legacy pipeline. No Context/log, task assembly, HTTP, default provider, guard
installation or judge behavior changes. This uses actual literal Interceptor
matching plus the bounded DecisionStore, not the legacy log method that swallows
persistence failures. Existing Guard behavior remains unchanged when used alone.

Constructor requires a declared exact Interceptor/config owner, same executor and
workspace as the exact conversation-only DecisionStore, exact canonical
ChatTranscriptAdapter/TranscriptStore, explicit session/model and no reviewer.
Host explicitly authorizes that conversation/session pairing; it is not inferred
from arbitrary metadata. Task scopes and task fresh-transcript adapters reject.

A private session is issued only after actual new/unfinalized transcript begin,
for original ChatAgent's registered wrapped token, actual turn snapshot/model,
thread and runner. One opaque call binds original validated Action and actual
RuntimeContext. Copied, reentered, retained, foreign-thread and changed resource
calls reject. No callback receives the opaque capability as execution permission.

The code-owned config-owner call registry reserves the original local generation
before cache matching/SQL, excludes same-owner mutations regardless of a host idle
callback, and stays registered through the real tool callback and postcallback
checks. It holds no registry lock across transport, SQL, authorization or tool
callbacks. This excludes cooperating local policy writes only, not independent
processes issuing SQL. The host still owns the adapter, original DB/session,
configuration lifetime and authorizes any external changes.

Ordinary normal gate permission must pass before interception. Independent call
and audit grants apply; after audit the original normal gate is refreshed and
canonical transcript/model/current token checked again before callback. Audit
permission cannot become tool permission. These are finite cooperative fences,
not a universal fixed point across arbitrarily mutually-mutating callbacks.

Matched allow/deny writes original scoped terminal records. Ask is unsupported
and blocks without creating a pending wait; model reviewer is refused. Disabled
tools/no match preserve source bypass behavior, still require the ordinary gate
and explicit call grant; no terminal audit is invented for them. Input/name/rule
and supplied decision facts are recorded. Protocol Action.call_id, chat turn ID
and run number remain internal lifecycle checks: audit correlation is unavailable,
run_id/tool_use_id are absent. Allowed execution_status is unknown, never success;
denied is not_executed. No execution result completion/correlation or canonical
assistant tool-use receipt is claimed (ChatAgent commits full history at run end).

Attempted audit uncertainty raises EffectUncertain and retains/quarantines the
exact local call lease/store/owner with pending canonical turn; no automatic
replay/recovery. Tool EffectUncertain also retains it. Ordinary denial/cancellation
before any uncertain effect revokes capability on exit. This is deliberately
stronger opt-in handling than Go's ignored Log errors, not a legacy change.

Tests use real temporary canonical JSONL, actual ChatAgent/Interceptor and inert
SQL/provider/tool fixtures. They cover commit-before-callback, denial/ask refusal,
normal grant denial, postINSERT cancellation/revocation, unknown commit/no replay,
actual blocked-thread mutation exclusion, retained/copied session and resource
changes. No model request, OS tool or user database action was executed. Independent
review and frozen actual PostgreSQL acceptance are still required.

Independent review corrections: session method entry alone cannot issue a call.
The runner now creates an opaque dispatch slot inline only after its validated
Action and original grant; exact runner/context/action/thread slot ownership is
checked throughout and revoked in runtime finally. A model gate constructing an
Action and manually calling session.dispatch is refused before any SQL. Final
normal execution authority is refreshed after callback-bearing durable fences;
only issued-binding/token checks follow it before the callback. The optional path
avoids adding another injected-clock callback between that fence and execution.
Both unchanged reviewer counterexamples now pass; fresh review remains required.

## Isolated acceptance

Stage 138 passed actual disposable PostgreSQL acceptance for this narrowly
supported opt-in slice. See ISOLATED_QA.md for immutable manifest, exact cases,
prior corrections and remaining limitations. This does not enable defaults or
establish full Go parity.

# Optional intent pause/resume HTTP composition

`IntentControlService` and `build_intent_control_application` explicitly install
one original adapter on an existing host and overlay the original JWT WSGI app.
Construction does not activate a default route or change the base app. A prior
control adapter or conflicting route is refused, never replaced.

The source route is POST `/api/tasks/{id}/intents/{iid}/control`. Confirmed
pause/resume returns `{id,state}`. The source client's `reason` and `mode` string
fields are accepted for these operations but cause no deletion behavior. Cancel
is explicitly unsupported (501); messages, rerun and task resurrection are not
registered. No wire version or inspection route is invented. A trusted host may
call the wrapper's explicit in-process `inspect(request, task_id, intent_id)` to
reconcile a retained pause with a newly authorized request.

Canonical positive identifiers, optional exact fixed-task restriction, an
explicit selector grant before lookup, a 4 KiB strict JSON body and finite wait
are required. The global WSGI cap is unchanged. JWT and selector authority are
request-local ContextVar state, reset in finally. The independent core proposal
grant remains mandatory at cancellation/CAS/inspection boundaries. Original
host, installer, service and auth resources/methods are pinned. No shared mutable
request callback or implicit task/global authority is installed.

Pending returns 409 with `pending:true,retry:false`; uncertain outcomes return
409 with `unknown:true,retry:false`. No response promises rollback after an
effect. Final response checks repeat JWT, selector and available proposal grants;
revocation after headers aborts body disclosure, not the earlier mutation.
These are finite cooperative checks, not a fixed point against arbitrarily
mutually changing callbacks. Unresolved core state remains retained.

Ten initial tests pass, including real owned blocked-worker pending pause,
authorized inspection, absent/cross-thread context denial, after-header grant
revocation, one-time installation and route conflicts. Independent review and
actual PostgreSQL HTTP acceptance remain pending. Core pause/resume acceptance
and expiry inspection are separate snapshots.

Independent review reproduced proposal callbacks revoking request authority
immediately before cancellation and after response headers. Both unchanged
regressions now pass: proposal approval is followed by a fresh original JWT and
selector check, then pure pins. Fourteen owner/review tests pass; fresh reviewer
clearance and actual PostgreSQL HTTP acceptance remain pending.

## Isolated acceptance

Stage 137 passed actual disposable PostgreSQL acceptance for this narrowly
supported opt-in slice. See ISOLATED_QA.md for immutable manifest, exact cases,
prior corrections and remaining limitations. This does not enable defaults or
establish full Go parity.

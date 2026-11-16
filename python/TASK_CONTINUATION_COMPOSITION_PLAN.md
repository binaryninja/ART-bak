# Next continuation milestone: existing HTTP route and clean paused shutdown

Read-only proposal. Current owner/store/manager/runner files are held for actual
PostgreSQL acceptance.

## Existing HTTP behavior, not another route

`server/tasks.py` already registers task control and accepts pause/resume.
`server/task_host.py:TaskHTTPManagerFacade.control_task` sends pause through the
original host and resume through its shared admission/capacity guard. The manager
now selects a retained exact same-process suspension when explicitly configured.
Thus the missing product composition is opt-in setup, not a new control handler,
new version field, or a parallel task resolver.

Proposed NEW `server/task_continuation_setup.py`:
`enable_task_continuation(exact_bundle, *, authorize)` constructs the exact owner
and invokes the existing original host installer once. Return a small frozen
bundle holding the same application plus owner; do not rebuild the JWT app,
manager, runner, pump or admission scheduler. Require original resource/method
pins and unused execution/admission state before installation. No provider calls,
schema writes or task lifecycle writes during composition. Default absence leaves
initialized resume unsupported. This does not enable queued, timed or restarted
continuations and cannot accept arbitrary SQL receipt metadata as authority.

Acceptance: original JWT POST task creation at timeout zero; real worker pause
through POST `/api/tasks/{id}/control`; SQL paused state and canonical cleanup;
HTTP resume with capacity refusal before SQL when full; permitted resume with a
fresh token/handle, unchanged prepared receipt/config and no goals replay; next
normal owned step launches work. Also unauthorized JWT, duplicate install,
foreign bundle, changed owner methods, terminal/queued/timed/no-private-receipt
refusal, and after-write uncertainty retaining both references. No new route is
necessary or should be claimed.

## Clean paused shutdown is intentionally blocked today

Host poll includes `continuation_owners`; even a clean paused suspension prevents
`all_settled`. RuntimeListener therefore correctly refuses resource/pool close.
A clean suspension is no longer executing, but retains process-local resume
permission and original resources. Removing the old execution run alone is not
a safe resource-close proof.

Proposed separate protocol:

* `owner.seal_resume()` irreversibly prevents new resume attempts and installation
  of resumed work. It must not prevent cancellation/drain of already owned work
  during shutdown. Define any in-progress resume outcome conservatively retained.
* Explicit `host.relinquish_paused(cap)` reserves the existing original host
  mutation/settlement barrier. It accepts only this owner's exact clean paused
  capability, no execution run, no deadline/finalization, no cleanup/process or
  intent-control uncertainty, original cancelled old token/handle, and drained
  original canonical resources. No bare task ID or cancelled flag substitutes.
* Under original guard/grant, reverify exact paused DB/task timestamp/bootstrap
  identity and absence of running claims. Relinquishment writes no SQL and does
  not unpause, complete, reset clocks or claim recovery support.
* Finish all external callbacks and SQL before callback-free exact manager and
  runner retirement. Preserve ownership through the full removal/acknowledgement
  tail. A failed compare/remove or acknowledgement retains an explicit pending
  or uncertain owner; it is not silently treated as released.
* Only after original runner removal and capability consumption can host drain
  report the suspension settled and listener close resources. Paused state remains
  durable, while future restart resume is still unsupported.

Do not broaden generic cancelled-handle retirement, clear suspension dictionaries,
or call this protocol for paused-but-quarantined, resuming, installed-tail, timed,
terminal or foreign resources. Listener wiring can follow only after core
standalone and concurrent seal/resume/close tests plus actual PostgreSQL proof.

Recommended order: first exact HTTP setup + genuine existing-route acceptance;
then clean-paused relinquishment and shutdown-tail ownership; finally explicit
listener integration. None requires a new initialized recovery fallback.

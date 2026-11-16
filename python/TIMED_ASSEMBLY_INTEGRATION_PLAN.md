# Explicit timed assembly: design record and remaining acceptance

This records the design before inert owner binding and role-clock propagation
were implemented. TASK_TIMING_BINDING_STATUS.md describes the current code and
verification; the call-site gaps below are historical motivation, not a current
status inventory. Positive admission, automatic observers and final actions
remain disabled. Synthetic prepared pins are not positive-duration acceptance.

## Current call sites and missing connection

- `agent/task_runtime.py:TaskRuntimeAssembly.engine_factory` creates the actual
  PlannerCycle and IntentWorker. Their provider resolver lambdas currently return
  `TaskProviderResolver.bind(...)` directly. These are the two proposed wrapper
  sites. `_goals` remains direct: initial goal decomposition must NOT start the
  task execution clock (matching the source first-call contract).
- `agent/bootstrap_handle.py:BootstrapHandle.advance` constructs the inert child,
  retains it, checks exact ownership and invokes `child.bind_cleanup(manager,
  self)` before changing to the engine phase. This is the model for a separate,
  explicit code-owned clock binding hook. `engine_factory(row, token, scope,
  current, guard)` does not presently receive the installed manager/outer handle
  or prepared journal receipt. Do not recover those through closures, mutable
  global lookup or a fabricated EngineScope.
- `db/task_deadline.py:TaskDeadlineStore` requires the actual manager, exact
  BootstrapHandle and prepared Receipt, original coordinator guard and an
  independent stamp policy. `OwnedGenerationClock` must be allocated ONCE for
  this original owner, not once per bind, role, worker or turn.
- `agent/task_deadline_provider.py:DeadlineProvider` wraps a selected ChatBinding
  and role/model-specific PreparedDispatchClock. Return a new ChatBinding with
  the same selected model and this adapter as provider, retaining live resolver
  configuration checks. Planner and worker facades share the original kernel.
  Stream creation stays lazy; stamping occurs at actual provider dispatch only.
- `agent/planner.py` and `agent/worker.py` currently instantiate ChatAgent without
  a clock argument. A pinned opt-in clock must reach those actual ChatAgent and
  RuntimeContext constructions. Pass one identical callable to the kernel,
  dispatch facades and ChatAgent. Merely choosing clocks with numerically similar
  epochs is insufficient. `providers/http.py` uses relative remaining_seconds
  for socket limits; it must not compare runtime and transport absolute clocks.
- `agent/expiry_observer.py:OwnedExpiryObserver(host, kernel, ...).tick()` is a
  manual non-stamping observer. It reports unstamped/busy/observing/draining/
  waiting_database/ready and does not allocate an assessment or complete a task.
  `server/task_runtime_setup.py` currently creates one bootstrap application and
  EnginePump; neither automatically constructs nor drives this observer.

## Minimal explicit composition shape (proposed)

1. Require a typed opt-in timed policy bundle, with pinned wall/monotonic clocks,
   stamp grant, role dispatch grant, observation grant and freeze grant. Keep
   ordinary assembly defaults unchanged. Include policy/version and timed mode
   in durable bootstrap configuration identity, without serializing secrets or
   callback representations. Retain all existing model, graph and tool grants.
2. Bind original clock resources only after an exact prepared receipt and installed
   manager owner exist, before the child can execute. Construction must not stamp,
   call a provider or run an observer. Retain partially constructed resources on
   binding failure rather than losing a possible owner. Rebinding/adoption denied.
3. Resolve the selected role model as today, then construct its dispatch facade
   against that shared kernel and wrap its actual provider. Preserve streaming
   capability, model identity, final live profile fences and grant checks. Never
   wrap goals or silently use planner credentials for a worker.
4. Expose an explicit manual observation operation through the existing application
   owner, tied to exact retained generation inventory. No implicit background
   observer or second manager/pump. Observation must be callable independently of
   a blocked engine tick; it must not hold engine admission locks while waiting
   for a running provider or reacquire a non-reentrant lifetime guard.
5. Keep zero-timeout tasks on their existing path. Positive admission remains
   rejected until the complete connected lifecycle and tests below are reviewed.

## Safety blockers before any gate change

- `BootstrapHandle.__init__`, `EngineRunner.ready`/validation still reject nonzero
  timeout tasks. A supported positive prepared timeout must originate from immutable
  admitted configuration, not test-style mutation of manager/handle pins.
- The final clock binding hook and exact clock propagation are absent. Adding only
  provider wrapping would fail clock identity or lose owner/configuration authority.
- Expiry is not completion. Manual observer `ready` means DB expiry plus drain;
  it must not invoke final assessment, mark goals met, synthesize a result or call
  manager completion. Separate reviewed terminal settlement remains necessary.
- Pause, shutdown, uncertainty and suspended clocks retain ownership/capacity
  according to existing manager/host rules. No resume adoption is implied. A manual
  observer requires an explicit operator cadence; there is no timely background
  expiry guarantee. Arbitrary blocking providers/tools remain cooperative.
- Existing exact in-flight cleanup must remain connected through freeze so claimed
  turns can persist terminal receipts/activity without granting new model/tool work.
  Failed persistence retains owner; no retry of inference or silent resource close.

## Required acceptance before claiming a usable timed application

Use inert providers and isolated disposable PostgreSQL, not external targets:

- One authenticated application, actual admitted positive configuration, installed
  owner and prepared receipt; goals perform no stamp. No owned tick means no model.
- First planner or first worker call stamps once; distinct profiles and later turns
  share the same lease/nonexpanding ceiling. Streaming creation is inert.
- Exact clock callable propagation, drift/backwards samples, invalid duration and
  close-time expiry; transport timeout uses relative durations with distinct epochs.
- Denied/revoked stamp or dispatch grants, model rotation and callback/resource
  replacement prevent transport. Ambiguous commit never retries or adopts a lease.
- Manual observer races an actual blocked owned operation: freeze cancels, pending
  canonical cleanup drains, DB-not-yet-expired waits, then ready without assessment
  or terminal task mutation. Repeat ticks do not duplicate freeze/cleanup.
- Pause-before-stamp, pause-after-stamp, shutdown and failed projection preserve
  truthful retained inventory. Cross-generation/copy/reused capability rejection.
- Zero-timeout full regression suite; no default threads, schema changes, listeners,
  ambient credentials or extra runtime permissions.

# Persisted planner provider for one restricted final invocation

Design only. No admission gate or executable change is included.

## Source and current gap

`server/task_llm.go:agentsForTask` creates distinct `goals`, `planner`, `worker`
and `mainagent` runtimes. `agent/wrapup.go` selects planner task-timeout wording,
not goals initialization or ordinary per-run wrap-up. Python's final runner is a
narrower read-only assessment: it cannot prove goals, create intents or launch
workers. Binding its model to the persisted **planner** role must not change that
catalog or imply source-complete timeout settlement.

`FinalAssessmentRunner.run` currently calls `provider_resolver(receipt, model)`.
A receipt alone does not authorize execution: it outlives the active invocation.
`TaskProviderResolver.bind` already reselects the persisted role/profile before
and after calls and stream iteration/close, but requires an execution scope and
current/guard pair. Ordinary engine current is intentionally false after freeze.
It cannot be reused or weakened for final assessment.

## Proposed exact API and split

New `agent/final_task_provider.py`:

- `FinalTaskProviderAdapter(resolver: TaskProviderResolver)` requires the exact
  code-owned resolver and pins its stores, executor, factory and original policy
  callbacks. Construction is inert. No profile lookup or transport construction.
- `bind(invocation: FinalProviderInvocation, model: Model) -> ChatBinding` accepts
  only an original live runner-issued invocation, not a receipt or generic bool
  callback. It requires model equality with the original FinalConfiguration.
- It creates a frozen private scope containing task/exploration/run, final config
  digest as revision, attempt ID, bootstrap identity and model. The persisted
  selection role is `planner`; transcript/runtime role remains `final_assessment`.
  Neither an alternate role nor caller-supplied scope is accepted.

Narrow `agent/final_assessment.py` seam, owned separately:

- Optional exact `task_provider` constructor argument, mutually exclusive with
  legacy `provider_resolver`. Legacy explicit host-resolver behavior remains
  unchanged; no implicit adapter/default registration.
- Private-created `FinalProviderInvocation` is allocated only after successful
  journal reserve/start and the runner's initial exact reconciliation. It pins
  runner, original final receipt (including owner/revision), journal, original
  journal guard, executor, transcript and recovery guard, configuration digest,
  local cancellation event, original clock and bounded invocation deadline.
- Original object identity is held by the runner. Copies, foreign runners,
  another thread/attempt, changed fields, closed tokens and reused tokens fail.
  Public receipt fields do not mint this capability.
- `validate_execution()` performs existing `_check(execution=True)` and exact
  pins, including reconciliation, outside the provider guard. A distinct
  callback-free `current(scope)` checks the original active invocation, thread,
  cancellation event, scope/config/resource pins. It does **not** reconcile SQL
  or acquire the journal guard. This avoids nested non-reentrant lifetime guards
  when TaskProviderResolver invokes current from inside its guard.
- The adapter's guard bridge uses the captured original
  `journal.guard(task_id, exploration_id)`; no ordinary engine guard/current is
  substituted. Before and after resolver/bound-provider operations the outer
  adapter calls validate_execution; the resolver retains independent persisted
  task/profile grants and its own configuration guard.
- Revocation is code-owned and irreversible. Revoke immediately when ChatAgent
  returns, before record_finalized or any finalization callback, and in a finally
  block for every exception/BaseException. Revoke before cleanup callbacks can
  initiate another call. Runner cancel sets the existing local event, never the
  manager token. Persistence after cancellation remains the existing separately
  authorized transcript/journal path, not this invocation's authority.

`OwnedFinalization` currently wraps `plan.provider_resolver` in a lambda, erasing
an exact adapter type. Composition therefore needs a separate optional typed
`FinalizationPlan.task_provider` field, mutually exclusive with its legacy resolver,
validated/pinned before allocation and passed explicitly to FinalAssessmentRunner.
Do not duck-type bind methods or hide the adapter in that callback. Plan/callback
pins in `timed_lifecycle.py` must include this new field when that later composition
is assigned; no such executable changes are part of this proposal.

## Provider/context and close boundaries

The returned wrapper supports complete and lazy stream. No selection, transport
or model call occurs until actual resolver/model use; no stream transport before
first next. Each operation validates the original invocation, exact actual
RuntimeContext metadata (task/exp/run/final digest/attempt/bootstrap), cancellation,
finite remaining duration, then delegates to the existing persisted-role binding.
Repeat invocation/context fences after callbacks and before disclosure.

The context uses the runner's original clock. Clamp the same context to the
remaining independent final-run duration, shortening only. Never use the expired
ordinary task clock as final-model budget, extend an existing context deadline,
compare different monotonic epochs, or make zero remaining mean unlimited.
Existing HTTPTransport receives the same context and applies relative duration
bounds. Synchronous arbitrary providers remain cooperative and cannot be forcibly
interrupted by this adapter.

Iterator close is always attempted exactly once. Execution fences apply after
successful close; an originating provider/cancellation exception is preserved.
Cleanup must still close an existing iterator after revocation without granting
new model/read/tool permission. A close failure or loss of invocation/configuration
fences cannot produce a successful final result or automatic retry. The active
manual owner continues to cover the invocation/cleanup tail.

## Persisted configuration contract

Use the same resolver instance that selected FinalConfiguration.model. Existing
HMAC model identity checks detect role binding, active chain/profile or private
configuration changes; selection errors/exhaustion do not silently fall back.
The final configuration additionally pins its explicit prompt, policy revision,
restricted catalog and finite limits. These are not inferred from model output
or from ordinary planner prompts. Persisted planner changes before/during a call
reject the binding even if the receipt remains valid. Resolver restart identity
is not a recovery credential; pending attempts are not adopted.

The resolver's configuration `current` and read grants must explicitly permit
configuration reads during the final phase. The adapter does not broaden those
policies. Journal authorization, model Grant, graph read grant and persistence
approval remain independent and mandatory. This slice does not auto-build an
entire FinalizationPlan or certify schema readiness.

## Acceptance before composition

Use actual journal + canonical transcript + TaskProviderResolver/ProfileFactory
with fake HTTP only: distinct planner/goals/worker profiles; lazy first stream
iteration; changed role/profile before dispatch and after yield; revoked/canceled
same-thread invocation; copied token/wrong attempt/context; reentrant callback
before/after final record; iterator exception/close cancellation; finite budget
consumed during grants; non-reentrant guard; constructor no SQL/transport;
missing schema no call; one canonical canceled receipt and no replay. Verify
post-ChatAgent and exception-retained bindings cannot execute. Actual PG follows
independent review, not external providers.

## Registration evidence limitation

Registration57 independent tests passed, but PG113's full persisted-role app
correctly refused changing a genuine zero-timeout prepared generation to synthetic
positive pins: its durable timeout configuration digest no longer matched.
No permit, stamp or next model call was authorized. This is fail-closed evidence,
not successful natural positive admission. Narrow manual-factory prerequisites
must be reported separately. Positive entry and automatic timers remain disabled.

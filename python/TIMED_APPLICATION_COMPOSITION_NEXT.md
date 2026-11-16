# Natural timed admission: concrete remaining composition

Read-only follow-up after standalone frontier/unstamped prerequisites. The runner
hook is independently evolving; this document does not open admission gates.
Reuse the existing ONE TaskRuntimeApplication/manager/host/manual registry. Do not
introduce another manager, runtime bundle, generic success callback or timer whose
lifetime the existing listener cannot see.

## What is already concrete

TaskRuntimeAssembly now binds each positive timeout into provisional/persisted
configuration identity (zero digest compatibility retained). PreparedTaskTiming
constructs one original TaskDeadlineStore/OwnedGenerationClock after preparation;
planner/worker wrappers lazily stamp at actual dispatch and clamp the same runtime
context. Goals remain unwrapped. OwnedExpiryObserver, restricted finalization,
scoped-permit early and unstamped stores, tracked frontier controller and
ManualActionOwner already exist. The new runner hook is the proper pre-active-
frame frontier seam; no second completion implementation is needed.

## Smallest useful next composition, with gates still closed initially

Add an exact frozen `TimedLifecyclePolicy` to the existing task runtime setup,
with explicit policy revision, frontier permit issuance/authorization,
observation/freeze grants, final start/journal/model/read/persistence/settlement
grants, canonical transcript and finite final configuration. Timing/cleanup
policies remain required. The final role is explicitly selected using the source
planner role, not a new implicit provider fallback. Provider/model/profile identity
and final limits/prompt/catalog enter the durable configuration before admission.
No credentials, tools, workspace root, process permission or schema install is
inferred from this policy.

Bind this policy to the actual application after its sole host/manual registry
exists. A code-owned method validates original bundle/host/runner/executor and
pins them once. This is composition on the existing application, not a new
lifecycle registry. An explicit host-issued grant must be scoped to each actual
prepared receipt; timeout values or database flags cannot issue it themselves.

### The missing post-prepare registration hook

Do not install actions inside BootstrapHandle's active prepared frame if binding
requires idle handle evidence. The smallest seam is EngineRunner.advance:

1. Invoke the original handle.advance normally under host.advance_owned.
2. When it returns bootstrap_prepared, the outer/child active frames have ended,
   but the host owned-work reservation still excludes pump/poll/admission overlap.
3. Before returning to the pump, a code-owned typed binder obtains the original
   PreparedTaskTiming.store/kernel and exact prepared receipt, constructs and
   registers observer, finalizer and frontier controller with the EXISTING manual
   owner, then binds frontier to the exact runner handle.
4. Retain all constructed objects on partial failure and fence that handle from
   further advance. Missing registration must never permit the next planner tick.
   A distinct retained setup-failure marker is needed; do not rely only on the
   pump's global failure latch or silently discard partially registered objects.

The callback is not an arbitrary factory permitted to return a boolean. Accept
only the exact policy/binder class whose products are the reviewed code-owned
components. Registration may read/grant configuration as explicitly authorized,
but no model/clock stamp/observer tick/final action occurs there. The constructors
remain inert and no provisional run/task identity is fabricated.

Expose exact registered actions by original handle/generation to host code.
Never resolve an arbitrary ambient task into a recovered lease. A manual observer
call can then progress independently of a blocked engine pump, and explicit
finalization uses the already registered original coordinator.

## Remaining execution gates and engine semantics

Only after that composition is accepted should one exact timed admission
capability be threaded through bootstrap_setup, EngineRunner._prepare and
BootstrapHandle construction. The default capability remains absent. Keep
inherited/seed/revival/initialized-positive paths rejected. Preflight must verify
bounded integer timeout and policy/schema availability before task insertion;
no model work is allowed in ready().

Engine._snapshot still explicitly requires timeout_seconds=0. Its positive branch
must verify exact prepared timeout/config and distinguish unstamped NULL clock
from original attached nonexpired lease, rather than remove the SQL predicate.
It must preserve source no-open-frontier behavior for the timed path and never
resume a paused intent to manufacture work. The new frontier hook handles ordinary
done before a real call, including no-lease completion. Existing zero behavior
remains unchanged until separately reconciled with source semantics.

Known dispatch budget exhaustion must be tested through actual engine cleanup,
not only the provider helper. A real positive task expiring before transport or
while a cooperative provider returns needs ordinary freeze/drain progression;
unknown effects alone warrant quarantine. No positive gate should be loosened
until this end-to-end behavior is verified without mutating prepared fixture pins.

## Schema and final-model binding still need actual assembly work

Read-only setup compatibility checks must validate existing explicit bootstrap,
final-assessment, final-settlement, stamped-early and unstamped receipt schemas.
Their fresh installers remain explicit operator actions, never startup/HTTP side
effects. No table-exists-only flag suffices for contract compatibility.

TaskProviderResolver currently has goals/planner/worker/mainagent roles and uses
runtime-scoped binding. Final inference needs a narrow adapter selecting the
persisted planner model while using the final journal's actual restricted scope
and final-run authority, not the cancelled/frozen ordinary execution current.
It must revalidate the same prepared configuration/profile and independently
finite final budget. This is a concrete missing adapter, not permission to use
an arbitrary generic provider or stale model identity. All original task/profile
read grants still apply. Restricted read tools do not gain prove_goal permission.

Current final settlement requires local confirmed proof for met goals and rejects
inherited/goalless/unknown evidence. This is a documented narrower final-phase
subset, unlike ordinary frontier_drained semantics. Natural timed admission
cannot advertise full Go final-planner parity while proof writes, goal-less final
states or broader source evidence are unsupported.

## Observation cadence and operational limits

A registration-only composition can be tested safely with manual observe/finalize
calls, but is not an autonomous timed-task implementation. Source deadline
coordination progresses independently of blocked workers and keeps absolute time
through pause. Before advertising natural timed behavior, require an explicitly
started independent bounded observation driver (or an explicit operator/manual
mode with that limitation stated). It must use the registered exact actions,
retain each invocation with ManualActionOwner and join before executor closure.
No default thread, silent background final model or pump-only deadline loop.

Resume/lease transfer after pause, process restart recovery, multiprocess
ownership, forced termination of arbitrary blocked synchronous providers, and
immediate GoalMet cancellation with active workers remain unsupported. Pause
before first dispatch must preserve NULL clocks; a stamped pause must not reset
absolute expiry. Shutdown may legitimately retain resources on uncertainty.

## Concrete next acceptance sequence

First implement only typed post-prepare registration on existing setup, leaving
all positive gates closed; test inert construction, partial-registration retention,
wrong-host/receipt rejection, original provider/grant binding and listener seal.
Then implement the actual final-profile adapter and schema-read compatibility.
Next test the positive engine branch + explicit observation cadence together in
one isolated candidate before enabling its opt-in admission capability.

Final acceptance must use natural authenticated HTTP create(timeout>0), actual PG
and fake HTTP model: goals call leaves clocks NULL; empty/abandoned/paused frontier
can retire unstamped; otherwise first planner/worker dispatch stamps once, both
roles share the same clock, blocked pump permits independent observation, cleanup
drains, final restricted call runs once only after DB expiry, and receipt/registry
retirement survives tracked shutdown. No prepared-pin mutation in that acceptance.

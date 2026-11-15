# Create → initialize → run: proposed owned-generation bridge

Status: explicit components implemented and independently reviewed; isolated
create/bootstrap/engine acceptance passed (ISOLATED_QA.md stage 49). The optional
assembly helper has a separate review gate. This document records the
design contracts, not a deployment or parity claim. The standalone `created`-only
entry remains separate from the typed admitted `running` entry. No automatic
model work belongs in `ready()` or construction.

## Current mismatch and preserved contracts

`TaskManager.create_task` calls pure validation/readiness, then `TaskStore.create`
(atomically creates exploration, origin, task), then `_start`. `_start` registers
an exact cancellation token, persists `running`, calls runner.start, and installs
the returned handle. AgentHost prevents pump/settlement during that mutation.
The bootstrap journal currently accepts only `created`. EngineRunner accepts only
initialized graphs with a host-provided durable EngineScope. Neither can safely
be bypassed by running goals in readiness or inventing a local run ID.

Keep the source HTTP create result and status semantics: accepted task creation
returns its DTO after owned handle installation, before model work; `running`
includes initialization. Capacity is reserved by the existing shared admission
guard across create/start and occupied until actual pause/settlement. Do not
silently execute work outside that guard or return a fabricated 202/completion.

## One runner, one externally owned handle

Extend EngineRunner with an explicitly enabled initialization mode (not a fake
AgentRunner single-turn subclass and not a second hidden EngineRunner/AgentHost).
Its existing initialized-only mode stays unchanged.

A new BootstrapHandle implements cancel/wait/unresolved/task_id/token/advance and
settle_completed. It owns the **same exact manager token** for its whole life:

1. `installed`: constructed and registered by runner.start, no DB journal/model
   calls. Snapshot includes task/exploration IDs and immutable configuration.
2. `initializing`: first explicit pump advance reserves the durable journal run
   ID and runs the actual GoalPlanner coordinator once. This happens only through
   AgentHost.advance_owned after manager handle installation. No automatic thread.
3. `prepared`: journal and persisted goals/lineage verified; no task completion.
4. `engine`: construct an inert actual TaskEngineHandle using the journal run ID,
   exact token, same task/exploration/config revision/executor and guard. Install
   as a child of the original handle; defer its first advance until the next tick.
5. `unresolved`: retain ownership and block automatic retry/admission. Never
   convert cancellation/idle/model end-turn into successful task completion.

The outer handle stays in runner inventory throughout; no gap swapping ownership.
`wait(0)` means no active callback/child work, not completed. Cancellation before
first tick needs no journal; cancellation during bootstrap cooperatively stops
and drains it, retaining uncertain effects. Cancellation after preparation but
before child installation must be checked both before/after construction. Any
constructed child is retained even if cancellation wins. Never cancel the manager
token merely because goals become met.

## Exact installed-generation capability

Add a narrow read-only TaskManager method, e.g.
`owns_generation(task_id, token, handle, *, allow_cancelled=False) -> bool`.
Under the manager lock, require exact `_runs` token and installed handle identity,
valid nondeleted row and appropriate lifecycle. No provider/DB callback under this
lock. Bootstrap execution requires uncancelled, installed, running/unpaused/
unqueued; persistence may only use an explicitly separate same-generation policy.
Parent lifetime guard and SQL checks remain necessary, not replaced by this method.

BootstrapHandle binds this capability and the existing host lifetime guard. Do
not pass a generic always-true current callback as an admission substitute. All
host/task policy writers must share the same lifetime barrier. Process-local exact
ownership is not a distributed lease; this first bridge remains single-host.

## Journal changes, narrowly separated

Preserve `BootstrapCoordinator.run` created-only mode. Add an explicit admitted
entry point requiring the installed-generation capability and exact handle/token.
Only that entry point permits SQL task status `running`; every claim/finalization
uses exact task/exploration, no pause/queue/delete/archive, same config snapshot,
and capability checks before/after SQL while holding the lifetime guard.

Claim allocates the BIGSERIAL run ID only after owned handle installation. The
journal must persist immutable config identity including provider/profile,
prompt/catalog/tool policy revisions and original task text. Prepared replay must
revalidate saved IDs/lineage/current policy; never steal running/unresolved rows.
A cancelled pre-claim handle can be retried with new ownership; an interrupted
claimed generation requires explicit reconciliation. First version can refuse
resume after preparation under a new manager token rather than invent a durable
cross-generation adoption policy. Do not promise automatic crash recovery.

## Pure preflight / defaults

Explicit readiness checks provider bindings, current profile, transcript/storage
capabilities, scope/constraint approval adapters, supported options and bootstrap
journal installation without model calls or writes. Both provisional-create and
persisted task preflight are necessary. No unsupported seed-first-intent,
deadline, inherited-scope or fallback behavior is accepted and then silently
ignored. TaskStore.create is reused; no duplicate origins/graphs.

Expected operational failure after task creation cannot be made atomic with SQL
and external model work. Return/retain real task identity and paused/unresolved
state where applicable; never claim the task was not created after it was.

## Fleet implementation split

- Bootstrap owner: admitted coordinator entry point + BootstrapHandle; exact
  journal/run/config binding, cancellation/drain, delayed engine construction.
- Foundation runner owner: optional explicit EngineRunner initialization factory
  and pure preflight path; union handle inventory, exact host/executor ownership,
  no initialized-mode regression; pump should need no new private access.
- Manager/host owner: narrow owns_generation method and integration tests proving
  no early tick before installed handle; lifecycle lock ordering review.
- HTTP/factory owner: explicit new hosted-factory options only after runtime
  review; preserve facade/capacity guard, no default bootstrap enablement.
- Independent QA: actual fake-provider path HTTP create → real TaskStore/journal
  → GoalPlanner/tools/JSONL → prepared → actual engine planner/worker → fresh-goal
  settlement. Temporary PostgreSQL only, separately authorized by coordinator.

Required race tests: pause during installation/claim/model/finish/child creation;
foreign token/handle/executor; capacity full; duplicate advance; retained callback
replay; old generation after resume; SQL commit uncertainty; missing scope grant;
prepared receipt graph tampering; no task done on bootstrap end-turn; concurrent
poll cannot retire before manager installation. Child effects never begin in
constructor/readiness. Every failure retains a truthful owner or proves no work.

# Tracked standalone frontier completion

Implemented `agent/frontier_completion.py` with exact frozen
FrontierCompletionPolicy and OwnedFrontierCompletion. Construction is inert;
explicit same-host ManualActionOwner registration is mandatory before direct
run_once. No runner, engine, bootstrap, runtime or public timeout gate changed.

The read-only bounded candidate verifies the exact positive prepared generation,
original task/bootstrap/configuration and clock under the host guard. Open goals
or open/running intents return waiting; actual busy handle returns busy; attached
expired original clock returns expiry_required. These do not allocate a terminal
store/capability or consume the action. Empty/met/abandoned goals and paused-only
intents use source ordinary frontier semantics, not achievement/proof semantics.

Ready chooses existing exact stamped or unstamped store, using the original
scoped irreversible permit. The chosen store repeats all authority and SQL
checks. No dummy clock/model call or lease adoption occurs. Once attempted,
uncertainty is sticky and no branch fallback/replay is allowed. An advisory race
can conservatively retain uncertainty even when no terminal write is known;
there is no safe automatic handback protocol yet.

A successful store result is `awaiting_runner_removal`, not fully drained.
ManualActionSnapshot now has separate `pending_removal` (default zero), distinct
from sticky `unresolved`; either makes drained false. Active tracking spans the
entire store call, including token cancellation after manager retirement. Normal
pending removal survives method return and sealing. Read-only
confirm_runner_removal checks the original pinned actual runner inventory; it
clears pending only after the original handle is absent. It performs no SQL,
model, admission, inventory mutation or generic completion callback. The exact
already-retired continuation is allowed after seal; new run_once is not.

No automatic host hook exists. A future runner settlement branch must invoke the
controller before entering outer/engine active completion frames and remove only
the exact completed handle. This component never calls the runner's whole
settlement pass, which could act on unrelated tasks. Immediate all-met completion
with active workers remains separate. Positive admission remains disabled.

Eight authored fixture tests cover stamped/unstamped source frontier, repeatable
waiting, busy/expired nonallocation, seal/cancellation, sticky insert uncertainty,
actual post-retirement blocked cancellation tail, and explicit runner-removal
confirmation. Fixtures use real manager/handle/runner and synthetic positive
prepared pins/inert SQL, not natural timed admission. The standalone scope passed 12 tests (including four independent cases) and
actual PostgreSQL stage108 acceptance in four groups. Existing 11 registry, 7 listener
composition and 17 finalization tests pass.


## Separate in-progress runner/accessor stage

The subsequent runner hook and retirement-result accessor are a distinct stage,
not part of standalone PG108 acceptance. The accessor pins the exact consumed
capability object **and semantic fields**, original immutable committed result,
store/cancellation bookkeeping and original atomic manager retirement reader.
The callback-free registry snapshot checks terminal metadata and absence of any
run, clock/completion reservation or cleanup ownership together. It does not
query SQL or grant permission. Current accessor tests include unchanged
independent cap-mutation and reattached-run regressions; new hook acceptance is
pending its independent review and fresh PostgreSQL run. Positive admission is
still closed; earlier PG evidence used explicitly synthetic positive pins.

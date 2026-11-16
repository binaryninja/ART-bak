# Explicit frontier runner hook: scope and evidence

## Implemented boundary

`EngineRunner.bind_frontier_completion(handle, component)` explicitly binds one
original prepared positive-timeout generation to its exact registered
`OwnedFrontierCompletion`. Binding validates the original host, manager, executor,
prepared receipt, generation and manual-action owner; it does not invoke model,
tool, grant or settlement SQL callbacks. No controller is constructed implicitly.

`AgentHost.owns_settlement(runner)` checks the original same-thread settlement
slot without callbacks. The runner's frontier branch requires that slot and runs
before ordinary handle completion. Unbound positive generations do not fall back
to zero-timeout completion. Zero/default completion behavior remains unchanged.

Committed retirement is established through the controller's private retained
result/evidence accessor, not a writable public result. The runner rechecks actual
handle drain and unresolved state, revalidates evidence after callback-bearing
waits, then compares/removes only the original inventory handle and binding.
The binding survives failed removal confirmation. Its exact read-only continuation
can finish after manual-action admission seals, without repeating settlement SQL.
Pending bindings also prevent admitting a replacement generation under that key.
The host settlement slot covers the entire callback tail, including the interval
after manager retirement and before removal confirmation.

`early_settling` remains pending in host deadline inventory. Process ownership is
not inferred from canceled tokens or finalized transcripts: actual handle wait
and unresolved checks include the independent prepared process-effect ledger.

## Verification

- Independent focused review: **43 tests passed**, covering hook, private accessor,
  zero/bootstrap behavior, host lifecycle, and combined process ownership.
- Frozen Hook52 checkpoint: **3,042 tests passed**, with **472 SQL checks** and
  inventory validation reported clear by the coordinating integration run.
- PG109 reported **four hook groups** exercised against PostgreSQL. A real-PG
  positive process-plus-hook execution remained blocked by the existing
  `timeout_seconds=0` execution gate. That blocked path is not a passing test and
  is not evidence of natural positive-timeout process execution.
- Separately, combined process/frontier tests use actual authored host, manager,
  bootstrap, engine, worker, process owner, cleanup owner and controller with an
  inert injected runner and SQL doubles. They configure the process owner before
  real bootstrap preparation, rather than fabricating active or uncertain flags.
  Existing synthetic positive-timeout fixture pins remain test-only.

Those combined tests demonstrate that an active runner keeps host polling busy
and drain false; invalid/uncertain runner returns retain ownership and prevent
terminal writes, removal and replay even when the fixture's SQL frontier is ready.
A genuine contractual drained return allows a later explicit poll to complete.
Cancellation does not convert an invalid drain receipt into safe retirement.
Other regressions cover direct-runner denial, open-frontier waiting, expiry
handoff, stamped/unstamped retirement, public-result substitution, exact evidence,
post-retirement cancellation tails, and sealed pending-removal continuation.

## Explicit limitations

This is an explicit binding hook, not automatic application lifecycle assembly.
Natural positive-timeout admission and positive process execution remain gated.
No automatic timer, observer, finalizer, provider invocation, recovery/adoption,
background process, external command or target access is enabled by this hook.
The acceptance runners are inert. A valid injected runner return is a contractual
synchronous-drain statement, not proof of OS containment or descendant cleanup.
Immediate all-goals-met cancellation with active workers remains a separate gap;
this work does not establish full source or SDK parity.

## Owned files for this slice

Implementation:
- `artex/agent/host.py`
- `artex/agent/engine_runner.py`

Owner tests/helpers:
- `tests/test_frontier_runner.py`
- `tests/frontier_process_fixture.py`
- `tests/test_frontier_process_retention.py`

Independent review files are maintained by the reviewer:
- `tests/test_frontier_runner_review.py`
- `tests/test_frontier_process_review.py`
- `tests/test_frontier_retirement_review.py`

The controller private accessor and its tests are architecture-owned and belong
in the same integration checkpoint; they were not edited by the host/runner owner.
`FRONTIER_RUNNER_HOOK_PLAN.md` records the prerequisite design. Its future-tense
hook steps are now implemented to the limited extent described above; its
automatic-assembly and positive-admission exclusions still apply.

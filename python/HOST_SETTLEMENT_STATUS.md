# Host settlement ownership

`artex.agent.host.AgentHost.poll()` now retains a thread-owned settlement slot
across the entire synchronous settlement pass. It releases the host condition
lock before invoking runner settlement, inventory, handle waits or owner inventory.
The slot remains occupied even when the manager has already recorded completion
and the runner has removed its last handle but a callback has not yet returned.
It is cleared and waiting threads are notified in `finally`, including when a
callback raises a `BaseException`.

## Observable behavior

- A concurrent poll returns `PollResult(busy=True)`. It does not report an empty
  inventory as proof that the other invocation has finished.
- Same-thread recursive poll or drain raises `TaskConflict`, rather than waiting
  on its own invocation. Admissions and lifecycle mutations remain fenced during
  settlement; `advance_owned` returns busy instead of running another callback.
- `drain(timeout)` includes an existing settlement slot in its condition wait.
  Zero or expired remaining time returns an unsuccessful, busy drain. A completed
  callback notifies the waiter so it can continue normal handle and owner checks.
- Existing receipt validation, exact manager/runner identity checks and retained
  deadline ownership checks remain in force. A callback exception does not turn
  an unresolved handle into a successful settlement.

## Limits

This is synchronous host serialization, not a background scheduler, deadline
finalizer, process containment mechanism or new completion permission. It does not
invoke the standalone stamped or unstamped completion stores, register manual
finalization actions, enable positive-timeout admission, or alter zero-timeout
completion policy. The ordinary zero-timeout runner and bounded cooperative drain
paths remain available.

The timeout bounds waiting for an already occupied slot and cooperative handle
waits. It cannot preempt arbitrary Python callbacks, blocked database adapters or
a new settlement callback invoked by the drain itself. An unsuccessful drain is
not permission to discard resources, close the database pool or terminate work.
The slot covers calls made through this host; it is not a cross-process lock and
does not make direct out-of-contract runner calls safe. Clearing the slot on an
exception only ends this invocation's ownership; it does not reconcile partial
external effects or authorize an automatic retry.

## Verification

`test_host_settlement_slot.py` and independent
`test_host_settlement_review.py` exercise post-manager-retirement and inventory
callback tails, concurrent busy/zero-drain behavior, admission/advance fences,
same-thread reentry, exception cleanup and waiter notification. Together with
`test_agent_host.py`, 18 tests passed on the reviewed source. These tests use
controlled threads and inert providers/callbacks, not real model requests,
subprocesses or a deployed service. PostgreSQL and listener acceptance are
separate from this local verification.

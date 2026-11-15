# Pause-safe deadline ownership: next coordinated change

Design for the coordinated ownership changes. An internal partial implementation
is described in `DEADLINE_OWNERSHIP_STATUS.md`; positive runtime task timeouts
remain rejected. Retaining a Python token is not durable authority and does not
enable automatic expiry. The plan below includes capabilities still unavailable.

## Verified current boundary

`TaskManager.pause` cancels the installed execution token, waits for the handle,
persists pause, then removes `_runs[task_id]`. `EngineRunner.settle_completed`
independently retires cancelled drained handles. `TaskDeadlineStore._check`
requires the original installed, uncancelled manager generation on every access.
Consequently a retained handle or `allow_cancelled=True` cannot read or settle a
paused task clock after successful pause. `BootstrapHandle` correctly refuses
fresh-token adoption of an existing prepared journal receipt.

`test_deadline_pause_boundary.py` exercises the real authored manager/host/runner
with synthetic persistence: after drained pause the former owner cannot validate
the clock and performs no SQL; polling retires it without completion. Positive
creation options fail before persistence. These are intentional protections, not
bugs to remove to make a lease helper appear functional.

## Smallest coherent internal slice

A **manager-owned process-local lease** must be installed while the exact
execution generation still exists, at the first successfully persisted positive
clock. Its identity includes: exact manager/host/repository/executor; task and
exploration IDs; full immutable prepared bootstrap receipt (request/config/owner,
run and journal revision); immutable first-run/deadline timestamps; timeout; exact
shared lifecycle guard. Owner credentials must not appear in DTOs/logs.

A lease is separate from an execution attachment. A paused lease retains clock
identity but confers no provider/tool/graph-mutation permission. It is not a new
CancellationToken and must never make `owns_generation` return true for a retired
run. No persistence-authority callback supplied by the caller may self-certify a
lease. Only the manager's exact retained object, validated against the same host
and SQL receipt, can enter lease-only persistence operations.

Suggested internal API (names are proposals):

- `manager.reserve_deadline_owner(task_id, token, handle)` returns a single-use
  registration reservation only for the exact installed run. It reserves pause /
  retirement coordination, **not** model or SQL authority.
- Existing prepared-owner stamp performs its normal exact SQL checks. After
  success, `manager.install_deadline_owner(reservation, receipt, clock)` binds
  that concrete persisted clock to the installed generation. If pause/cancellation
  races or stamp outcome is uncertain, retain a quarantined reservation rather
  than dropping the only possible clock owner or reissuing a stamp.
- `manager.deadline_owners()` exposes a bounded immutable inventory to the owning
  host, independent of runner handles and ordinary execution-capacity counts.
- `manager.validate_deadline_owner(lease, operation)` recognizes only supported
  non-execution operations. Initially these are exact read / quarantine / release
  after verified terminal state. Expiry finalization is unavailable until its
  separate reviewed coordinator exists.
- `manager.pause` records a suspended attachment after confirmed drain and pause
  commit; it does not delete an installed lease. Persistence uncertainty leaves
  the lease and run cancellation gate unresolved. It may remove the old execution
  run only after recording the lease transition. No callback/provider is called
  under the manager registry lock.
- Runner retirement must ask the manager to acknowledge the exact detached
  generation before forgetting its handle. Retiring an execution handle never
  releases the lease. Host drain/shutdown includes outstanding lease work even
  though an idle paused lease does not occupy execution capacity.

Do not expose arbitrary `install(Receipt)` or `resume(new_token)` APIs: the former
could manufacture ownership and the latter could adopt another configuration.

## Lock ordering and uncertain installation

Never hold the manager lock while calling `handle.validate_prepared_receipt`,
waiting for a child, entering an arbitrary host guard, or invoking SQL. Bootstrap
and engine callbacks already enter their condition/lifetime locks then call
manager ownership checks; reversing that order risks deadlock.

Reserve under the manager lock, release it, acquire the original lifecycle guard,
validate handle and persisted identities, then compare-and-install the exact
reservation under the manager lock. Every exit completes or quarantines that
reservation. Pause sees and respects reservations before removing ownership.
An attempted/committed stamp followed by lost authority cannot be treated as an
uninstalled no-op. This handshake must be implemented together with pause and
runner retirement, not as an independently usable helper.

## Persistence extension, not a weakened existing check

Keep `TaskDeadlineStore.stamp/validate_current` execution-owner rules unchanged.
Add a distinct lease-bound read operation only after the manager capability
exists. Under the original guard it locks task then journal, rechecks full
prepared receipt identity and immutable clock, allows the specifically suspended
state, rejects deletion/archive/replacement, and checks the retained lease before
and after SQL. No UPDATE, re-stamp, deadline reset, or generic initialized-task
fallback. Revalidate the exact original executor/guard and committed clock.

Same-host resume needs a separate, atomically transferred attachment to a fresh
execution token. The old token stays cancelled. Resume retains the original
clock and full config/receipt; after freeze it fails. Until the explicit prepared
adoption path and transfer race tests exist, resume of a leased task must fail
closed **before** `apply_admission`, not write running then discover the mismatch.
No crash recovery, cross-host adoption, or multi-process lease claim is included.
A restarted process encountering a positive stamped task must visibly refuse
adoption; a name or timestamp alone is not ownership proof.

## Proposed exclusive file ownership

1. Manager owner: `manager.py`, dedicated lease manager tests. Own exact registry,
   reservation/install transitions, pause and completion hooks, pre-admission
   fail-closed leased-task behavior. No app factory changes.
2. Host/runner owner: `agent/host.py`, `agent/engine_runner.py`, dedicated retirement
   and drain tests. Coordinate actual lease inventory / retirement handshake and
   capacity separation, without a watcher or default background thread.
3. Persistence/clock owner: `db/task_deadline.py`, `agent/deadline.py`, dedicated
   stamp-to-registration and lease-read tests. Wire successful/uncertain stamp
   to the real manager handshake; do not merely export an unused lease class.
4. Independent reviewer: exact-token races, lock inversion, partial commits,
   post-pause no-execution guarantees, and restart refusal. Temporary PostgreSQL
   QA must exercise full receipt / timestamp fences after unit review.

Bootstrap prepared-adoption, the freeze/drain/final-assessment coordinator, and
runtime budget hooks remain subsequent coordinated work. No owner removes the
nonzero-timeout rejection in this slice. Synthetic tests may exercise ownership
transitions with explicit fixture receipts, but must say they are not positive
runtime timeout acceptance.

## Required race acceptance

- Pause before reservation, during stamp, after commit and before lease install.
- Stamp commit uncertainty retains reservation and blocks a second generation.
- Concurrent duplicate registration never produces two lease owners.
- Pause failure never publishes a clean suspended lease or frees ambiguous work.
- Drained pause releases execution capacity but preserves exact lease inventory.
- Old token/handle cannot authorize models, tools, graph writes or ordinary
  `manager.complete`, even while its lease is retained.
- Foreign host/executor/config/journal revision/clock cannot be rebound.
- Runner polling cannot lose a lease by independently retiring its attachment.
- Shutdown/drain reports retained uncertainty honestly and never claims expiry
  finalization, because this slice has no final-assessment coordinator.
- Resume is refused before durable admission until transfer/adoption is reviewed.
- Concurrent terminal completion preserves done and releases only the exact
  verified terminal lease; ambiguous commit retains ownership.

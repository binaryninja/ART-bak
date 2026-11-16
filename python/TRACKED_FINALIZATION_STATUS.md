# Tracked manual finalization and shutdown

Implemented `agent/manual_action_owner.py` plus connected observer/finalizer hooks
and optional application/listener composition. `track_manual_actions=True` creates
one inert registry for the application's existing exact host; default is None.
Explicitly register original observers and finalizers before their first call.
Registration does not grant model, database, graph or terminal authority and does
not discover/adopt untracked work.

The bounded registry pins original host/manager/runner/executor identities. Sealing
is irreversible; direct public tick/claim/run_once calls on tracked objects cannot
bypass it. A finalizer must share its original observer's owner. Exact invocation
receipts remain active through outermost finally, not merely until a result is
available or the manager's deadline slot disappears. Copied completion receipts
cannot release work. Cancellation callbacks run outside registry locks.

The listener pins this optional registry and its original operations. Shutdown
seals manual admission before host callbacks, requests cooperative cancellation,
and requires both host settlement and registry drain before resource/pool closure.
It releases its listener lock while calling shutdown operations or waiting, so an
active finalizer can finish. A confirmed SQL settlement followed by a still-active
Python return/unwind tail continues to block closure even after manager retirement.
Uncertain allocated work remains visible and prevents a clean drain.

Seven registry owner tests and four independent registry tests passed, along with
27 observer/finalization regressions. Seven setup/listener tests passed, including
an actual shared-host/pool controlled finalization tail and retained cancellation.
Independent integrated review cleared40 focused tests. Frozen full suite2892
tests458 SQL checks passes. Actual PostgreSQL stage101 verifies natural zero
application/listener sealing and orderly closure separately from synthetic
positive-owner registry retirement-tail/cancellation. Full persisted-role timed
application shutdown remains unproven behind unchanged admission/config guards.
See ISOLATED_QA.md for exact manifests and scope separation.

No positive timeout admission, automatic observer/finalizer, background timer,
recovery, resume transfer or default activation was added. Existing untracked
component APIs remain unchanged and are not covered by this registry's shutdown
guarantee. Arbitrary provider/host callbacks are cooperative, not forcibly killed;
a timeout or postcommit uncertainty retains resources rather than claiming success
or rollback. Registry tracking does not reconcile quarantined SQL/canonical state.

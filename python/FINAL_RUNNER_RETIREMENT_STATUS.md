# Registered final-settlement runner retirement

This candidate closes a distinct lifecycle tail discovered during genuine
positive-duration PostgreSQL acceptance: final settlement retired manager ownership,
but the original engine runner still retained its unused frontier binding. Missing
manager ownership is not treated as generic success or an expiry wait.

An actual complete timed lifecycle registration now binds the original finalizer
to the original runner/actions while the host owns the preparation frame. The
binding is inert, one-time, and requires no SQL, grant, stamp or provider call.
Standalone finalizers without that explicit registration keep their prior API.

After successful real FinalSettlementStore settlement, the finalizer retains exact
consumed settlement evidence: original result, capability and finalized receipt
objects plus their field values, original journal/configuration/handoff/clock and
resource identities. The callback-free `retirement_result(runner)` checks those
pins and the original atomic manager retirement snapshot: matching terminal
status/time, no execution generation, no retained deadline/unstamped/cleanup
owner, and the original cancellation token cancelled. No caller-created receipt
or missing registry slot alone can supply this evidence.

The runner's original settlement slot checks private evidence, actual drain and
identity again, then compare-removes only the original handle. It retains its
bindings if the read-only removal confirmation fails. The finalizer's known
pending-removal state stays in ManualActionOwner inventory, including after
sealing; only this exact read-only continuation may proceed after seal. No new
SQL, model call, admission or settlement retry is permitted. Active finalizer
and confirmation invocations continue to block resource shutdown through the
post-manager-retirement tail. Unknown effects remain unresolved rather than
being converted to known pending removal.

Seven owner tests currently pass using an actual registered lifecycle and actual
journal/settlement components with inert SQL/provider. Tests explicitly disclose
synthetic positive prerequisite configuration; they are not natural-admission
acceptance. Those were component-only evidence; subsequent independent and actual PostgreSQL
acceptance is recorded below. The earlier
freeze-disposition fix is separate: legitimate frozen work may wait normally,
but copied/quarantined/missing ownership must still fail.

Independent review reproduced two exact-proof gaps: replacing the journal's
retained finalized receipt with an equal copy, and replacing an integer receipt
field with an equal float. Both unchanged regressions now pass after requiring
original journal receipt identity and recursive type-sensitive immutable value
snapshots. Unsupported field types fail closed; datetime inspection admits only
standard-library timezone implementations, not arbitrary tzinfo callbacks.
Twelve combined retirement tests, seventeen existing finalization tests and
eleven manual-owner tests pass locally. Fresh independent review subsequently passed 28 owner/reviewer/runner/freeze
tests. Stage130 actual PostgreSQL verifies genuine admission, expiry settlement,
post-finalization poll and empty runner/manual inventories, including after seal.
Seventeen focused retirement tests also pass on that frozen snapshot. See
ISOLATED_QA.md for exact evidence and limits.

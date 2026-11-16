# Unstamped ordinary completion prerequisite

Implemented: explicit fresh `artex_unstamped_completion` installer and
`UnstampedCompletionStore(exact_TaskDeadlineStore, grant=permit, authorize=...)`.
The deadline store is used only for its original prepared owner/resource binding;
it has no lease, and neither clock timestamp is invented or written. The timeout
must be the exact positive configured/prepared value. Existing positive runtime
admission gates remain closed.

A separate manager reservation atomically excludes both first-clock reservation
and execution. It requires the original installed handle/token, no other clock
or completion reservation and no cleanup, then verifies actual zero-wait drain
outside the registry lock. Admission, pause retirement, ordinary completion and
runner retirement refuse retained unstamped ownership. `deadline_owners()` includes
this pending lifetime as reserved/quarantined, without representing it as a lease.
Current ordinary host polling can refuse on this fenced generation; no automatic
host hook or clean-drain success is claimed.

Original guard/task/bootstrap locking protects exact configuration and both NULL
clock columns. Source frontier means no open goals and no open/running intents:
empty/abandoned/met goals and persisted paused intents do not require proof or
resume. Task done and the `clock_state=unstamped`, `reason=frontier_drained` receipt
are atomic SQL writes. The scoped irreversible EarlyCompletionGrant protocol is
reused unchanged. Final policy precedes complete persisted verification; pure
permit acknowledgement is held through private evidence/registry retirement.
Token cancellation is outside its lock. Commit uncertainty retains quarantine,
including possible durable done with locally unacknowledged ownership. No retry,
receipt adoption, model call, final assessment or fabricated deadline exists.

Authored tests use actual installed manager/bootstrap resources and inert SQL,
with explicitly synthetic positive prepared timeout pins. They exercise both
reservation race orders and a concurrent single-winner race, null/partial clock
checks, paused/empty frontier, insert uncertainty, final grant/readback revocation,
and no clock fabrication. Independent review and actual PostgreSQL acceptance
are pending. This does not establish natural HTTP positive-timeout admission.

The standalone invocation is not yet covered by ManualActionOwner. Manager
retirement is not invocation completion: the cancellation tail still needs the
future tracked host integration before same-application executor shutdown safety
can be advertised. The immediate all-met/active-worker source branch remains a
separate gap.

## Verified component checkpoint

Independent owner, source and concurrency review passes 19 tests. Exact isolated
PostgreSQL stage105 passes eleven groups, including NULL-clock preservation,
actual first-stamp exclusion, six reservation races, final durable drift and
revocation, rollback and committed cancellation uncertainty. Full frozen suite:
2959 collected tests and 470 SQL syntax checks. See ISOLATED_QA.md for manifest
and the explicit synthetic prepared-positive fixture limitation. No public
admission, full engine hook or tracked invocation-tail integration is enabled.

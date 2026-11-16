# Explicit early frontier completion prerequisite

The standalone EarlyCompletionStore preserves Go's ordinary frontier-drained
meaning: no open goals and no open/running intents. Empty or abandoned goals and
paused intents do not block this source branch. A done receipt with reason
frontier_drained is not a claim that all goals were achieved or proved.

The store binds one original attached, unexpired prepared clock owner. It reserves
a distinct manager capability, fences new execution, requires actual zero-wait
owner drain, and verifies task, bootstrap identity and bounded frontier under the
original guard and SQL locks. Task status and the fresh additive receipt are
written atomically. Unknown or unconfirmed effects retain quarantined ownership;
no inference, automatic retry or fabricated expiry/final assessment is involved.

The unpublished Boolean authorization design failed independent and real-PG
callback-boundary tests. Repeated callback/readback checks cannot establish
arbitrary callback stability. It was replaced by an explicit EarlyCompletionGrant
scoped to the exact prepared receipt and operations. Every policy callback must
return the same permit; Boolean and copied/wrong-scope approvals are rejected.
Revocation is irreversible. After final durable verification, a pure permit check
and registry acknowledgement share the permit lock, without SQL/policy callbacks.
Concurrent revocation linearizes before acknowledgement (refused) or afterwards.
Token cancellation occurs outside both locks; failure retains committed SQL and
quarantined ownership rather than claiming rollback or automatic retry.

Independent owner, semantics and permission tests pass (25 collected). Exact
PostgreSQL permit revision passes nine groups, including the original database-
drift failure, final readback permit revocation and cancellation failure. Frozen
combined suite: 2940 tests and 465 SQL syntax checks. Original failed stage103
manifest is retained separately; ISOLATED_QA.md describes the API-fixture migration.

This is a standalone idle-owner component. Public positive admission, automatic
engine/host completion, unstamped positive-task handling, immediate all-goals-met
cancellation of active workers, and ManualActionOwner invocation-tail integration
are NOT enabled. Same-application listener lifetime safety is not claimed for
this standalone store. See EARLY_COMPLETION_HOST_INTEGRATION_PLAN.md for proposals.

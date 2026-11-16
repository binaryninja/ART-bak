# Notification overview read port

Standalone `GET /api/notify/meta` reader and service: independent review cleared
13 focused tests, and frozen PostgreSQL acceptance PG120 passed five actual SQL
groups plus those 13 tests. Optional composition below is a separate next slice.
No delivery, retry, notifier, timer, channel mutation, listener or default route
is constructed by this component.

## Source contract

`server/notify_api.go:notifyMeta`, `db/notification.go:NotificationStatsSnapshot`,
`db/settings.go:GetBool`, and `server/notifier.go` define the response. The frontend
consumer is `web/src/app/(main)/system/notify/page.tsx` through `api.notifyMeta`.
Existing Python `notify.kinds()` and `CHANNELS` supply sorted kind names, default
rates and secret-key **names**, never secret values or channel configurations.

Only `notify_enabled`, `notify_public_base_url`, and
`notify_digest_interval_min` are read. Missing enabled defaults true; present
values are true only for exact `true` or `1`, including present-empty false.
The digest remains the saved string (missing is empty), distinct from numeric
`defaults.digest_interval_min = 30`. The URL is returned as saved data, not visited.

The single aggregate query preserves six source counters. Pending includes
pending and sending; backlog age considers pending only. Sent-today uses the
PostgreSQL session's `date_trunc('day', now())`, not an invented UTC boundary.
Future-dated pending entries can yield negative backlog age; it is not clamped.
Settings and statistics are separate queries as in the source, not claimed to be
one atomic cross-query snapshot. No statistics are fabricated on storage failure.

## Boundaries

An explicit original executor, finite SQL-side cumulative settings byte bound,
and strict scalar/row validation are required. Counts are nonnegative signed
64-bit integers; backlog age is signed. Invalid/oversized settings and failed SQL
return redacted failure. This intentionally differs from source settings helpers
that swallow storage errors into defaults.

Independent global operational and configuration-disclosure grants are required
before SQL and after SQL, serialization and guard exit. Configuration can disclose
an internal public-base hostname, so JWT or aggregate read permission alone is
not sufficient. Original callbacks, resource identities and request selection
are pinned. Finite cooperative checks are not a universal fixed point across
arbitrarily mutually mutating policies.

Tests cover exact defaults/order/raw digest/boolean handling, session-day SQL and
negative ages, both grants, guard/serialization revocation, SQL sentinel/type
failure, the three-key-only projection, and rejection of mutations/unknown query.
No actual user settings, database, external endpoint or secret was accessed.

## Optional metadata overlay

`bind_metadata_reads(notification_meta_service=...)` explicitly installs protected
GET `/api/notify/meta`, returning `bundle.notification_meta`. An exact original
service/reader, shared executor, bounded settings and unchanged channel metadata
are required. No default service is inferred. Existing routes remain unchanged;
conflicting GET/HEAD declarations are rejected before callbacks or storage reads.

The outer original JWT/global grant chain invokes the independent operational
and configuration disclosure grants with current/resource checks before and after
serialization, before headers, and after `start_response`. Late revocation aborts
body delivery; it cannot replace already-emitted headers. No credential values,
network send, model invocation, mutation, or notification timer is enabled.

Four new synthetic composition tests and two strict scanner tests pass; together
with selected standalone/older overlay tests, 27 pass. Five independent review
tests passed. PostgreSQL stage 121 passed four actual SQL/JWT groups and 22
focused executions. The scanner accepts only the
exact protected optional declaration, not arbitrary new routes. The route inventory is generated from the reviewed frozen snapshot.

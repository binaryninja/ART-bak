# Notification delivery history: unregistered read slice

`db/notification_history.py` / `server/notification_history.py` implement the read
DTO contract of `GET /api/notify/deliveries`, sourced from
`server/notify_api.go:notifyListDeliveries/toNotifyDeliveryDTO` and
`db/notification_delivery.go:ListNotificationDeliveries`. No transport, notifier,
queue, retry, channel mutation or automatic route registration is initialized.

Exact state/event_kind filters remain arbitrary strings; positive channel_id
filters one channel, invalid/nonpositive source selectors mean global scope.
The explicit authorization receives this exact immutable selector, so a host must
not mistake invalid channel input for a narrow channel permission. Unknown or
duplicate query keys, bodies, oversized filters and offset>1,000,000 are rejected.

Count joins delivery+event, while page additionally joins channel, as source does.
A missing channel can therefore leave total greater than returned rows. One SQL
statement makes both projections snapshot-consistent. Page orders descending ID;
page defaults1, page_size defaults50; effective page size>200 falls back50 while
HTTP echoes the positive requested value (the source's observable discrepancy).
Nullable last_error becomes empty string; absent batch_id/sent_at are omitted.
Finding IDs serialize as decimal strings. Snapshot name falls back to vulnclass;
severity is event-time snapshot data, never a current finding lookup. No placeholder
is invented for an empty title. Disabled channels are not filtered from history.

No channel credentials/config, raw snapshot or summary is returned. The bounded
snapshot text is needed for source title/severity decoding. SQL cumulative byte
sentinels suppress transfer of oversized page text; defaults/cap1MiB, rows<=200.
Overflow refuses the complete response, never truncates diagnostic history.
Datetime validation accepts exact datetime plus built-in timezone/ZoneInfo, with
bounded RFC3339 output. Strict scalar and exact selected-row checks reject bad
adapter results. Ordinary PostgreSQL JSONB snapshots are supported; malformed
JSON from an adapter fails closed instead of pretending an empty successful read.

An independent original read grant AND raw diagnostic grant authorize error and
snapshot-derived disclosure. Pinned resource/current/guard/policy checks repeat
before SQL, after SQL, after serialization and after guard exit. Original read
permission is rechecked after raw authorization callbacks. No arbitrary mutating
policy fixed-point guarantee is claimed; original JWT still belongs to eventual
mounting transport and is not equivalent to these explicit grants.

Seven inert tests pass for DTO/null/large ID behavior, source filters/count join,
pagination echo/effective limits, missing channel sentinel, raw denial, final
serialization/guard revocation, callback pins and invalid/oversize refusal.
Independent review and isolated real-driver acceptance remain pending. No actual
notifications, user records, credentials or live databases were accessed.

## Verification

Ten owner/independent tests and three real isolated PostgreSQL stage100 groups
pass against the documented immutable overlay. Exact source fields, joins, byte
sentinels and raw disclosure revocation are verified, not sends or route wiring.
Combined checkpoint2871 collected tests458 SQL syntax checks pass.
See ISOLATED_QA.md for actual foreign-key and snapshot coverage limits.

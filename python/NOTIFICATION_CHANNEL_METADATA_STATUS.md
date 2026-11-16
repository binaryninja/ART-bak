# Notification channel metadata PATCH

Standalone, unregistered `PATCH /api/notify/channels/{id}` subset. Seven synthetic
transaction tests and six independent review cases pass. PostgreSQL stage 124
passed four actual transaction groups and thirteen focused executions. No actual user channels, credentials or deliveries were changed.

Only name, enabled, mode, rate_per_min and filter are accepted. Config/kind are
rejected even when null; creation, deletion, test-send and retry are absent.
Source: `server/notify_api.go:notifyUpdateChannel`,
`db/notification.go:SaveNotificationChannel/SetNotificationChannelEnabled`.

The selected row is locked by exact positive ID. Only ID/enabled are read, so
stored configuration and unknown configuration fields are not even fetched.
UPDATE assigns only explicitly supplied metadata fields, never config or kind.
Omission/null of supported fields means unchanged; explicit false and zero remain
meaningful. Name uses source Go whitespace trimming; mode is realtime/digest;
rate is nonnegative; filter severity is constrained. JSON/filter bounds and exact
scalar types are stricter than the source's tolerant partial decoding. Empty
patches check existence/authority without a mutation.

Disabling a previously enabled channel marks pending and sending deliveries
skipped; other states remain unchanged. Metadata and delivery updates occur in
**one transaction**, deliberately stronger than the source handler's separate
configuration save and enabled-toggle transactions. Stored diagnostic text is
English (`Channel disabled`) rather than the source localized sentence.

The host must supply an immutable scope/revision, exclusive lifecycle guard,
current check, store write grant and independent request write grant. The exact
normalized immutable proposal is passed to both policies, and original resources,
request/selector and proposal are checked around callbacks. Grants are rechecked
before SQL effects, immediately before transaction exit and after commit/guard
exit. No read authority or JWT is treated as mutation approval.

After a mutation attempt, any SQL, grant, commit or acknowledgement failure returns
an uncertain outcome with retry=false, even when a fixture knows rollback occurred.
There is no automatic retry or false rollback assertion. This is not a durable
idempotency/reconciliation service or an atomic policy fixed point.

Tests cover byte-for-byte preservation of stored unknown/secret config, omitted/
null versus zero/false, source defaults/filter checks, missing rows, grant denial
before SQL, atomic rollback when delivery skipping fails, late revocation and
commit acknowledgement loss with already-persisted changes. No route/default
registration or network/runtime notification activation is included.

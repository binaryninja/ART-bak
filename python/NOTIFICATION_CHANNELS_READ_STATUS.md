# Redacted notification channel list

Standalone GET `/api/notify/channels`, not registered or activated. Eight synthetic
tests pass; independent review and actual PostgreSQL acceptance remain pending.
No sends, channel changes, timers, provider calls or remote resources are used.

Source: `server/notify_api.go:notifyListChannels/toNotifyChannelDTO`,
`db/notification.go:ListNotificationChannels`, `notify/mask.go`, and channel cfg*
consumers. Source ordering enabled DESC then ID, `{channels:[...]}`, scalar DTO,
rate and timestamp fields are retained. Filter absent/null slices remain null;
explicit empty slices remain empty. Recognized case-insensitive filter keys are
supported. Invalid structural types and colliding case aliases fail closed rather
than exposing source decoder partial-error output. This is a deliberate stricter
subset, not complete malformed-row parity.

## Deliberately safer presentation than upstream

Source masking reveals credential suffixes and copies undeclared config keys.
This reader instead returns constant `__masked__` for present declared secrets,
including entire nested headers. Unknown kinds return `{}`. Unknown keys are
omitted. No credential suffix or raw-config bypass is available.

Inspected allowlist:
- Email: host, port, tls, from, to, username. Password is masked.
- Telegram: chat_id and a restricted base_url. Bot token is masked.
- Webhook: recognized method only. URL and headers are masked; body_template and
  content_type are omitted rather than echoing arbitrary embedded material.
- DingTalk, Feishu and WeCom: declared credential masks only.

Allowed fields require expected scalar/list types. Host accepts only hostname/IP
characters. URL userinfo, query, fragment, non-root path, malformed/unsupported URL
or control characters yield a constant mask. Address/identity strings reject URL
shapes and query/fragment markers. Personal addresses and account names remain
configuration information and require a separate explicit configuration grant.
No heuristic can establish that arbitrary text placed in a nominally nonsecret
field is actually nonsensitive; the allowlist is a schema boundary, not a content
classification claim. This display-only subset is **not** supported for roundtrip
channel editing. Existing global `notify.mask_config` is unchanged.

## Bounds and authority

One parameter-bound query selects max_rows+1 and enforces cumulative text/JSON
bytes in SQL before payload transfer; overflow refuses the entire list instead
of truncation. Recursive JSON depth/count, UTF-8, row shape, ordering, ID uniqueness
and exact integer/boolean types are checked. Only already-authorized redacted DTOs
reach serialization. No notifier or channel transport is instantiated.

Original executor, bounds, channel specifications, allowlist and request are
pinned. Independent operational and configuration grants plus current/guard
checks surround reads, serialization and guard exit. Failures are redacted.
These finite cooperative checks do not assert an atomic policy fixed point.

Tests cover suffix/container/unknown-key suppression, URL risks, source DTO/null
filter distinctions, SQL overflow/duplicate-order refusal, grants and final
serialization/guard-exit revocation, and no query without both permissions.

## Optional metadata composition (separate follow-up)

`bind_metadata_reads(notification_channels_service=...)` adds only protected GET
`/api/notify/channels`, exposing the exact original service as
`bundle.notification_channels`. No source reader changes, implicit service,
mutation, sending or roundtrip editing support are introduced. Original methods,
shared executor, bounds and channel/allowlist data remain pinned.

The existing outer JWT/global checks now include the separate original channel
operational and configuration grants plus current checks, including after
`start_response`. Late revocation withholds the body, without claiming to replace
already-emitted headers. Exact GET/HEAD route conflicts are rejected atomically.

Four new composition tests and two narrow scanner tests pass. With standalone
owner/reviewer and prior overview composition tests, 24 selected tests pass.
Standalone URL redaction review cleared 14 tests before its channels66 freeze;
optional composition review and its separate PostgreSQL acceptance are pending.
The strict scanner accepts only the exact optional guard/path declaration. No
moving-source inventory was regenerated.

## Frozen acceptance

Standalone stage 122 passed four actual SQL groups and fourteen focused tests.
Optional wrapper stage 123 passed four actual SQL/JWT groups and twenty-three
focused executions. Constant masks and final disclosure fences were tested; no
notification was sent. See ISOLATED_QA.md for exact scope and fixture correction.

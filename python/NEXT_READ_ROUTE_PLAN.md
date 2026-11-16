# Next meaningful read-only services

Source-only audit of the current ROUTE_PARITY missing-GET entries, checked against
Go handlers and existing Python adapters. Declaration absence is not evidence that
no component exists. Tool-config and tool-rule reads are already under review/QA;
exclude them from new work. No registration, source changes or execution here.

## 1. Intercept history detail: `GET /api/intercept/history/{id}`

**Value:** shows the recorded reason and actual reviewed context behind a decision,
not merely a pending/history summary. Source: `server/server.go:926`,
`server/intercept.go:interceptDetail`, `db/intercept_detail.go:GetInterceptDetail`.
The source DTO embeds InterceptApprovalRow plus `audit` (explicit null for old
records). It must not reconstruct missing audit context or call it model reasoning.

**Dependencies / sensitivity:** existing `db/intercept_history.py` supports only
pending/one/history; its `one` is not the richer audit detail. Add a bounded exact-ID
projection joining original approval attribution and stored audit JSON. Pin an
explicit task/conversation/global read scope before disclosure, with a separate raw
review-context grant. Audit may contain user text, model_input, tool input/output,
profile ID and decision reasons. SQL byte sentinel before transfer, bounded JSON
validation and original-authority final fences are necessary. No model/provider
needed; schema already has audit. No approval mutation or execution navigation.

**Minimum tests:** old audit null; exact source omitted fields; real-driver missing
row 404; malformed/oversized audit refuses without text leakage; cross-task or
conversation denial; deletion and grant revocation during serialization/guard exit;
actual persisted snapshot remains distinct from current prompts/configuration.

## 2. Judge metering: `GET /api/intercept/judge/usage`

**Value:** real cumulative cost visibility independent of debug record retention.
Source: `server/server.go:933`, `server/intercept.go:interceptJudgeUsage`,
`db/llm_usage.go:JudgeUsageStats`. All-time calls/input/output/cache totals filter
exactly `worker='judge'`; `days` only bounds the ascending UTC daily series.
Nonpositive days defaults to 30. Daily entries contain date/calls/input/output,
not cache totals. No zero-filled missing dates are invented.

**Dependencies / sensitivity:** actual llm_usage schema/executor exists; current
Python records adapter has task metering but not this global judge aggregate.
Require explicit global usage authorization plus request/current/guard fences.
No profile secret, provider or model dependency. Aggregate organizational activity
is lower sensitivity than raw audit, but JWT/task read alone is insufficient.
A finite maximum days is a documented stricter bound. Source's absent-PG zeros
must not become a fake healthy adapter: require configured storage or explicit
unsupported response. Consider one consistent snapshot for totals/series, rather
than claiming source's two separate queries are atomic.

**Minimum tests:** non-judge rows excluded; failed/interrupted judge calls included;
old rows affect totals but not recent daily; UTC midnight boundary; empty ledger
zero totals with empty daily; strict days parsing/bounds; final revocation; no
inference that absent ledger rows mean no external judge activity occurred.

## 3. Notification delivery history: `GET /api/notify/deliveries`

**Value:** real delivery outcome/attempt/backoff visibility without sending/retrying.
Source: `server/server.go:810`, `server/notify_api.go:notifyListDeliveries` and
`toNotifyDeliveryDTO`, `db/notification_delivery.go:ListNotificationDeliveries`.
Returns deliveries/total/page/page_size; filters state,event_kind,channel_id;
rows ordered delivery ID descending. Store clamps page>=1, invalid page size to50
(max200); preserve/document source HTTP-vs-effective pagination distinction.
DTO includes attempts,last_error,batch/time fields and snapshot severity/title;
title uses snapshot name, otherwise vuln_class.

**Dependencies / sensitivity:** notification tables and source joins need a new
read adapter; Python notify currently supplies offline primitives, not delivery
storage. Require explicit global or exact-channel read authorization and separate
raw-error/snapshot disclosure permission. Do not expose channel config credentials
or parse arbitrary endpoint content as trustworthy diagnostics. Bound selected
snapshot/error text before transfer and totals/pagination work. No notifier,
network transport, model, retry queue or settings mutation may be initialized.

**Minimum tests:** actual join/filter/count/order and missing channel/event behavior;
source page-size fallback; snapshot title fallback; nullable sent_at/batch fields;
oversize/error redaction; exact original selected channel grant after serialization;
constructor and reads perform no enqueue/retry/send side effects.

## Deferred boundaries

Execution navigation (`/api/intercept/history/{id}/execution`) needs exact correlated
call/result retrieval and source 410/409/deleted-conversation distinctions; do not
alias it to detail or return an invented empty result. Settings GET needs truthful
runtime flags and secret-safe proxy/profile views, not fabricated defaults. Update,
ScopeSentry and skill filesystem routes have network/process/filesystem dependencies
and are not simple read-only SQL slices. All three proposals above still require
independent review and isolated real-driver acceptance before optional wiring.

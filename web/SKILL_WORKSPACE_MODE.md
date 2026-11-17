# Skills host configured-mode discovery

The explicit Python Skills launcher defaults to read-only. A Python build profile
alone does not imply that its filesystem host has enabled writes. This UI queries
`GET /api/skills-workspace` with `cache: "no-store"` and the existing authentication
transport. It requires HTTP 200 and exactly
`{"profile":"skills-workspace","writes_enabled":boolean}`. The response is a
configured-mode hint, not a grant, proof of current authority, or promise that a
future mutation will succeed. The host still checks every request.

## Fail-closed client behavior

Non-mock Python Skills pages start with writes disabled. Unchecked/loading,
unauthorized, denied, unsupported (including older/default apps returning 404),
malformed and failed discovery states keep writes disabled and show a mode status
or error with an explicit read-only recheck. Read-only confirmation allows browsing,
local draft editing/copying and catalog refresh, but not saving or creation. The
New skill sheet can still open to retain or copy a local draft; its Create action
is disabled. Existing file and inline-name drafts are not discarded by mode checks.

Each explicit recheck closes the write gate immediately, before a React render.
Only its latest owned response may reopen that gate. Replaced or canceled reads
are aborted and their late completion ignored. Effect cleanup retires its pending
owner, including manual rechecks and cleanup/setup replay. There is no polling,
automatic retry or mutation timeout. A slow read can be replaced using Recheck
host mode. The modeled gate is checked synchronously by root creation, file save,
file upsert and directory creation, not just rendered disabled buttons.

A discovery refresh never clears existing root/file unknown-outcome latches,
changes selection, resets drafts, cancels an already dispatched write, or treats a
write refusal as success. A write already in flight retains its existing receipt
and draft-ownership handling even if a later discovery read reports read-only.

Go and MOCK keep their existing behavior and do not call this Python-only route.
A Python MOCK build retains the existing restricted UI while using its fixture
writes; it does not discover or authorize a real filesystem host.

## Limits and validation

The hint can become stale after it is read. There is no persistent authorization,
automatic revocation guarantee, or client override of host root/lease/quarantine
checks. The existing in-memory mutation uncertainty restrictions remain in force;
reloading the page does not override authoritative host quarantine.

Tests cover exact API transport, invalid/unauthorized/unsupported responses,
initial and refreshed write gates, read ownership/replay, draft retention,
uncertainty preservation and unchanged Go/mock behavior. Source/model/callback
checks and production builds are not mounted React/browser acceptance. Final
combined native HTTP/API integration and exact-tree evidence are recorded by the
coordinating review, without retrying the previously denied browser startup.

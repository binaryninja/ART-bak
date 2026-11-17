# Scoped Skills workspace UI

Based on remote `0d64395c2f5847ff2257acff929cf8613f2d16b7`, exact tree
`58355362c460c4ba9841a2bec4e65811c722b305`. This frontend change does not
create backend services, mount filesystem routes or grant filesystem access.
The separately reviewed method-aware composer/explicit host must supply the
catalog, content reader, content writer and directory creator.

## Concrete corrections

The original Skills page accepted late file reads into whichever draft was
visible, unconditionally cleared dirty after save, and synchronized its inline
creation guard only after React rendered. A late create also changed selection.

The page now uses a directly tested, dependency-free workspace model through
`useSyncExternalStore`:

- Drafts, baselines, read tokens and revision counters are keyed by exact
  `(skill, relative path)`, including Unicode names. Selection is recorded
  synchronously so stale-render save/edit callbacks cannot act on a new view.
- Effect cleanup retires only its owned read, allowing replay and A/B/A
  navigation even if the previous request hangs. Late reads/errors/finally do
  not replace a newer read or a loaded/edited draft. Loaded drafts are cached
  until the page unmounts; catalog refresh does not reload file contents. If a
  settled mutation retired the sole initial reader, an explicit Retry read state
  replaces the otherwise ownerless loader, including successful mkdir at a stale
  selected-file path. Active replacement readers and specific read errors are
  preserved. Retry never clears uncertainty.
- One pending save/upsert/mkdir per skill is recorded immediately before await.
  Repeated same-turn callbacks cannot dispatch twice. Different skills remain
  independent. Save success advances only the submitted baseline, preserving
  newer text and recomputing dirty by content equality, not edit history. A read
  hydration during empty-file upsert is not mistaken for a user edit; actual
  edits after that hydration are retained.
- Explicit pending/unknown/no-retry failures and invalid mutation receipts
  latch further writes for that skill in the mounted workspace. There is no
  automatic retry. Read or catalog success does not clear the latch. The warning
  directs the operator to reconcile the actual filesystem before reopening and
  to preserve any unsaved draft.
- Inline create events carry the exact input-instance owner. Enter/blur repeats,
  stale blur/Escape and older completion cannot consume/clear a newer input.
  Success refreshes only the catalog, never the current selection or draft.
- The new-file action is deliberately an **empty-content PUT upsert**. It always
  asks whether to write an empty file and warns that an existing file will be
  replaced. A cached dirty draft at that path blocks it. This is not atomic
  create-only protection or external-writer CAS. Folder success is described as
  "Folder ready", compatible with Go's idempotent `MkdirAll` result.
- Catalog request ownership preserves the latest result and explicitly displays
  load errors with stale-data wording rather than presenting empty success.

## Wire contracts and profile

Actual Go handlers in `server/server_mgmt.go` and Python skill services agree:
file upsert is HTTP **200** `{ok:true}`, directory creation is HTTP **201**
`{dir:requestedCanonicalPath}`, and file read returns `{file,content}`. Only these
mutation methods gained exact status/receipt validation; unrelated legitimate
HTTP 202 workflows are unchanged. File reads check response path and string
content. Catalog responses require the source-shaped name/files/calls/tasks/
usage_agents fields rather than silently coercing malformed success to empty.
MOCK deep-file PUT and directory POST routes now return these same payloads,
and MOCK GET decodes each path segment once for the content identity. This
retains fixture-only demo behavior; acknowledgements are not real filesystem
persistence. Default demo fixtures and the actual MOCK transport short-circuit
are covered by source-extracted tests, not a running demo/browser.

Relative resource segments are encoded once; traversal/URL syntax is rejected
before a mutation is sent. Server validation remains authoritative.

When the existing build-time `NEXT_PUBLIC_ARTEX_BACKEND_PROFILE=python` is set,
the page presents the restricted existing-skills workspace: catalog, reads,
file upsert and directory creation. ZIP import, top-level skill creation and
all deletion controls are disabled; MCP/visibility/usage panels and their
ancillary requests are omitted. No-ledger catalog zeros are not displayed as
verified usage. The existing explicit Python development host sets this profile.
This is presentation gating, not authorization or a dynamic capability endpoint.
The Go profile retains its other controls and ancillary APIs.

## Limits and verification

`node --test web/tests/skill-workspace.test.mjs` runs 42 authored tests of the
actual pure model and extracted transport/page callbacks with inert APIs.
Coverage includes reversed reads, effect cleanup/replay, same-turn double save,
newer edits, cross-skill requests, unmount, uncertainty, malformed receipts,
encoded Unicode paths, empty-file replacement, input ownership, catalog errors
and restricted-profile request selection. Full Node regression command:

```
node --test web/src/lib/*.test.mjs web/tests/*.test.mjs
```

The Node tests are **not mounted React, a TypeScript check, Next build or browser
acceptance**. The formerly missing frontend dependencies have now been restored
using standard lockfile `npm ci --ignore-scripts` from the official registry.
This makes actual Next route generation, full `tsc --noEmit`, production/static
builds and Biome checks available for the immutable candidate and final combined
tree. Their results must be recorded separately from the pure-model tests.
Repository-wide baseline Biome failures remain distinct from introduced findings;
there is no broad unrelated formatting or lint-rule relaxation in this change.

The narrow lint revision formats only the new module/declaration/test files,
expresses ASCII-control rejection without a control-character regex, gives read
retry an explicit memoized request identity, and avoids an added nested ternary
and non-null assertion in the editor. It preserves the reviewed wire contracts,
unknown latch and draft/operation ownership. A regression covers every ASCII
control byte and the retry identity.

Rendered browser acceptance remains a separate gate, including any browser
startup restrictions. The independent backend/application fixture must establish
auth/Host/Origin plus real scoped filesystem persistence separately. No live
runtime is implied by this patch and no dependency/network restriction was
bypassed.

The in-memory drafts and latches end on page unmount/reload, are not durable or
shared across tabs, and do not cancel issued requests/server work. Users must
save/copy drafts before leaving. Reads cannot reconcile uncertain mutations;
there is no operation-inspection protocol. Known HTTP errors retain the existing
transport classification, which cannot prove rollback on unflagged server errors.
The Go-only upload/delete/metadata/visibility workflows are unchanged and are not
claimed to share the new mutation fence or be race-safe. Backend grants, external
filesystem writers, broad parity, usage ledger truth and runtime execution are
outside this UI patch.

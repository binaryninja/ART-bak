# Built-in tool metadata editor request ownership

This change is limited to the existing built-in ToolEditor. It does not invoke a
tool, alter custom-tool editing/execution, implement Python reset, or change
backend authority, bindings/enablement semantics, routes or shared API transport.

## Request and draft ownership

Save and the existing Restore defaults action share one synchronous owner per
mounted editor. Same-turn repeats and Save/Restore overlap cannot dispatch a
second metadata request while that owner is active, even before React renders
its busy state. Both buttons reflect the same busy state. There is no timeout,
automatic retry or cancellation of an already dispatched mutation.

The editor keeps an immediate latest-draft reference for description, parameter
rows, bindings and enabled state. Save snapshots those current values. Schema
validation still happens before API dispatch, with the existing scalar coercion
rules and structural preservation unchanged. Later edits cannot mutate the sent
payload, and every edit, including same-value edits, retires close authority.
Successful completion refreshes the catalog but closes only the unchanged draft;
otherwise feedback says that newer edits remain unsaved.

Unmounted editor callbacks no longer close another selected/reopened editor,
refresh the catalog, emit stale feedback or update React state. The parent's
existing key-based remount boundary remains intact. A functional parent close
callback also preserves a different queued selection before cleanup commits. Commit-phase layout-effect cleanup retires callbacks before a replacement editor
can become interactive. Cleanup/setup retains
an in-flight owner and restores the correct busy state after an inactive
completion. A failure keeps the local draft and releases only its own busy state.

## Unchanged contracts and limits

Go metadata update and reset return HTTP 200 `{ok:true}`. Python catalog metadata
update has the same successful DTO, while its reset endpoint remains an explicit
503 unavailable response. This slice keeps the existing shared client transport
and receipt/error interpretation unchanged; it does not add retries or stronger
wire receipt guarantees. Metadata values are not execution grants.

Ownership is per mounted editor, not durable idempotency or a per-tool global
lock. Closing/reopening or leaving the page does not cancel the earlier server
request and does not prevent an explicitly submitted request in a fresh editor.
A failed parent catalog refresh now retains known rows (and the mounted editor)
and displays an explicit error with a read-only retry. It no longer masquerades
as an empty catalog and discards a newer local draft after acknowledged Save or
Restore. There is no new server compare-and-swap, external-writer protection,
draft rebase, or catalog reload-ordering redesign. If a request finishes after its editor has
unmounted, the user must refresh the catalog to observe its server-side outcome.

Tests execute the actual editor/helper declarations with inert metadata APIs and
hook doubles, covering same-turn repeats, both overlap orders, synchronous edits,
newer drafts, A-to-B/reopened-A navigation, failures, invalid schema/no dispatch,
cleanup/setup, and the actual parent refresh failure callback. They do not mount
React or run a browser. Exact full suites,
real type/build checks, unchanged-source hashes and independent review are
recorded against the frozen candidate separately. No denied browser startup or
held task-create/runtime/traffic candidate is revisited.

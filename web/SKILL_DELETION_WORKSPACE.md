# Skills file and empty-folder deletion UI

This frontend slice is paired with the separately reviewed, explicit Python
Skills host deletion adapter. It adds no filesystem authority itself and does
not delete user data during its validation. Whole-skill deletion, ZIP installation,
MCP/visibility cleanup and database work are not enabled for Python.

## Permission discovery and wire contract

The legacy exact `{profile:"skills-workspace",writes_enabled:boolean}` mode
response remains accepted with deletion disabled. An exact three-field response
may also contain boolean `deletes_enabled`; true is invalid with writes false.
Loading, unavailable, malformed, unsupported, unauthorized and stale reads cannot
enable Python deletion. Rechecking mode immediately closes the deletion gate and
cannot clear any mutation uncertainty. Mode is a hint, never request authority.
The host requires its separate deletion flag/grant, in addition to write mode.
Go and MOCK do not call this host endpoint; mock receipts are fixture-only.

`DELETE /api/skills/{name}/files/{relative-path}` encodes each validated path
segment once, requires a nonempty string file path before dispatch (never the
separate whole-skill root route), sends no body/query, and accepts only HTTP 200 plus the exact
`{deleted: requestedPath}` receipt. No generic success, queued status, mismatched
identity or uncertainty flag is treated as acknowledgement. The original Go
handler recursively removes directories; that behavior and warning remain for
Go. The Python host refuses nonempty folders, missing/unsafe paths, skill roots
and the case-insensitive top-level required SKILL.md. No recursive or top-level
skill deletion is offered for Python. Nested SKILL.md is ordinary file data.

## Confirmation, ownership and local drafts

The existing confirmation dialog names the exact skill/path and warns that
host deletion is permanent. Python folder copy says empty folders only. Known
unsaved target or descendant drafts prevent dispatch. Save those drafts or keep
editing them; the model does not discard text to make deletion possible.

A synchronous mounted-workspace owner refuses deletion while any tracked root
or per-skill model mutation is pending. Once deletion starts, new model mutations
wait, including other skills. Pre-existing Go-only upload, metadata and visibility
requests are not registered with this model and are not canceled or serialized
by a later deletion; those workflows are not redesigned here. The dialog has its own immediate owner
so repeated same-render clicks cannot dispatch twice. The original dialog alone
may close after an acknowledged result; newer dialogs and selection are retained.
Commit-phase retirement suppresses page callbacks after unmount. Existing Go-only
whole-skill endpoint/payload semantics are not redesigned by this slice.

Acknowledgement never clears selection or cached text. Exact/subtree drafts are
marked deleted, late reads are retired, and Save/upsert/mkdir cannot silently
recreate a deleted path within this mounted workspace. Edits made while deletion
was pending are retained and remain copyable. Cached text is not recoverable host
storage or a reliable backup. Tombstones do not automatically clear after a
catalog or mode refresh; reconcile actual filesystem state before reopening.

A known no-effect 400/401/403/404/409 refusal retains the dialog, draft and any
active file read, and exposes the error. Lost/truncated/mismatched responses,
explicit unknown/retry-false errors and 5xx failures suspend every subsequent
workspace mutation through a separate deletion-uncertainty latch. No timeout,
automatic retry, forced reconciliation or implicit recreation is introduced.
An already-dispatched request is not canceled by a mode refresh or navigation.

## Validation scope and limitations

Tests run the actual pure model and extracted API/dialog declarations with inert
responses, including Unicode, mock mode, legacy profile, all mutation overlap
orders, stale dialogs/reads, known refusal and unknown completion. They do not
mount React, exercise a real browser, delete user files or prove remote behavior.
The independently assembled backend/host union requires authenticated real
synthetic HTTP acceptance and exact combined build checks before publication.

Ownership and tombstones are per mounted page, not durable deduplication or
browser-stale CAS. The host's request-time checks do not prove a target is the
same object the user saw in an earlier catalog. External writers and filesystem
reconciliation remain host/user responsibilities; no stronger guarantee is claimed.

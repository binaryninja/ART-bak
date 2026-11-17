# Single-task control receipt compatibility

This web-only change is based on remote `f240b4b129f846e88f8efb29d5d00902b3453810`,
whose tree is `2a1d0414cd2e736e9c4c8c3d2ad41d8528ee30d3`. It does not activate a
Python runtime, change backend authority, change networking, or add routes.

## Confirmed source contract

`server/server.go`'s single-task `control` handler returns HTTP 200 with the
`taskControlResult` in `server/task_control.go`. Pause returns `paused: true`,
`queued: false`, `status: "paused"`. Resume returns `paused: false` and either
`queued: true, status: "queued"` or `queued: false, status: "running"`. All four
fields, including the string task ID, are serialized (no `omitempty`).

`python/artex/server/tasks.py` also returns HTTP 200 from `manager.control_task`.
The current manager pause and resume/continuation paths return the corresponding
paused/running receipts. Queue admission is accepted for compatibility with Go;
this frontend change does not implement queue admission in Python. The existing
mock single-task route already returns the same four fields.

Only `api.controlTask` requires HTTP 200 and validates the matching task ID and
coherent action/paused/queued/status tuple. It returns only those four fields.
Unreadable or contradictory success, unexpected 2xx, or an explicit
pending/unknown/no-retry result cannot be represented as confirmed success.
Other API methods are unchanged. In particular, legitimate asynchronous HTTP 202
chat acceptance and the separate batch-control contract retain their semantics.

## Detail-view behavior

Previously the detail callback ignored the receipt, inverted its captured pause
boolean, and said "Exploration resumed" even for a queued resume. Two immediate
clicks could dispatch twice. The callback now renders the confirmed receipt and
says "Task queued for resumption" for queued admission.

An immediate per-task ref guard blocks duplicate control and archive actions
while control is pending. An immediate archive ref also prevents archive-first
control and duplicate-archive dispatch before React renders; successful archive
enqueue stays gated until navigation, and failure keeps the existing release
behavior. Controls show
"Updating…" while pending. An unconfirmed outcome shows "Unconfirmed" and blocks
control/archive in this mounted detail view. The existing transport's explicit
pending/unknown/no-retry classification is used; ordinary known errors do not
invent success. No mutation is automatically retried.

Navigation and unmount guards prevent obsolete callbacks from initiating work
or publishing a control completion into another task. Unique poll identities
also prevent an old A response replacing a newer A after A -> B -> A navigation.
Reads begun before or during a control are invalidated at its completion; a new
read then reconciles the view.

The uncertainty latch is **not durable or authoritative**: it survives only
navigation within the same mounted detail view, not reload/remount, another tab,
client, or the separate task list/batch controls. Polling does not clear it, and
no inspection endpoint is invented. Backend errors without explicit uncertainty
flags remain governed by the existing transport. This patch cannot prove such
an error rolled back, eliminate every server uncertainty, or authorize retry.
It does not redesign archive completion or other detail-tab mutation flows.

## Verification and remaining gates

`node --test web/src/lib/*.test.mjs web/tests/*.test.mjs` exercises the full authored
Node suite. The new 21 task-control tests include receipt shapes, actual extracted
transport/callback declarations, queued feedback, duplicate clicks, stale
navigation, A -> B -> A, unmount, poll races, known failures and uncertainty.
Source assertions tie their fixtures to current Go/Python handlers. The VM harness
uses inert deferred responses and minimal state setters; it is **not React
rendering, a TypeScript type check, browser acceptance or a real server run**.

Run the Python regression suite and SQL-capture parser on the same tree:

```
cd python
python -B -m unittest discover -s tests -v
python -B tools/check_sql_syntax.py
```

SQL parsing is not PostgreSQL execution. Next/React dependencies and project
TypeScript/Biome tools are absent in this workspace, so full frontend compilation,
lint, rendered interaction/accessibility and browser-to-Go/Python end-to-end
acceptance remain open gates. No dependency restriction is bypassed. Do not claim
this narrow receipt fix establishes full frontend or Python feature parity.

# Worker-session request ownership

Based on remote `1a3bb045257548c56019851b97ced822ac2c5175`, exact tree
`fc2d0dfa052fc03515ef59b2c5004b38a8a4c7c2`. This frontend-only patch does not
implement or enable backend worker operations, change API routes, authentication,
request bodies, expected statuses or receipt validation.

## Reproduced defects

The original worker-control callback used only captured React state to guard
pending work. Two calls from one render both reached `api.controlIntent`. Its
completion also patched the current worker list and cleared the current deletion
dialog without checking task ownership. The older-worker page loader likewise
could append a previous task's rows, clear a newer request's loading flag, or issue
duplicate requests before React rendered.

## Scope of the correction

- Immediate per-task refs guard control dispatch. Display state mirrors the
  pending map, so finishing A cannot clear B's pending control. A -> B -> A retains
  A's original pending request within the same mounted tab.
- Explicit pending/unknown/no-retry failures latch synchronously by task/intent.
  Their controls remain blocked after navigation back; read success does not
  unlock them. The warning is shown only for the currently selected task.
- Control initiation and completion require the active mounted task and matching
  loaded worker rows. Until the first matching list page loads, worker control
  buttons remain unavailable instead of acting on a previous task's rows.
- Deletion confirmations carry their task and dialog-instance identity. Only the
  original dialog may be cleared by that cancellation's completion; a later
  dialog is preserved. Python's existing cancel restriction and inherited-worker
  guard are unchanged. Go soft/hard cancellation behavior is unchanged.
- First and older pages have unique request ownership. Same-task reads coalesce
  while pending; stale success, failure and completion cannot modify another
  task's rows or loading state, including A -> B -> A. Effect cleanup retires only
  its own pending first-page token, so a hanging old fetch cannot block replay or
  return navigation, nor can an old cleanup clear a replacement effect's read.
- A per-task read generation changes at control start and finish. Reads started
  before a control cannot overwrite its rendered result, and reads do not start
  while that task's control is pending. Completion requests a fresh first page.
  Confirmed patches also update the first-page cache so displacement into older
  rows cannot resurrect the cached pre-control state.

The API still uses its existing HTTP 200 requirement and `confirmedIntentState`
helper. This is not expanded receipt validation, an inspection endpoint, durable
operation identity, server-side compare-and-swap or mutation deduplication. No
mutation is retried automatically. Known errors retain the existing transport
classification; this cannot prove an unflagged server error rolled back. The
uncertainty latch lasts only while this SessionsTab remains mounted, not across
reload, tab teardown, other clients or other task-control screens.

Issued fetches are not aborted by this patch, and server execution is not
canceled by navigation. Main-session creation/chat, worker messaging,
approval/side-question actions and unrelated history/SSE ownership mechanisms
are unchanged. In particular, this does not claim every session mutation or
malformed cancellation response has been made safe.

## Verification and remaining gates

`node --test web/src/lib/*.test.mjs web/tests/*.test.mjs` runs the authored Node
suite. The new `worker-session-ownership.test.mjs` adds 25 tests executing actual
extracted control, first-page, older-page, cache-patch and cleanup declarations
with inert deferred APIs and state setters. It covers repeats, reversed request
order, navigation, unmount, explicit uncertainty, modal ownership, Python cancel
blocking, Go cancellation fixtures and pre-control read invalidation. An existing
source assertion now checks the immediate uncertainty ref rather than the old
captured state guard.

These are source/control-flow tests, **not mounted React scheduling, TypeScript
compilation, browser interaction or live backend integration**. Next, Biome and
project TypeScript executables remain absent here. Build/static export,
lint/type checks and rendered/browser acceptance remain open gates. No network or
dependency restriction is bypassed.

Run full same-tree Python regression and SQL-capture checks with:

```
cd python
python -B -m unittest discover -s tests -v
python -B tools/check_sql_syntax.py
```

SQL parsing is not PostgreSQL execution or proof of full Python parity.

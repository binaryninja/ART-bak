# Explicit clean-file reload in the Skills workspace

Loaded file text is intentionally cached while the Skills page is mounted.
Catalog refresh and switching files do not refresh that text. Previously the only
way to see an external file edit was to reopen the whole page, losing its other
local drafts and in-memory operation state.

The file editor now offers **Reload file** for a loaded, clean, non-deleted draft.
The existing authenticated read transport explicitly uses `cache: "no-store"`.
Reload is a read, not a save, discard action, mutation retry or reconciliation
protocol. It also works when the Python Skills host confirms read-only mode.

## Ownership and draft behavior

- The model acquires the reload owner synchronously. Repeated same-turn clicks
  cannot issue duplicate reads. It refuses reload while the draft is dirty,
  unloaded, already loading or deleted, or when a tracked mutation may affect it.
- Cached text remains visible while reading and after a failure. A failed reload
  identifies the text as cached, retains its baseline and lets the operator
  explicitly try again. Initial-read failures keep the existing Retry read flow.
- Editing retires the reload immediately. Late success, malformed content or
  failure cannot overwrite the edit, even after typing back to the old baseline.
- Selection changes and unmount retire the page's reload owner. Old cleanup and
  completion cannot retire or overwrite a newer owner. Retirement does not cancel
  an already dispatched HTTP request or server work.
- An acknowledged concurrent save/upsert or deletion keeps the existing mutation
  ownership rules. Deleted drafts stay tombstoned and cannot be revived by reload.
- Successful reload changes only the selected file's cached content and baseline.
  It does not clear file/root/import/deletion uncertainty, enable host writes,
  reset other drafts, change selection, or establish filesystem compare-and-swap.

There is still no automatic polling, cross-tab draft storage, external-writer
conflict detection, durable draft recovery, or proof that the observed host text
remains current after the read. The existing generic authentication transport is
unchanged apart from the file GET cache option.

## Verification

`node --test web/tests/skill-reload.test.mjs` exercises the actual model, extracted
page callbacks and file API. The regression includes clean and read-only reload,
Unicode identity, pending mutations, same-turn repeats, canceled ownership,
intervening edits, stale failures, retained cached text, deletion and all existing
unknown-outcome latches. All frontend Node tests, Next-generated route types,
TypeScript and production/preview/static builds are separate checks.

These checks are not mounted React or rendered-browser acceptance. They do not
start a provider/task runtime, change backend authority, inspect held candidates,
or prove backend feature parity. No task-creation or sidebar navigation code is
part of this change.

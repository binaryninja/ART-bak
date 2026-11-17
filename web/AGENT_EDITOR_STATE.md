# Agent editor read identity and draft preservation

Based on remote `dadf8492d8e52fb2269ccfed29f5a806b8ca0d18`, exact tree
`cce0a8a230a74bf5460a0a1a6dea1dfca64857e6`. This is a frontend-only correction,
not a new Python capability, API, persistence format or mutation contract.

## Reproduced defect

The original editor rendered editable default values before its initial GET
finished. The same component could retain agent A's draft while `agentKey`
changed to B, and a delayed A read could replace B's detail/prompt. Executing the
original reload declaration with deferred responses reproduces that mismatch.
Any failed GET, including Python's explicit capability-unavailable HTTP 503, was
also labeled "Agent not found".

The public `AgentEditor` now keys an internal session by `agentKey`, isolating its
fields, selections and dialogs. Editable tabs are absent until a verified initial
read completes. Unique request tokens and mount cleanup reject stale read
success, error and completion, including overlapping reloads. The response must
have the requested key and a nonempty string ID, matching Go's `agentDTO` and
Python's `Agent.to_dict`. Reuse of a key for a different persisted ID blocks the
form and asks the user to reopen it instead of adopting the old agent's draft.

Only HTTP 404 is reported as not found. Other read failures have a distinct error
and a read-only Retry action. Failed reads do not automatically retry mutations.

## Draft behavior

The first matching read hydrates the editable fields. Later reads refresh server
history, variables, profile choices and code defaults without replacing local
prompt, configuration, wrap-up or resource-selection drafts. This also preserves
edits made while a save or refresh was pending, and across a transient read error
and Retry within the same mounted editor.

An explicitly acknowledged reset may replace only its own prompt/turn fields,
and only if their current values still equal the values when that reset began.
Newer edits win. A small synchronous ref-backed state group for those five fields
also covers edits queued before React renders; functional prompt insertion uses
the current draft. Acknowledged resets remain pending until a current detail read
succeeds, so another refresh does not discard a concurrent reset result.

Local form values intentionally remain drafts, not a live mirror of normalized
or externally changed server configuration. Reopen the editor to discard drafts
and read all persisted fields again. This does not add durable draft storage,
unsaved-navigation confirmation, server-side compare-and-swap, mutation retry
safety, request deduplication or cancellation of already-issued mutations. Existing
mutation success/error semantics and global catalog loading remain unchanged;
unsupported Python writes still fail explicitly. Global tool-binding optimism
and unrelated subeditor mutation races are not redesigned here.

## Evidence and remaining gates

Run `node --test web/src/lib/*.test.mjs web/tests/*.test.mjs` for the full authored
Node suite. `agent-editor-state.test.mjs` adds 17 dependency-free tests of the
actual extracted reload/reset/draft-setter declarations, deferred request order,
identity, unmount, error/retry and draft/reset behavior. Source assertions cover
the keyed session boundary and the existing Go/Python response shapes.

These tests use inert responses and minimal state setters. They are **not React
rendering, hook-scheduler acceptance, TypeScript compilation, browser navigation
or live Go/Python integration**. Next/React dependencies and project TypeScript
and Biome executables remain absent in this workspace. Build, static export,
project lint/type checks and rendered/browser acceptance remain required gates;
no network or dependency restriction is bypassed.

Run the same-tree Python suite and SQL-capture parser for backend regression:

```
cd python
python -B -m unittest discover -s tests -v
python -B tools/check_sql_syntax.py
```

SQL parsing is not PostgreSQL execution or full Python parity acceptance.

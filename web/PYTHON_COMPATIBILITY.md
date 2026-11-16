# Experimental Python compatibility profile

Set `NEXT_PUBLIC_ARTEX_BACKEND_PROFILE=python` explicitly when building a frontend
for the optional Python adapters. Unset or any other value preserves the Go UI
capability defaults. This setting does not install routes, grant permissions,
activate interception, or establish full frontend/backend parity.

The profile disables intent deletion and regex rule creation/edit/toggling.
Existing regex rules are displayed, not represented as executable. Deletion of
such a rule can still be requested with its original version. Python durable
interception is separately opt-in and conversation-only: this page does not
enable task interception, judge fallback, or human-approval execution.

Global tool rule updates, toggles, and deletes send the exact selected
`updated_at` as `X-Rule-Updated-At`, preserving fractional precision. A conflict
requires reloading and reviewing the changed record, not automatic retry.
Tool configuration GET must return `version_digest` in the Python profile; PUT
returns it unchanged as `X-Intercept-Tools-Digest`. It is never reconstructed
from the decoded tools list. Older Go responses can omit this token outside the
Python profile. A failed configuration load cannot enable Save.

HTTP errors retain status plus strict `pending`, `unknown`, and `retry:false`
fields. Network failures on mutations are treated as unknown outcomes.
Pending/unconfirmed worker controls block further actions for that task/intent
within the mounted screen; unknown interception mutations similarly block
further changes in their screen. Only a confirmed pause/open response changes
the displayed worker state. There is no automatic retry or invented inspection
endpoint. Operator inspection/reconciliation is required. These UI latches are
not durable server authority: page reload/remount clears local state and is NOT
proof that an operation completed. Server retained ownership remains essential.

Pure authored helpers and static call-site checks can be run without npm:

```
node --test web/tests/mutation-contract.test.mjs
```

These tests do not execute the original npm scripts and do not replace a
TypeScript/Next build, browser tests, or integration against an active server.

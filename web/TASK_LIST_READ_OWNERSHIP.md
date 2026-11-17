# Task-list read ownership

Base: remote `1d6fe5532c639a7b72eb23ef3c62586cea1faabd`, local equivalent
`d3af75f69c711b368237941e090ae9c15e8ad78e`, tree
`7b0436da430ba4de3228e04b15ab22759eb9844b`.

## Concrete defect and correction

The current-tasks page previously published every completed poll. An older
pre-control read could finish after a newer refresh and replace paused task data
with stale running data. Category reads could similarly replace a newer category
list and reset its selected filter. Initial task read errors were swallowed and
rendered as successful empty results ("No tasks yet").

The page now has independent task and category read owners. An explicit refresh
retires the previous read before dispatch; only its current owner may publish
rows, errors, signature deduplication or loading completion. Confirmed category
moves retire any earlier task read before their local update and request a fresh
read. Effect cleanup aborts/retire reads, and setup replay starts fresh even when
an old transport ignores abort or never settles.

Periodic task polls coalesce with an active read rather than superseding a slow
read every ten seconds. A 15-second client read deadline retires that request and
makes a later poll/retry possible. This timer is subject to normal browser event
loop scheduling, not a hard wall-clock server cancellation bound. AbortSignal is
forwarded only by `api.tasks` and `api.taskCategories`, both GET methods. No
mutation is aborted, dispatched or automatically repeated by this reader. An
ignored abort can still leave the backend read working; its late result cannot
publish into the retired view.

An initial failure is explicitly unavailable with a retry, not an empty success.
Refresh failures retain the last confirmed rows and label them as stale until a
successful read. Background polls do not toggle foreground loading state, so an
unchanged successful payload retains the existing deduplication optimization. Category
errors have their own retry. Only a successful read changes the existing loaded
flags, task signature and category-filter reconciliation. Task/category DTOs,
Go/Python route methods, authentication and existing task action gates are not
changed. Callers omitting the new optional signal remain supported.

## Scope and evidence

Tests exercise the exact exported reader, ignored aborts, old success/error
ordering, slow/hanging reads, deadlines, explicit retirement, inactive and replay
lifetimes, synchronous failures, and independent task/category owners. They also
execute source-derived actual API methods and page callback declarations, and
check their polling/cleanup/empty-state wiring. These are not mounted React or
browser interaction tests. Full Node tests, real route type generation,
TypeScript and Next build checks are recorded against the frozen candidate in
its review evidence; baseline lint failures remain separately reported.

This does not redesign archive reads, task mutation receipts, duplicate-click
handling, mutation uncertainty, mutation completion callbacks, task identity
reuse, server consistency or the existing stale-row action policy. Backend
concurrency/version checks remain authoritative. It does not provide global
state persistence, a new backend capability or a working browser claim.

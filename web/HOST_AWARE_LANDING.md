# Host-aware authenticated landing

This frontend-only change directs a real Python-profile sign-in through `/`.
The landing page uses a dedicated read-only adapter for the existing authenticated, uncached
`GET /api/skills-workspace`. An exact supported Skills profile selects the fixed
`/system/skills` destination even when that host is read-only. Its configured
mode is not authorization, and this landing check never enables mutations.

Go and MOCK preserve the existing fixed `/function/tasks` destination without
discovery. Only HTTP 404 with JSON containing exactly one nonblank string `error` field preserves that
same full-Python fallback. Other failures, malformed replies and 403/503 do not
choose a destination: the page shows an explicit retry/sign-in recovery state.
The landing-specific GET has no auth or navigation side effects: 401 stays an explicit recovery state, and only the deliberate Sign in again action clears the current credentials. Shared API/auth behavior elsewhere is unchanged.
There is no query-driven redirect, cached host decision or task API call.

The login page's existing-token and successful-login branches retain their
credential synchronization and fresh navigation behavior, but use the fixed
profile-aware entry. The proxy still protects unauthenticated routes and keeps
its existing MOCK bypass; authenticated Python login/setup entry goes to `/`
rather than presuming the host implements the default page. Setup actions and
other route links are unchanged. Direct bookmarks remain direct bookmarks.

A single landing attempt owns its AbortController and ten-second deadline.
Unmount/retry cleanup retires that attempt at the React commit boundary; a late
completion cannot navigate or replace a newer error. The adapter captures the original bearer token rather than rereading storage, rejects redirects and requires JSON. Token identity is checked
before publishing a decision. A changed/removed credential yields explicit
recovery rather than using the old decision. Cleanup/setup replay starts a fresh
read even if an earlier transport ignores abort and hangs. A deadline expires
only that read, does not automatically retry and cannot cause a mutation.

A leftover cookie without a local credential is cleared before returning to
login, avoiding a proxy-to-root bounce. This is not a replacement authentication
scheme: token storage, credential validation and the shared HTTP transport remain
unchanged. Cross-tab auth handling and token-value reuse retain their existing
semantics; this is not durable session ownership or revocation detection.

Auth/profile/navigation tests use inert responses and exact source callbacks.
They test only fixed destination strings, not the destination page or its APIs.
Standard aggregate type/build checks are compilation gates, not review of any
other feature. No task-create or other held source is inspected or modified, no
task endpoint is called, and no provider, database, user data or publication is
involved. Source/model checks and emitted HTML are not mounted browser acceptance.

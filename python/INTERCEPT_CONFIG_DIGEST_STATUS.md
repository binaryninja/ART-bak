# Tool configuration opaque version token

The optional GET tool-config response adds the Python extension `version_digest`.
It uses the writer's `tools_digest` on the exact stored `intercept_enabled_tools`
string, or `None` when the setting is absent. It is not calculated from the
normalized tool list. The decoded tools and token come from one existing bounded
SELECT; no second settings snapshot or unrelated setting is read.

The service's existing grant/current/guard and serialization fences still cover
the entire response. The optional metadata wrapper continues pinning the same
reader.read method and retains its existing after-header disclosure checks.
Legacy reader.read() returns only decoded tools; the service explicitly opts in
with read(include_version=True). Existing mutation authority is not expanded.

Four dedicated tests plus 31 reader/wrapper regression tests pass. Coverage
includes distinct raw encodings of equal tool lists, absent/null/malformed values,
one-query snapshot behavior, compatibility and denied/oversized redaction.
Independent source review and subsequent PostgreSQL acceptance are recorded below;
they are separate from these inert tests. No default route, write, secret setting or execution was enabled.

## Isolated acceptance

Stage 139 passed actual disposable PostgreSQL acceptance for this narrowly
supported opt-in slice. See ISOLATED_QA.md for immutable manifest, exact cases,
prior corrections and remaining limitations. This does not enable defaults or
establish full Go parity.

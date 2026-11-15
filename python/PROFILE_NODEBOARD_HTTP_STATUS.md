# Profile and node-board HTTP checkpoint

624 authored offline tests pass. Source inventory finds 46 registrations against
261 Go method/path registrations (215 unregistered). Several registrations remain
explicit 503 placeholders, so this count is not working-feature coverage.

- Protected profile list/save/activation and runtime snapshot routes match source
  method/path contracts. Mutation requires an explicit coordinator that validates
  runtime prerequisites, persists and reapplies caches. No coordinator or provider
  connection is invented. Persisted-but-not-applied outcomes are distinguished
  from successful application; probes/deletion remain unavailable.
- Public credential placeholders cannot overwrite stored keys/URLs, adapter output
  identifiers are validated, and malformed adapter output becomes a redacted
  server error rather than a misleading success or disclosure.
- Node-board HTTP composes scoped pagination, edges, neighbor nodes and anchored
  asset labels through explicit authorized adapters. Asset resolver coverage must
  exactly match requested IDs; explicit None represents a known deleted asset,
  while incomplete coverage fails closed. No task selection or access is inferred.
- Both factories accept explicit dependencies; they do not invoke providers or
  exploration resolvers at startup. Production integration is still incomplete.
- Four build/container/release files and three sidequestion documents now have
  English prose. Build commands, release triggers, variables and URLs are unchanged.
  Historical validation claims are preserved as historical, not rerun evidence.
  An originally Chinese observed page title is retained as Unicode escapes.

Independent source-only and inert regression review covered these boundaries.
See REVIEW_FINDING_RUNNER_STAGE.md. No new real provider or target calls occurred.

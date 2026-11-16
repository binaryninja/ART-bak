# Optional authenticated intercept metadata routes

`bind_intercept_reads` creates a separate view of one original WSGI application,
with exactly two protected GET declarations: tool configuration and tool rules.
The original router/application is unchanged. Both exact services must share one
executor; each retains its existing source grants, with a separate explicit global
disclosure grant. No matcher, mutation, schema, listener or default activation.

Construction rejects static/dynamic GET/HEAD conflicts before SQL or policy work.
Unrelated paths delegate to the original application. Unsupported methods on the
two metadata paths never fall through to an existing mutation route. HEAD retains
the services' strict rejection and does not introduce new source registrations.

Identity pins cover the original JWT authorizer, auth resources, transport,
routers, services and readers. Successful disclosure is checked after service
serialization and after start_response. If that callback revokes authority, no
successful body is released; the already emitted status cannot be replaced.

Two independent regressions were fixed before integration: a final data grant
revoking service current, and the original JWT clock callback revoking global
read permission. Final JWT evaluation now precedes refreshed global and exact
service current/grant/current checks followed by pure identity checks. This is a
finite cooperative boundary, not a fixed-point guarantee for mutually mutating
policy callbacks. Four independent regressions and twelve owner tests pass.

Exact frozen checkpoint:2861 collected tests457 SQL syntax checks pass.
Isolated PostgreSQL stage99 passed six integration groups and26 focused tests;
see ISOLATED_QA.md for manifest and precise natural zero-timeout/read acceptance.
Public positive-duration admission is still refused.

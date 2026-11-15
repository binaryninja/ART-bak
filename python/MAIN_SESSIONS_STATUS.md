# Optional main-session metadata

`GET /api/exploration/main-sessions` reads stored session sequence/timestamps for
one explicit task/exploration scope. An exact `task` selector is mandatory; no
active-task or global fallback is inferred. Source order is descending sequence,
with the implicit original sequence zero represented by Go's zero timestamp,
not task creation time.

Task/archive and row bounds fail closed. Original store/request grants and policy
identities are checked through transaction exit and response serialization. The
service owns no session creation/reset, provider or execution facility. Factory
composition is optional, validates retained bindings before startup reads and
keeps JWT authentication ahead of task authorization. Default apps lack the route.

See ISOLATED_QA.md stages 74–75 for source comparison, actual PostgreSQL and
independent composition tests. No broader session/lifecycle parity is claimed.

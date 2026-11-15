# Optional intent-to-asset provenance reads

The source `GET /api/tasks/{id}/intent-assets` projection is available only through
explicit fixed-scope store/service wiring. It reads intent anchors and their existing
asset labels/provenance. Local intents retain source state behavior; inherited
intents come only from individually authorized direct task sources in terminal
states. No recursive context, target discovery, graph mutation or testing is added.

SQL bounds all projected text and row counts before output. A missing optional
source node is omitted, matching Go's DTO. Original source/request grants and
callback identities are checked through transaction exit and serialization. An
independent review reproduced both boundary revocations before the correction;
unchanged negative cases and positive non-reentrant guard cases now pass.

Application factories require explicit compatible bindings and supply no missing
read authority. Default applications do not acquire this route. Actual PostgreSQL
acceptance uses only synthetic assets and task contexts; see ISOLATED_QA.md.

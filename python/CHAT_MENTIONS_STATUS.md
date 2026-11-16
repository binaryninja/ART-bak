# Standalone chat mention catalog search (candidate)

`db/chat_mentions.py` and `server/chat_mentions.py` port only the source
`SearchChatMentionsPage` and `GET /api/chat/mentions` search contracts. They do not
parse visible mention tokens, expand record snapshots, send chat, admit a turn,
read evidence, or execute anything. No application route is registered.

The catalog is explicitly global, including finding summaries, company names and
asset labels that may be private. `CatalogScope('global-catalog', revision)` and
independent original catalog, asset-ID, current, guard and request policies are
mandatory. This is not a task-scope fallback; search does not invent source-absent
archive filtering or grant access to archived task details. An asset denial
rejects the whole page rather than filtering and leaking pagination information.
The asset grant sees all returned candidate asset IDs, including the lookahead.
Request and catalog/asset grants are rechecked after data projection, serialization
and guard exit; callbacks and resources are pinned. Cooperative finite fences do
not create an atomic snapshot across arbitrarily mutually mutating policies.

Source semantics retained:
- Kinds: finding/company/asset/endpoint/ip/app/root_domain/subdomain/service, or all.
- Query trimmed with Go-compatible Unicode space set, at most 200 Unicode code
  points. Percent, underscore and backslash are literal LIKE search characters.
- Findings, companies and assets each select at most 21 rows. Exact decimal-ID
  matches sort first, then numeric ID descending and kind ascending, not lexical
  ID order. The outer union selects 21; response contains 20 and optional cursor.
- Cursor is unpadded URL-safe base64 JSON with id/kind/exact/scope/query, bounded
  to 2048 characters and tied to the exact normalized query and selected kind.
- SQL truncates label/description to 160/240 characters before transfer, as Go
  does; SQL additionally caps kind bytes. The maximum 21 rows bounds transferred
  text. Returned dictionaries are newly copied and validated, not DB row aliases.
- Asset fallback display text is English `Asset #<id>`; no stored value changes.

Intentional stricter input/adapter behavior: unknown/duplicate query parameters,
malformed percent escapes, NUL/unpaired-surrogate search strings, noncanonical
cursor object shapes/duplicate fields, boolean IDs, invalid UTF-8 and malformed,
misordered, duplicate or oversize result rows fail closed. Cursor is a pagination
position, not a signed authorization token. All SQL values are native parameters.
No cursor allows changing the globally authorized catalog selection.

Nine authored tests pass using synthetic rows and inert SQL capture. They cover
numeric/exact-ID ordering, literal escaping, cursor scoping, transfer limits,
original grant/resource swaps, serialization and guard-exit revocation. Actual
PostgreSQL SQL execution and independent review remain pending; no live catalog,
provider, command, target or original Go program was executed.

## Actual PostgreSQL acceptance

Stage 127 passes three original-schema fixture groups and 15 focused tests.
See ISOLATED_QA.md for the immutable manifest, tested boundaries and limitations.
No default route or external action is enabled.

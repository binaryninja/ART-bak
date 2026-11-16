# Bounded skill catalog and opt-in HTTP integration

This slice connects actual skill-directory discovery, existing bounded content
receipts and the flat metadata parser to GET `/api/skills`, and makes that catalog
an independently optional family in `bind_skill_reads`. Catalog and content can
be enabled separately; neither inherits listing, ledger or the other's grants.
There is no default wiring, filesystem mutation, mkdir, execution or SDK loading.

The Go reference is `server/server_mgmt.go:fsListSkills` and `skillFileNode`.
The DTO retains directory names, sorted file paths, description/license/
compatibility/MCP metadata, calls/tasks and usage_agents. Parsed frontmatter name
does not replace directory identity. SKILL.md instruction bodies are read only
under explicit content authority and never included in the catalog DTO. Missing
SKILL.md retains the directory's filename-only node. Unsupported present metadata
fails the entire catalog rather than Go's best-effort metadata omission. This
parser supports only the port's documented flat scalar subset, not full YAML.

Usage decoration is deliberately unavailable: calls/tasks are zero and
usage_agents empty, with last_used omitted, matching the source's no-ledger
fallback. These are not verified usage totals. Existing individual usage and
missing-stat endpoints remain independently available. This slice does not claim
complete Go list parity or introduce an unsafe broad database reader.

Discovery is fd-relative with nofollow root/ancestor traversal, exact pinned
root identity, same-device directories and regular single-link files only.
Root regular files are not catalog nodes but count toward traversal limits;
symlinks, hardlinks, special files and invalid directory/path names refuse.
All entries count before filtering/sorting. Aggregate depth, path/output byte,
entry and decoded metadata caps bound the whole catalog rather than each skill.
A bounded content read can allocate up to the underlying per-file cap before the
aggregate decoded-metadata check rejects it; the aggregate cap is not a streaming
allocation ceiling. JSON response bytes are separately capped.

Snapshots seal DTO payload, full observed tree fingerprints and original content
snapshots. Final disclosure repeats source/content/current/request/global checks,
then JWT in HTTP, followed by code-owned tree rescan and content receipt checks.
Tree rescans catch nested insertions/removals and observed replacements, unlike
root-only checking. Pre/post-header validation withholds buffered bodies after
revocation or mutation; headers already sent cannot be replaced. Errors are
sanitized and never contain filesystem paths or instruction content. Method and
query/body refusal are explicit; HEAD has no body or source reads.

Binding is inert, requires exact original JWT app/readers, and refuses overlapping
routes of any method without route mutation. Module-load anchors protect original
reader/service/delegate methods at bind, and identities are rechecked during
requests. The five-family composer retains independent grants and selected-view
fences; catalog-only optional binding leaves content routing with the original.

Validation: 26 new reader/HTTP/integration tests and all 220 skill-focused tests
pass. These cover metadata and directory identity, absent metadata, nested names,
no access before grants, independent content authority, aggregate bounds,
unsupported metadata, links/FIFOs, sealed snapshots, late tree/root changes,
JWT denial and final expiry, method behavior, sanitized errors, all-method atomic
conflicts, pre-bind substitutions, post-header changes, five-family reachability,
permission isolation and catalog-without-content selection. Fixtures use real
disposable files and WSGI/JWT with inert database adapters, not a listener.
Independent review identified a foreign-bound-content-method receiver gap. The
fix pins both exact receiver and function for all anchored content methods;
regressions cover denied original authority, before-bind and post-header
substitution. A second review reproduction showed final catalog-current revocation
of content authority. Final child-current gates now follow catalog-current, then
request/global and child-authority-only sweeps occur before pure receipts. HTTP
repeats the child-authority sweep after final outer/request callbacks, then JWT
and pure receipts. Regressions cover both final catalog-current and final outer
revocation of child content authority. Arbitrary authority callbacks revoking
each other after their observations remain outside this finite protocol.
Independent re-review cleared both corrected defects. Additional probes verified
final child-current revocation of request/global grants and last child-authority
JWT expiry or tree insertion all withhold the body.

Corrected PG+FS160 passed eight isolated integration groups and all 220 focused
skill tests:
1. All eight foreign child-method receivers before bind/after headers, plus final
   catalog-current/outer child-authorization revocation.
2. Catalog child authority/current denial, sanitized malformed metadata, link/FIFO
   refusal, nested/inode/metadata/root changes and post-header expiry.
3. Nested/encoded content paths with content/inode/ancestor/root changes and expiry.
4. Real PostgreSQL usage and hot/cold missing statistics, filesystem listing,
   content and catalog through one five-family JWT app, with inert body exclusion,
   directory identity and original/unauthenticated/unrelated-route behavior.
5. Independent five-family outer/request/source/current grants.
6. Final outer JWT expiry in all five families.
7. Post-header grants/delegate replacement and atomic route conflicts.
8. All five delegate call/check/constructor and binder substitutions, foreign
   originals and replaced bases refused before reads.
Non-GET deep content and catalog conflicts also reject atomically.

Report: `/workspace/shared/artex-qa/skill-catalog-composite-160/REPORT.md`.
Manifest SHA256: `e68509706e4cb868b7fcef93844f1263c5a4acca5cf14ee02851c4cddabcce00`.
Catalog reader: `73864fcc54dbdf754008d02ba2f45699bf3c7c5f277f4c846d3149269e319e30`.
Catalog setup: `cea92594f6dece36fbfae1bdeb6b38f03ad321dae50f704b2a15c5829301fbaf`.
Composite: `0a372f698a72006387c3ce503f963ad82a6db0ae089f0fb0de6c8e112e7f4e04`.
All manifest entries and source hashes matched after tests. The frozen snapshot
used a copied index plus explicit current overlays, not an exact staged-tree
acceptance claim. Initial PG+FS159 remains preserved as the pre-correction
baseline and is not final acceptance.

PG+FS160 used fresh disposable PostgreSQL 17, temporary files and synthetic JWT
in an isolated UID1000 namespace with loopback only, no external routes or host
workspace/home/real credentials. The snapshot was read-only; shared source/index
were unchanged. It used component WSGI calls, not a network listener/browser or
default wiring, and did not run the full repository suite or SQL parsing.

The host must exclude uncooperative concurrent writers/mount/rename activity.
Metadata observations are not an OS transaction and cannot defeat arbitrary
mutually mutating callback cycles. Final ordering is a finite cooperative
observer protocol, not a universal race guarantee. Module anchors do not defend
against compromised imports or arbitrary private-state rewrites. No production,
browser/frontend or whole-tree parity claim is made. No staging/publication.

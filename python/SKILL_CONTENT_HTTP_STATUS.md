# Explicit authenticated skill-content HTTP overlay

`SkillContentReadService` and `bind_skill_content_reads` expose the accepted
bounded content reader for GET `/api/skills/{name}/files/<relative-file>`, including
nested filenames. This is opt-in against an exact original WSGI/Router/AuthService
JWT app, not default wiring. It is not yet added to the three-family skill-read
composer. No file mutation or instruction execution is enabled.

The existing Router has single-segment parameters only. This overlay uses an
explicit nested-path dispatcher, not an invented wildcard route registration.
It preserves original JWT checks before access, explicit method denial, WSGI
body/target/header bounds and unrelated original routing. Binding conservatively
refuses every overlapping original path pattern, for any method, rather than
silently shadowing a pre-existing writer. No route inventory increase is claimed.

Service parsing validates decoded name and canonical relative file, rejecting
traversal, encoded traversal, double separators, dot normalization, invalid UTF-8,
non-origin URLs, queries, fragments and bodies. HEAD returns 400 without body or
reads; other unsupported methods return 405. Request, source content grant,
source current scope and outer global permission remain independent.

The exact original selection and reader-issued content snapshot are retained
through serialization and the overlay's pre/post-header disclosure checks. Final
order includes source disclosure validation, request/outer grants, repeated
original JWT, then code-owned snapshot and complete pathname/receipt validation,
with pure identity checks between steps. Final callbacks modifying the file or
expiring JWT cannot release the buffered content in covered tests. Post-header
revocation raises before body return; already-emitted headers are not replaced.

Original service/reader/view implementations are anchored at module load and
validated at bind, including internal preparation and receipt/path validators.
This refuses observed pre-bind method replacement, not an already-compromised
Python import environment or arbitrary modification of private implementation
state. Exact root/scope/method/authority/request identities remain pinned.

All filesystem exceptions are mapped to generic responses: 404 missing, 409
observed change, 403 denial, 400 filesystem bound/path refusal, 503 unavailable
filesystem/capability and 500 unexpected failure. Raw paths, exception details
and receipt internals are never serialized. Content is inert text plus canonical
relative file. Raw bytes are bounded by the reader; JSON escaping can expand the
representation, so the raw file cap is not an equal-sized HTTP body cap.

Validation: 19 new service/setup tests and all 183 skill-focused tests pass using
real disposable POSIX trees and genuine WSGI/JWT, without a network listener.
Coverage includes nested content, invalid targets, no access before grants,
sanitation, serialization changes/revocation, original-app inertness, all-method
route conflicts, post-header grant/file changes, final-outer file writes/JWT
expiry, final-current grant/JWT revocation and pre-bind method replacement.
Independent read-only review found no behavior blocker. Additional probes covered
encoded/double-encoded traversal, canonical encoded nested paths, post-header
symlink/hardlink and request mutations, and sanitized private-path exceptions.

FS157 isolated acceptance passed four actual filesystem/JWT groups and all 183
skill-focused tests. Evidence: `/workspace/shared/artex-qa/skill-content-http-157/REPORT.md`;
manifest SHA256 `71f073c3e994c1dd22820fdb7edf7f4753e495ecf43c563048da07fd520fa814`.
The groups cover nested content and sanitized errors; independent grants and
late JWT expiry; file/inode/ancestor/root mutations; and pre-bind substitutions
and all-method route conflicts. Acceptance used disposable synthetic files and
real WSGI/AuthService JWT in an isolated credentialless namespace, not a network
listener or browser. No source or fixture correction occurred in that run.
The tested setup hash was `a8f67825d7db6428884ba58c3336e3a42e43379b5acb5e22b6053ed5bca7065e`;
a subsequent module-docstring-only correction describes the actual final JWT,
snapshot and pathname/receipt checks. AST comparison excluding that docstring
confirmed unchanged executable code.

The underlying reader's cooperative writer/mount/rename exclusions and finite
policy-observer constraints remain necessary. This is not an OS snapshot,
universal race guarantee, browser/frontend acceptance, binary-download API or
whole-application parity. No source reader changes, publication or staging are
performed by this slice.

# Explicit authenticated skill file-listing HTTP overlay

`SkillFilesReadService` and `bind_skill_files_reads` expose the reviewed filename-
only reader as an opt-in GET `/api/skills/{name}/files` overlay on an exact original
WSGIApplication/Router/AuthService JWT application. No default wiring or original
route mutation occurs. Original JWT, request grant, source name-specific grant,
source current scope, and outer global grant remain independently required.
The reader performs filesystem metadata/directory reads, but no filesystem
mutations. “No filesystem” would incorrectly describe this capability.

The handler validates the decoded skill name and rejects non-origin targets,
query parameters, fragments, invalid UTF-8 names, path separators and request
bodies. Unsupported methods are 405; HEAD does not widen access and returns 400
with no body or filesystem reads. Conflicting existing GET/HEAD route patterns
refuse binding atomically. Unrelated routes delegate to the original app.

Original app/router/auth resources, service/reader methods (including root
revalidation), source root/scope and callbacks are pinned. Successful data is
rechecked after serialization, before headers and again after start_response
before returning body bytes. JWT validity is rechecked after the final outer
callback because time may advance during it. Final order is request/outer grant
checks, repeated original JWT check, code-owned root identity check, then pure
binding checks. This prevents an observed final-callback root swap from escaping. Post-
header revocation raises before returning the body, not a fictitious replacement
status for headers already sent. Finite policy observers must cooperate with
lifecycle ownership; no mutually mutating callback fixed-point is claimed.

Filesystem failures are sanitized: missing skill 404, detected directory change
409, bound exceeded 413, permission denial 403, unavailable filesystem/capability
503 and generic unexpected failure 500. Responses never include raw OSError
messages, configured absolute paths or instruction contents. This endpoint lists
relative filenames only; it does not read/parse/execute SKILL.md or other files.

Validation: 19 new service/overlay tests pass, along with all 131 skill-related
tests. Tests use genuine JWT/WSGI and real disposable POSIX trees, not a listener.
They cover optional/inert construction, pre-access JWT/grants, name validation,
method bounds, route conflicts, error sanitization, serialization revocation/root
replacement, all post-header grant/current revocations, original-authority/method
replacement, final outer-callback root swap and final source-current revocation
of outer/request/source grants or actual JWT clock authority.
Final outer-callback advancement of the original clock past JWT expiry also
refuses the body before return.

Independent re-review cleared the final JWT fence and reproduced refusal of the
original final-outer-clock-expiry case. **FS153 isolated acceptance** subsequently
passed five actual filesystem/JWT groups:
1. Genuine JWT opt-in listing, original-app 404, missing-JWT 401, authorization
   before filesystem open, sorted names only, no contents/root-path disclosure,
   directory-only opens and no-follow traversal for all relative descendants.
2. File/directory symlinks, hardlinks, FIFO and root/ancestor symlink substitution
   refused; entry/depth/text bounds, invalid paths, missing skill and methods.
3. Root replacement before access, after 200 headers and during the final outer
   grant prevented filename disclosure.
4. Post-header grant/current/JWT expiry, final-current revocation of
   outer/request/source/JWT authority and guard-exit revocation withheld data.
5. Final outer-callback advancement of the stable JWT clock beyond expiry
   withheld the body through the repeated original JWT check.

All 131 skill-focused tests passed on the frozen snapshot. Manifest SHA256:
`d4e87f7eeea5675b06116bdfde428cce51b25b5814972deb51d52d1d71f74e81`.
Service SHA256:
`5ba1a75d6a1818b84b2731c2689496d356f26089aa1cdcbe6ffb89c33fc38166`.
Setup SHA256:
`e5ba362327b2ff7416b11faf0aa85dbb0a3c4f3a01ad31b9d83001fc58508c20`.
The current setup matched the tested source. FS152 is retained: its first group
failed an overly strict harness assertion requiring O_NOFOLLOW on the literal
`/` anchor; other groups passed. FS153 changed only that assertion, retaining
O_DIRECTORY for the anchor and O_NOFOLLOW for every relative descendant, and
added the exact final-grant expiry regression. Product source was unchanged
between those snapshots; both contained the corrected final JWT fence.

Acceptance used temporary files in a fresh credentialless user/filesystem/network
namespace, UID1000, no external route or host workspace/home access. This was
actual WSGI/JWT composition, not a network listener/browser test. No skill
contents were read or executed, no database/provider/target traffic occurred,
and no default route was activated.

Reader's cooperative-writer constraints still apply: no atomic filesystem
snapshot, hostile concurrent-writer immunity or universal race guarantee.
No route inventory
regeneration, publication, frontend acceptance, arbitrary file-content reading,
mutation, provider call or whole-application parity is claimed by this slice.

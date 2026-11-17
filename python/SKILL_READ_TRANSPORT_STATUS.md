# Skill browsing Unicode and HEAD parity

The existing opt-in skill file listing and content-read overlays now handle real
WSGI Unicode paths and HEAD requests, matching the useful Go GET/implicit-HEAD
contracts in `server/server.go` and `server/server_mgmt.go:fsListFiles/fsReadFile`.
This is a transport correction for two already implemented read families, not a
new source grant, default mount, editor launcher or file mutation capability.

## Correct boundary, no second URI decode

A WSGI gateway has already URI-decoded the URL into `PATH_INFO`, carrying the bytes
as Latin-1 characters. The old read overlays passed that carrier directly to the
standalone URL parsers. Actual HTTP percent-encoded Chinese skill/file names
therefore failed, even though directly injected Unicode Request fixtures worked.
The standalone parsers could also decode a literal percent again, accepting some
double-encoded aliases that the transport should have refused.

The new code-owned `decode_skill_request` helper decodes the carrier as UTF-8 once
and rejects any literal percent before the standalone parsers can URL-unquote.
It performs no filesystem operation, percent decoding, Unicode normalization,
query decoding or authority inference. The original request method, header object
and body are preserved in the derived Request. Source/request/global policies
receive the correctly decoded path and exact same selection throughout the read.
Invalid UTF-8 and double-encoded names/path aliases return 400. Canonical path and
skill-name rules remain the existing source checks. Normal URI-encoded nested
separators still face those canonical path checks after the gateway decodes them.

The helper is pinned by each overlay and checked before binding, handling and
final disclosure. A replaced decoder fails closed. The global WSGI adapter and
unrelated routes are unchanged. Standalone services retain their encoded-URL
Request contract and still decode such URLs once; only the WSGI overlays reject
already-decoded percent aliases. Other skill families are outside this slice.

## HEAD retains the same authority and representation checks

The two standalone parsers now accept GET or HEAD. They do not turn HEAD into GET
for policy purposes: all request grants still see HEAD. Existing bounded readers,
source/current/request/global grants, original JWT checks and snapshot/disclosure
receipts all run. The WSGI adapter supplies the GET representation's Content-Length
and content headers while emitting no body. Reads may still occur to compute and
validate that representation; HEAD is not a permission bypass or a cheaper empty
success path. Missing or denied resources remain missing/denied, with no body.

The existing post-header validation remains in place even for HEAD. Revocation,
JWT expiry, decoder replacement or changed content can fail transport after headers
have started. Already-sent headers cannot be retracted; no atomic network-delivery
claim is introduced. Existing reader bounds, no-follow protections, exclusive
cooperative guard requirements and finite callback-check limits are unchanged.

## Verification

Ten new regression tests use actual private temporary files, JWT/WSGI and a real
loopback HTTP server. They cover percent-encoded Unicode listing/content, HEAD
metadata/no body, original HEAD policy identity, independent grants before any
filesystem access, missing/invalid paths, double-encoding and malformed UTF-8,
post-header revocation/JWT expiry, decoder replacement and the unchanged standalone
encoded-URL contract. Three old tests expecting unsupported HEAD 400 now expect 200.
The new tests first reproduced five failures on the unchanged baseline; all 59
initial focused tests passed after the correction. The first full run caught one
older composition fixture that placed a literal percent-encoded slash directly
in PATH_INFO and expected another decode. That fixture now expects refusal and
checks the normal nested path; a real HTTP once-encoded nested separator remains
accepted in the new regression coverage. No transport permission was widened. The initially masked old composition HEAD
expectation was corrected as well. Independent review found that a pre-header
revocation fallback could emit its error body after the inner WSGI adapter had
suppressed HEAD output. Both outer fallbacks now preserve HEAD suppression; a
phase-specific regression checks 403, no body and the error representation length. No test substitutes decoded Unicode for
the actual WSGI carrier when exercising these overlays.

The candidate is integrated onto 00ac13bb7020d85ddb372bbd302e95303b0fa70f, including
the separately reviewed file-write slice without modifying its files. Full frozen
suite and independent review remain gates. No PostgreSQL server, user data,
production service, commit, publication, merge or deployment was used.

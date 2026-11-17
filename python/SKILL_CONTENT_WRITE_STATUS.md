# Explicit skill file save/create

This adds an opt-in `PUT /api/skills/{name}/files/{file...}` handler for the
existing Skills editor Save/New file API (`web/src/lib/api.ts:writeSkillFile`).
Go `server/server_mgmt.go:fsWriteFile` accepts JSON `{ "content": "text" }` and
returns HTTP 200 `{ "ok": true }`, creating or overwriting the named file. This
port preserves that successful request/response shape for the supported subset.
It does not execute, load, install or evaluate saved skill content.

## Explicit composition and authority

`SkillContentWriteService` requires the exact existing `SkillDirectoryScope`,
source/selection permission, an independent mutation callback, a current-revision
check and an exclusive writer guard. Each mutation grant receives the original
request and immutable scope, skill/path, observed parent identity, prior file
metadata/content-hash version, new UTF-8 byte count and SHA-256 digest. A read
permission or JWT alone is not write authority. Content remains available in the
request for the host's exact approval decision, never logged by this adapter.

`bind_skill_content_writes` accepts only an original WSGI/Router/AuthService base
and a separate global grant. It retains the original JWT and checks grants before
filesystem access and at every primitive authorization checkpoint. Construction
is inert; the base routes are not modified. Conflicting file-family routes and
swapped captured services, policies, auth bindings or methods are refused. This
factory is not enabled in the default CLI/runtime. It cannot be stacked on the
existing exact-base read overlays. A complete read/write editor host must compose
those independently authorized families explicitly; this is not such a launcher.

The host must exclude all other tree writers, root/parent renames and mount
changes, and serialize policy/revision changes through the same guard. Read-only
acknowledgement callbacks also run after guard release. These finite checks are
not an OS compare-and-swap against hostile writers or a lock held over network
transfer. They do not attempt a fixed point over mutually revoking callbacks.

## Bounded filesystem subset

The original descriptor-relative `ScopedFilesystem` atomic write primitive is
reused without relaxing its no-follow, regular-single-link, version, metadata,
replace or fsync checks. The service independently reopens the named root/parent
and compares the prior file version after every primitive grant, including the
last callback immediately before replace. A target changed in that callback is
not overwritten. These versions protect observed changes during this request,
not a stale browser edit prepared before the request: no ETag/If-Match protocol
or persistent editor revision is introduced.

Content is UTF-8 text, at most 262,144 bytes, with NUL refused. Existing binary,
non-UTF8, oversized, symlink, hardlinked, directory and special-file targets are
refused. All parents and the skill must already exist; unlike Go, there is no
recursive parent creation. New files request 0600. Existing ordinary rwx bits
are preserved and special bits stripped, unlike Go's direct truncation behavior.
Unsupported extended attributes/ACLs or ownership preservation fail closed.
Observed cross-device parent/target traversal is refused. Existing ordinary file
contents are replaced atomically, never partially truncated in place.

Paths use the existing skill-name/relative-path rules, additionally requiring
canonical spelling and at most 32 components. The WSGI Latin-1 carrier is decoded
as UTF-8 exactly once, without another URL-unquote. Normal URI decoding may produce
nested separators in the file portion; canonical validation still rejects empty,
dot or traversal components. Literal percent/double-encoded aliases are refused.
The existing application's request-body bound is retained, so it may impose a
lower limit on heavily JSON-escaped text than the service's encoded-body bound.

## Outcomes and verification

Once the primitive begins, every failure or interruption is conservatively an
unknown outcome with `retry: false`, even if only a temporary file may have been
written. The adapter never retries, promises rollback or claims a failed rename
removed the old file. Before that boundary, missing parents are 404, observed
conflicts 409, denied grants 403 and invalid input 400. Content above the byte
bound returns 413 (the WSGI body bound can refuse earlier).

A successful body uses an acknowledgement receipt that revalidates all grants,
named path and exact written metadata/content version without writing again.
Checks after guard cleanup catch observable revocation there. Changes after
headers withhold the body and fail transport; already-sent 200 headers cannot be
retracted. Operators must inspect/reconcile uncertain outcomes before retrying.

Twenty-six new tests use actual private temporary trees, actual JWT/WSGI and a
real loopback HTTP server. They cover atomic create/overwrite, content-digest
proposals, permissions, modes, symlinks/hardlinks/FIFO, binary/size/path limits,
original route integrity, payload/proposal tampering, early and final-grant target
or parent movement, root replacement, fsync failure, interruption after replace,
post-header changes, final guard-exit revocation, parallel writes and real Unicode
HTTP. Existing workspace-write and filesystem primitive tests also run unchanged.
An initial combined test command used a nonexistent module name; rerunning with
the actual test module names fixes that test-invocation error, not product code.

Full frozen-tree testing and independent review remain gates. No PostgreSQL server
is required. No user files, production service, commit, publication, merge or
deployment was touched; no full Go or production-readiness claim is made.

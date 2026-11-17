# Bounded create-only Skills ZIP import component

This opt-in component implements `POST /api/skills/upload` with one multipart
field named `file`, a `.zip` filename, no query parameters and no overwrite.
Success is exactly HTTP 201 `{ "name": "…", "files": N }`, where `N` counts
regular files, not explicit directory entries. The filename is never a target
name or fallback. Other methods remain unsupported. Original JWT authentication
and separately supplied source, mutation, import, current and request-global
policies are required; reads or ordinary writes do not imply import authority.

## Supported archive subset

The entire multipart request stays within the existing 1,572,896-byte host cap.
ZIP bytes and actual aggregate expanded bytes each have a 1 MiB cap; each file
has a 256 KiB cap. At most 128 archive members, and at most 128 resulting files
plus explicit/implied directories, are accepted. Paths use the existing 512-byte
UTF-8 relative-path rules, at most 32 components and 255 bytes per component.
Canonical NFC names must be collision-free including casefold aliases and
file/parent conflicts. Windows device-name and trailing-space/dot ambiguities
are refused even on the supported POSIX host.

Only standard stored or raw-Deflate entries are accepted. Names must be ASCII or
explicitly UTF-8 flagged. There is no encryption, ZIP64, multiple disks, data
descriptor, extra field, comment, preamble, padding or trailer support. Central
and local headers/names must agree, with contiguous local records in central
order. Unsupported flags, link/special/setid metadata and ambiguous directory
attributes fail closed. All actual expansion and CRCs are checked before any
filesystem effect. Bounded raw decompression rejects incomplete streams, unused
trailing streams and falsely understated expanded sizes; the implementation does
not rely on `ZipExtFile` truncating to a declared length. It never calls an archive
extraction API.

Exactly one canonical `SKILL.md` must be at the archive root or directly inside a
single wrapper directory. Anything outside that selected root is rejected, not
silently ignored. The manifest must be UTF-8, NUL-free and accepted by the port's
existing flat-scalar parser with an explicit valid name and nonblank description.
A metadata `mcps` field is outside this subset. Legacy codecs, filename/directory
fallback names and general YAML are not supported. Other files may be binary;
their bytes are inert and never executed. Existing text-only readers/editors may
refuse binary content. Import does not grant runtime, tool, MCP, provider or
visibility authority and does not use a database.

## Ownership and uncertainty

Construction is inert. The original exact root, service, bound method owners and
functions, request, selection and immutable plan/digests are pinned. Policies
approve the exact root identity, complete planned content, archive digest and
file count. Whole-operation exclusive host ownership includes external writers,
renames and mount changes; a process mutex alone cannot provide that contract.
Every callback checkpoint is followed by fresh no-follow named-root/parent and
partial-tree checks before the next effect. Files must remain single-link,
owner-only regular files without inherited ACLs/xattrs. New directories are 0700
and files are 0600. Existing destinations of any kind conflict without overwrite.

The validated plan is staged in bounded memory, not in an on-disk temp archive.
Installation creates a new directory, then creates directories/files exclusively,
checks complete bytes and identities, and fsyncs files/directories/root. It is
**not an atomic installation**. Once the first mkdir is attempted, any failure,
interruption, cleanup, durability or authority uncertainty returns a redacted 500
with `outcome: "unknown", retry: false`. Partial state remains untouched: there
is no rollback, recursive cleanup, retry, repair or replacement. A complete
receipt rechecks the installed tree and authorities after guard cleanup and at
body handoff. A post-header failure withholds the receipt, not the already-made
effect. The containing host must quarantine failed or abandoned mutations and
retain its original ownership state. Never-consumed response cleanup does not
repeat the import. Concurrent authorized duplicates under the same exclusive
guard produce one success and later conflicts.

These are finite cooperative checks, not adversarial OS compare-and-swap, a
fixed point for arbitrarily mutually revoking callbacks, stale-browser version
protection, socket-send atomicity, a crash-recovery journal or proof of rollback.
The standalone binder does not itself supply host ownership, quarantine, a new
mount or frontend capability discovery. Composer, host, UI and real authenticated
transport/cleanup integration remain separate gates before vertical publication.

## Source compatibility and evidence

The source Go `server/server_mgmt.go:1292–1441` upload handler uses multipart
`file`, discovers `SKILL.md`, derives a name, extracts files and returns 201
`{name,files}`. The Python subset deliberately refuses Go's overwrite/recursive
cleanup, fallback names, codec breadth, ignored out-of-root members and larger
limits. Go's entry count can include directory records; this subset's receipt
counts installed regular files. No compiled-Go execution or complete ZIP parity
is claimed.

Synthetic tests cover stored/deflated root/wrapper/Unicode/binary content; malformed
header relationships, actual-size and CRC lies, raw-stream tails and expansion
bounds; path/alias/member collisions; independent grants and original JWT;
root/parent/target changes, hardlinks, partial writes and filesystem failures;
bound-owner drift; guard/header/body revocation; immutable plan/request drift;
create-only conflict and concurrent duplicates. Tests use only private temporary
data. Full Python/SQL-syntax/Node checks and independent component/combined
reviews are reported in external immutable-candidate evidence, not implied by
this status document. No live PostgreSQL, provider, rendered-browser, deployment
or production-readiness result is claimed here.

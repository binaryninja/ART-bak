# Create-only Skills archive import UI

This frontend slice targets only the explicit Python Skills workspace import
adapter. It does not implement archive extraction, grant filesystem authority,
execute imported content, enable imports in the default server or change the
existing Go upload/overwrite API and callbacks. MOCK acknowledges a named fixture;
it does not inspect or install an archive.

## Explicit capability and multipart contract

The exact host profile accepts only `profile`, `writes_enabled` and optional
boolean `deletes_enabled` / `imports_enabled` keys (two through four total).
Import/deletion true requires writes true. Legacy two/three-field replies never
infer import permission from writing or deleting. Loading, incompatible,
unsupported, unauthorized and stale profile reads fail closed for imports. A
recheck cannot retire an admitted mutation or clear any unknown-outcome latch.
The discovered mode is a configuration hint; the host still authorizes requests.

A separate `api.importSkill(File)` sends a single multipart `file` field to exact
`POST /api/skills/upload`, with original bearer authentication and no query,
overwrite option or manually imposed Content-Type. Fetch supplies the multipart
boundary. Existing JSON transport is not reused incorrectly for FormData. A 401
follows the existing sign-in redirect/credential-clearing behavior.

The browser checks only native File identity and metadata: nonempty file up to
1 MiB, nonempty .zip basename (case-insensitive), at most 255 UTF-8 bytes and no
slash, backslash, double quote, C0 or DEL. This is not ZIP content validation.
The response must be HTTP 201 with exact `{name,files}`: valid NFC-normalized canonical skill name
and safe integer regular-file count 1–128. The host derives the name from the
validated manifest, not the archive filename; filename equality is not claimed.
Known 400/401/403/409/413 refusals retain the File and show an error. Any lost,
malformed, unexpected or otherwise ambiguous post-dispatch outcome suspends
subsequent tracked mutations. Unknowns are labelled by the submitted archive
filename, never by an invented installed skill name. No automatic retry or
Python overwrite is offered, including when an error contains “already exists”.

## Bounded host subset, not a general ZIP installer

The coordinated host subset accepts at most 1 MiB compressed and 1 MiB expanded,
at most 256 KiB per regular file and 128 total entries (directories count toward
that budget; the success count is regular files only). Only the bounded
Store/Deflate subset is supported, including standard Info-ZIP archives within
these restrictions. Names must be ASCII or explicitly UTF-8 flagged; Unicode
filename override fields are not supported. Only timestamp (`0x5455`) and
version-1 UID/GID (`0x7875`) extra fields are accepted, at most one of each and
32 total extra bytes per header. Tags must be paired between local and central
headers; flags and common values must match. UID/GID widths are 1–4 bytes.
These inert fields are parsed for consistency, not restored: imported timestamps,
ownership and modes are not taken from the archive. This is not support for all
Info-ZIP archives or all Unicode filename encodings.

Other extra fields, ZIP64, encryption, additional codecs, data descriptors/streamed
ZIPs, comments and self-extracting preamble/trailer data remain unsupported.
Exactly one SKILL.md requires an explicit valid name
and nonblank description; MCP frontmatter is not supported. No path outside the
chosen root or overwriting existing skill is permitted. Other bounded files may
be inert binary data and need not be readable by the text editor.

The host validates archive contents before installation effects, but this is not
a claim of all-or-nothing filesystem installation or automatic rollback. Later
filesystem/durability failures may preserve partial effects under quarantine.
Native authority, prevalidation, partial-effect handling and reconciliation are
separately reviewed backend responsibilities, not browser guarantees.

## Request, File and draft ownership

A synchronous import owner waits for all prior tracked root/file/folder/delete
mutations, then blocks every new tracked mutation across skills. Import
uncertainty has its own persistent-within-mount latch and cannot be cleared by
selecting another file, closing/reopening the Sheet, reading the catalog or
refreshing capability discovery. Existing root/file/deletion uncertainty also
prevents import. Reads and local draft edits remain usable subject to host access.
Pre-existing Go-only uploads/metadata/visibility are not model-tracked and are
not canceled or serialized by this new path; their existing semantics remain.

The Python Sheet retains the selected native File on local refusal, server
refusal or unknown completion. Known errors remain visible with their archive
name on the main page after the Sheet closes; only matching File errors appear
inside a replacement Sheet. Every picker change captures a new File reference
immediately; picker and submit callbacks own their exact open-dialog session.
Only an acknowledged unchanged File in its original still-current session is
cleared/closed. Later File choices, reopened dialogs, navigation and every
existing editor draft are preserved. Commit-phase retirement prevents completion
callbacks from changing an unmounted page. Catalog refresh never hydrates or
clears drafts. Closing a Sheet does not cancel an already dispatched import.

## Validation boundaries

Inert source/model tests exercise multipart construction, status/shape checks,
legacy and explicit modes, all mutation overlap orders, known/unknown outcomes,
File/session ownership and unchanged Go upload code. Real type/build checks are
separate from mounted React/browser behavior. Exact backend/host integration and
authenticated synthetic API/model-to-Next-to-CLI acceptance are required before
publication. No user archive or production data is used by these frontend tests.
No durable/cross-remount deduplication, browser-stale CAS, arbitrary-writer fence,
archive safety proof in the client, automatic retry/restore or deployment is claimed.

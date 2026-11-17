# Explicit top-level skill creation

`SkillCreateService` and `bind_skill_creation` add only authenticated
`POST /api/skills` to an original exact WSGI/Router/AuthService base. The method
returns the original Go receipt `201 {"name": "requested-name"}`. The existing
Skills workspace composer accepts an optional `create_service` and independent
`authorize_create` grant together. Without both, its old GET-only root contract
is unchanged. No default application mount or filesystem root inference occurs.

## Supported request

Required strings: `name`, `description`. Optional strings: `license`,
`compatibility`, `instructions`, defaulting to empty. Nulls, duplicate keys,
unknown fields, query selectors, alternate JSON encodings and malformed input
are rejected. MCP/visibility/archive fields are deliberately unsupported,
including an empty `mcps` array. No metadata update, upload, deletion, provider,
database or execution capability is added.

- Name: original `valid_skill_name` rules, 1–64 characters, plus at most 255
  UTF-8 bytes for one filesystem component. Names are not silently normalized.
- Description is nonblank. Description/license/compatibility are single-line
  strings without CR, LF or NUL, each at most 16,384 UTF-8 bytes.
- Instructions contain no NUL and are at most 262,144 UTF-8 bytes.
- Final generated `SKILL.md` is at most 262,144 UTF-8 bytes. The JSON wire bound
  matches the existing file writer (6 × 262,144 + 32 bytes); a host may impose a
  smaller transport bound.

The original inert `render_skill` JSON-quotes metadata as YAML scalars. The
result is parsed back before mutation to verify exact metadata and supplied
nonblank instruction text, with no MCP entry. Blank instructions use the existing
inert scaffold. This is not execution or approval of the text in the file.

## Authority and effects

The exact `SkillDirectoryScope`, current/source grant, separate mutation grant,
original JWT/global grant and whole-operation exclusive writer guard are all
required. The composer requires the same exact root and guard as its existing
read/write families; a read grant does not enable top-level creation. The host
must exclusively control external writers, root renames and mount changes.
Original transport, router, auth and service methods are checked by both bound
owner and function. Each request gate also pins its exact request, root scope
and one selection identity; a rebound same-class foreign service is refused.
Callbacks are trusted cooperative host primitives; finite permission/identity
checks are not a fixed-point guarantee for arbitrary mutually revoking callbacks
or atomic authorization across socket delivery.

The root must exist, be owned by the current effective user and have mode 0700.
Root traversal and all child operations use no-follow directory descriptors.
Existing top-level entries, including symlinks or files, conflict without being
overwritten. A new directory is created with mode 0700, then its `SKILL.md` is
created exclusively with mode 0600. Unsupported ACLs/xattrs, links and special
files are rejected. The file, directory and root are fsynced. Named-directory
and file identities are rechecked after cooperative callbacks; successful
receipts verify the original complete file again without repeating any write.

This is **not an all-or-nothing directory transaction**. Once `mkdir` is
attempted, any unconfirmed effect, partial write, authority loss, sync/close
failure or later receipt failure is uncertain. The service preserves partial
artifacts and never retries, overwrites, removes recursively or claims rollback.
It returns redacted `500 {"outcome":"unknown","retry":false,...}` when it can
still return a response. A failure after headers instead withholds the body.
The executable host must classify exact `POST /api/skills` as a mutation, seal
new work and retain ownership after unknown completion. Manual reconciliation
or process termination may be necessary; no recovery operation is introduced.

Before an attempt, invalid requests return 400, denied authority 403, observed
existing/conflicting entries 409, configured size excess 413 and unavailable
composition 503. Preparation failures can return redacted 500; hosts may
conservatively quarantine those as well. Unauthorized requests remain 401.

## Scope of verification

`test_skill_create.py` uses original WSGI/JWT, temporary private roots and
synthetic inert text. It covers create/catalog/read/edit/reload composition,
quoted metadata round trips, independent authority, duplicate/no-follow
conflicts, observed root/parent/link replacement, partial writes and failures,
post-header and guard-exit revocation, bounds and unchanged opt-out behavior.
No real task runtime, PostgreSQL, provider or external target is involved.
Executable-host and frontend integration, actual loopback acceptance and
independent whole-tree review are separate gates before publication.

Go source contract: `server/server_mgmt.go:fsCreateSkill` and the root POST route
in `server/server.go`. Deliberate differences include quoted metadata, stricter
bounded input, private modes, no MCP side effect, explicit separate authority,
and conservative retained partial state instead of Go's recursive cleanup.

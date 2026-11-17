# Opt-in Skills-only development host

This separate launcher is **not the ARTEX task runtime replacement**. It starts
no PostgreSQL, task engine, provider, tool execution, subprocess tool, archive,
ledger or background task pump. Skill text is data, never instructions to execute.
Nothing is mounted in the default application and no existing CLI is changed.

## Explicit operator setup

Use a POSIX system and the official optional `bcrypt>=5,<6` dependency. Choose an
existing absolute, canonical, current-user-owned **mode0700** skills root. Existing
skills require their own directory and `SKILL.md`; an empty root is also valid.
The explicit write-enabled profile can create a new top-level skill, as described
below. Keep only intended skill data there. Do not use a shared
or actively edited tree: the operator must exclude other writers, root renames,
mount changes and lock-file replacement for the entire host lifetime.

Provide an existing absolute **mode0600**, current-user-owned, regular single-link
password file **outside the served root**, without symlink ancestors. Its UTF-8
password must satisfy the existing auth limits (at least eight characters, at
most 72 bytes), with at most one trailing LF. Passwords in argv/environment,
symlinks, FIFOs, hard links and unbounded files are not supported. This host never
creates or saves the password file, password hash or JWT signing key. Do not put
real credentials in command history or under the Skills root.

```sh
PYTHONPATH=python python -m artex.skills_workspace \
  --root /absolute/private/skills \
  --password-file /absolute/private/auth/password \
  --exclusive-owner \
  --port 8787
```

Reads are enabled after login as username `ARTEX`. Add `--enable-writes` only if
the authenticated local operator may create top-level skills, create/overwrite
text files and create subdirectories within that explicit skills root. This is
single-admin workspace authority, not per-skill or per-user authorization. A successful file
PUT is an upsert, not browser-version compare-and-swap. Existing subdirectories
through `/dirs` are idempotent; parent directories must exist. Existing file and
directory service limits and filesystem restrictions remain unchanged. The host caps content reads
at 256 KiB and uses the existing bounded request/body/connection transport.

Permanent removal requires the **separate** `--enable-deletes` flag as well as
`--enable-writes`. Write access alone does not grant deletion. This opt-in permits
only a selected regular single-link file or empty subdirectory through
`DELETE /api/skills/{name}/files/{path}`. It cannot remove a skill root, the
mandatory top-level `SKILL.md` (including case-folded aliases), nonempty
directories, symlinks, hard links or special files. There is no recursive delete,
trash, automatic backup, restore or rollback. Confirm the exact skill/path and
permanent-removal consequence for each operator action; an automation must not
treat the host flag as the user's action-time confirmation.

A confirmed removal returns exactly `200 {"deleted":"the-relative-path"}`.
Missing paths return 404 rather than an invented idempotent success; nonempty
folders conflict with 409 without recursively removing any contents. A current
in-request target fingerprint and fresh parent/root checks detect observed drift,
but this is not a stale-browser compare-and-delete or a fence against unrelated
writers. The exclusive-owner requirement still applies. If removal, durability,
authorization or acknowledgement becomes uncertain, keep the observed filesystem
state and reconcile manually; never repeat the deletion automatically. Retained
editor drafts are not a verified backup or restoration mechanism.

`POST /api/skills` is enabled only through this host's explicit write grant. It
requires `name` and a nonblank `description`; optional `license`, `compatibility`
and `instructions` are inert text. It does not accept MCP or visibility settings
and never executes generated skill content. A confirmed create returns exactly
`201 {"name":"the-requested-name"}`. Existing top-level entries conflict with 409;
they are not overwritten, merged or treated as an idempotent success. New skill
directory/file creation is create-only, but the two filesystem changes are not a
single atomic transaction. A possible partial effect is retained and returned as
unknown, never rolled back or retried automatically.

### Create-only ZIP import

Add the separate `--enable-imports` flag **as well as** `--enable-writes` to
authorize `POST /api/skills/upload`. Neither ordinary write access nor deletion
mode implies import authority. The request must contain one multipart `file`
field with a plain `.zip` filename; the filename is metadata, not the destination
name. No query parameters or overwrite option are accepted. An existing target
conflicts with 409 and is never replaced or merged. A confirmed new installation
returns exactly `201 {"name":"manifest-name","files":2}`; `files` counts regular
files, not directories.

This is a deliberately narrow archive subset, not general ZIP compatibility:

- At most 1 MiB compressed archive and 1 MiB total actual inflated file data;
  each file is at most 256 KiB, with at most 128 archive entries including folders.
  The existing `MAX_JSON_BYTES` request/transport ceiling is not increased.
- Stored or raw Deflate compression only; no encryption, ZIP64, multiple disks,
  streaming data descriptors, ZIP extra/comment fields or self-extracting
  preamble/trailer. Names must be ASCII or explicitly marked UTF-8.
- Canonical relative NFC paths only, at most 32 segments and 255 UTF-8 bytes per
  component, within the existing total path limit. Duplicate/case aliases,
  file/directory collisions, traversal, links, special entries and incompatible
  metadata fail closed rather than being normalized or silently skipped.
- Exactly one `SKILL.md` at the archive root or beneath one common top-level
  folder. No unrelated entries outside that folder. Its UTF-8 metadata must
  provide a valid explicit skill name and nonblank description; no filename
  fallback or MCP frontmatter is supported.

All member paths, header relationships, actual expanded lengths and CRCs are
checked in bounded memory before filesystem changes. Other files may contain
inert binary data; the existing text editor can refuse binary reads. Imported
files are created with mode0600 and folders with mode0700, never with executable
permissions copied from the archive. No skill instructions or files are executed.

Installation is a sequence of guarded, create-only directory/file operations,
not an atomic publication. If an effect, durability check, authority observation
or acknowledgement becomes uncertain, partial files remain and the whole host
is quarantined with its lease retained. There is no recursive cleanup, automatic
retry, overwrite, rollback or promised recovery. Inspect the retained tree before
any later manual action. Malformed input rejected before effects is not partial
installation, and a client losing a successful receipt is not proof of failure.

Authentication is initialized with real bcrypt before any socket accepts requests;
unauthenticated initialization therefore cannot take over the host. The hash and
random JWT key are memory-only. Changing a password over the existing auth API is
memory-only and does not update the operator's input file. Restart invalidates
all old tokens and reloads the explicit password file. The launcher never prints
passwords, hashes, signing keys, tokens, request paths or query parameters.

### Discovering configured read-only mode

Only this explicit Skills host mounts authenticated `GET /api/skills-workspace`.
The exact successful JSON is `{"profile":"skills-workspace","writes_enabled":false}`
by default, or the same shape with `true` when the operator supplied
`--enable-writes`. The default response remains exactly those two fields. Only
an explicit delete-enabled host adds `"deletes_enabled":true`; absence never
implies deletion permission. An explicit import-enabled host independently adds
`"imports_enabled":true`; it also requires writes, and absence never implies
import permission. Both optional fields may be present. Older strict clients may
fail closed on an opt-in extended response. `HEAD` supplies the representation length without a body.
Successful responses carry `Cache-Control: no-store`; queries and request bodies
are rejected with 400, and other authenticated methods with 405. An ordinary
application without this host does not acquire the route.

This bounded response contains no root paths, credentials, skill contents or
permission-changing controls. It describes configuration at that request, not
authorization, durable capability, revocation notification or a promise that a
later write will succeed. Every mutation still uses the original write grant and
root/lease/request checks. Missing, malformed or unavailable profile data must
not be interpreted as write permission by a client. A profile refresh must never
clear an unconfirmed mutation. A sealed or quarantined host continues to reject
new profile requests with 503; discovery cannot bypass that state. No cache or
already-delivered response can prove that authority remains current.

For the separately configured development frontend/proxy, an operator may add
`--frontend-origin http://127.0.0.1:3000` (or another accepted canonical HTTP
loopback origin). This trusts that entire origin, not a particular proxy identity.
The existing exact backend Host boundary and duplicate/framing checks remain.
Forwarded headers confer no trust. Missing Origin is still accepted; this is not
full CSRF protection, TLS, CORS enablement, rate limiting or production hosting.
No frontend dependency installation or real browser acceptance is implied.

## Ownership and shutdown

One `RuntimeLease` uses `.artex-skills.lock` in the owned root. Never unlink this
file. Its descriptor and guarded owner permit are retained until verified clean
shutdown. This root-level regular file is not a skill and its contents are not
reachable through a valid skill route. The shared RLock covers all eight service
guards; original request tokens are required for their permissions. The lock and
lease coordinate this host and cooperating launchers only, not arbitrary writers
or a distributed/restart authority.

The host uses the existing `QuietRuntimeHandler` and `_BoundedServer`, fixed
`127.0.0.1`, bounded sockets/connections/body and no access/error logging. Shutdown
permanently seals new request admissions, stops the listener, waits for actual
handler and response-iterator tails, then releases the exact guarded lease. Calls
admitted before sealing may complete; sealing is not cancellation or rollback.
An iterable closed before its first iteration has not dispatched any work. A
mutation iterable closed after dispatch is conservatively uncertain.

A mutation 500, exceptional response/disclosure/close, or uncertain thread launch
cannot be called clean. Unknown mutation seals all new work and retains the lease.
The exact top-level `POST /api/skills`, archive `POST /api/skills/upload` and
`DELETE` beneath `/api/skills/` are included in this accounting, not just
file/directory writes. Safe pre-effect refusals such as
401, 403 or an existing-entry 409 do not themselves quarantine the workspace.
After an unresolved shutdown the CLI stays alive, holding the exact host and
signal scope; it does not automatically retry, reconcile, release or restart.
SIGINT/SIGTERM handlers only latch. Failed signal installation rollback or
restoration retains that exact scope too, separately from host drain. Any
BaseException or non-confirmed result from final host stop enters the same retained
foreground state; it is not allowed to escape main and silently end ownership.
Retention diagnostics are best-effort: a closed stderr pipe cannot skip the hold.
If the final release syscall itself fails or is interrupted after taking effect,
retaining a Python object does not prove the OS lock remains held. Release is
attempted only after observed work/handler drain; no rollback of close is claimed. Deliberate
forced termination still releases OS locks; it is not proof of settled writes.
An operator must inspect/reconcile uncertain files before any later manual launch.
No automatic recovery authorization or mutation journal is provided.

## Candidate evidence and limits

The host tests use temporary synthetic files/credentials, real bcrypt/JWT and
real loopback HTTP. They cover initialization refusal, read-only mode, Unicode
read/write/HEAD, upsert/idempotent mkdir, symlink refusal, hostile Host/Origin and
duplicate Host, actual post-rename fsync uncertainty and held lease, root revocation,
request-tail/in-flight drain, interrupted launch, real signal restoration faults,
and a child CLI that authenticates/writes then drains on SIGTERM without secret
output. Separate deletion tests cover write-only refusal, opt-in file/empty-folder
removal and exact receipts, preserved manifests/nonempty directories/links,
actual post-unlink fsync failure and client disconnect with retained ownership,
plus explicit delete-enabled child CLI shutdown and lease reacquisition. These
fixtures contain only temporary synthetic data. Fault probes distinguish
in-process injections from actual HTTP behavior. Separate import tests cover
independent import mode, bounded multipart/authentication, Unicode import and
catalog/read/edit/conflict, partial-file/fsync/disconnect/disclosure uncertainty,
plus an explicit import-enabled child CLI and natural lease-releasing shutdown.
Lowercase HTTP PUT currently refuses 405 in the original write overlay; normalized
host quarantine accounting is additionally tested with a synthetic lowercase 500.

No PostgreSQL, live provider, user files, real target, browser, production service
or external network was used. No mutation is retried. Unknown-case test teardown
is not evidence that production can cleanly release unknown ownership. Independent
review and exact combined frontend acceptance remain separate gates; passing this
launcher does not establish the broader Python runtime's functional parity.

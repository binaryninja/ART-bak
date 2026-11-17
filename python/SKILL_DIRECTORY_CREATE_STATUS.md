# Explicit skill subdirectory creation

This adds the protected `POST /api/skills/{name}/dirs` contract used by the existing
Skills page's New folder action (`web/src/app/(main)/system/skills/page.tsx`).
The source is `server/server_mgmt.go:fsCreateDir`: JSON `{ "path": "references" }`
returns HTTP 201 with `{ "dir": "references" }`. Existing directories are
idempotent. Only this mkdir route is added; no files are written, removed, loaded,
installed or executed, and no PostgreSQL dependency is introduced.

## Explicit composition and authority

`SkillDirectoryCreateService` requires an exact `SkillDirectoryScope` containing
an existing absolute root, device/inode and host revision. The host independently
supplies source/selection permission, exact mutation permission, current-revision
checks and an exclusive writer guard. The immutable mutation proposal includes
that scope, skill/path selection and the primitive's observed parent/target
fingerprint. Read permission does not imply mutation permission. Permission
callbacks must support read-only final acknowledgement checks outside the writer
guard as well as mutation checkpoints inside it. No filesystem mutation is
performed outside the guard.

`bind_skill_directory_creation` accepts an original WSGI/Router/AuthService
application and a separate global request grant. It leaves the original routes
untouched, rejects overlapping routes and swapped service/auth bindings, and
checks the original JWT before filesystem preparation and again at primitive
mutation checkpoints. Construction is inert. This is an opt-in factory, not
new default CLI/runtime wiring. It does not accept arbitrary overlay wrappers;
existing read-family composition is unchanged and combining those factories
requires an explicit host arrangement, not silently inferred authority.

The caller must serialize all tree writers, root renames, mount changes and
permission/revision changes through the same guard. Policy callbacks are trusted,
cooperative checks, not arbitrary mutually revoking/mutating code. This is not
an OS sandbox or a compare-and-swap guarantee against uncooperative writers.

## Supported filesystem subset

The implementation reuses the existing descriptor-relative `WorkspaceMutations`
mkdir primitive. Root ancestors, skill directory and nested parents are opened
without following symlinks. The named root is reopened and its original inode
rechecked after permission callbacks. Named-parent identity is reopened at every primitive grant checkpoint, including
when the primitive already holds an older descriptor. Parent/target fingerprints
are compared before mkdir, and the observed resulting directory is checked before returning
its acknowledgement and again before the response body is delivered.

Unlike Go's recursive `MkdirAll`, every parent must already exist. Missing parents
or skills return 404 without creating them. New directories request mode 0700,
subject to a more restrictive process umask, rather than Go's 0755. Existing
regular files conflict; symlinks, hardlinks, special files, traversal, encoded or
noncanonical paths, overlong paths and depth beyond 32 are refused. No recursive
operations, delete capability or arbitrary filesystem root inference is supplied.
The existing primitive refuses observed cross-device parent/target traversal.

## Failure semantics

The primitive's attempted-effect uncertainty is preserved, including interruption,
fsync failure, post-effect revocation or guard/close failure. These produce an
explicit unknown-outcome response with `retry: false`, never a rollback claim or
automatic second mkdir. An acknowledgement receipt does not repeat the mutation.
If authority, bindings or the observed directory change after response headers,
the body is withheld and transport fails; already-sent 201 headers cannot be
retracted. The operator must inspect the directory before retrying an uncertain
request. No automatic reconcile, recovery or restart authority is introduced.

## Verification

Twenty-six new tests exercise actual temporary POSIX trees, actual JWT/WSGI,
and an actual loopback HTTP server. They cover 401 before filesystem preparation,
source/global/mutation/revision denial, existing-parent and idempotent behavior,
Unicode names over real percent-encoded HTTP, double-encoding refusal,
symlink/hardlink/FIFO refusal, root/parent/target replacement,
request/proposal mutation, concurrent serialization, partial-effect uncertainty,
post-header revocation and original route integrity. All 38 focused tests,
including existing workspace-mutation tests, pass. No user files or production
services were touched. Two initial test failures were fixture mistakes: a regular
file had been hardlinked before expecting a regular-file conflict, and direct
assignment to a frozen Request raised instead of mutating it. The fixtures now
exercise separate ordinary/hardlinked files and deliberate frozen-object tampering.

Independent review cleared the scoped implementation on the prior base. The
unchanged code and tests are integrated onto the AgentEditor head ab631ab6;
combined exact-tree verification remains a separate gate. No publication, merge,
deployment or full Go parity claim.

Independent review found two phase-specific issues in v1 despite the first focused
pass: parent movement during the second grant could create under a displaced
parent, and revocation during the final acknowledgement guard exit could escape
its last check. v2 reopens the named parent at each primitive checkpoint and runs
a complete permission sweep after final guard cleanup, followed by code-owned
filesystem observation. Added tests target exactly those phases, including a
mutation-grant-only revocation. Independent revised review passed 40 focused
tests, 4,243 full Python tests, 526 SQL syntax checks and 68 Node tests on the
prior base. These are temporary filesystem and authored-test results, not a
production deployment claim.

Review also caught a Unicode transport gap: direct Unicode PATH_INFO fixtures did
not model WSGI's Latin-1 carrier. The opt-in dispatch now decodes that carrier as
UTF-8 once before authorization/selection, and the service never URI-decodes again.
Real HTTP percent-encoded Unicode names now reach the intended directory; invalid
UTF-8, literal percent/double-encoded aliases and encoded separator escapes do
not. No global WSGI behavior or unrelated route's decoding is changed.

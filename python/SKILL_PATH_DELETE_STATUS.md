# Explicit Skills file and empty-folder deletion

`SkillPathDeleteService` and `bind_skill_path_deletes` expose only the original
`DELETE /api/skills/{name}/files/{relativePath}` receipt:
`200 {"deleted":"the-exact-relative-path"}`. They accept an original exact
WSGI/Router/AuthService base and an explicit fixed `SkillDirectoryScope`.
Construction is inert; no default mount, root inference or permission is added.
Host/composer/frontend wiring and their lifetime tests are separate required gates.

## Deliberately bounded subset

- Only an existing regular single-link file or empty subdirectory is removable.
  Missing skill, parent or target is 404, not idempotent success. A nonempty
  directory is 409; there is no recursive traversal/removal.
- No whole-skill/root removal, symlink (including dangling), hardlinked file,
  FIFO, device, socket or mounted target. Unsafe paths/targets return 400.
- The top-level manifest is mandatory: any single-segment relative path whose
  `casefold()` is `skill.md` returns 403 before filesystem access. A nested
  `SKILL.md` is ordinary data. This conservative spelling check is not a claim
  to implement every filesystem's Unicode/case normalization behavior.
- Request body and nonempty query selectors are refused. Skill and relative path
  retain the original canonical name/path rules; names/components are bounded
  to 255 UTF-8 bytes, relative paths to the existing 512-byte limit and 32
  segments, and request targets to 4,096 characters.
- The WSGI boundary decodes its Latin-1 URI-byte carrier as UTF-8 exactly once.
  It does not URL-unquote again. Literal percent aliases remain invalid.

## Authority and observed effects

Current/source grants, a separate mutation grant, a separate **permanent-delete**
grant, original JWT/global authority and one shared exclusive writer guard are
required. Read or write permission alone never implies deletion permission.
The immutable proposal identifies the exact scope/selection and native
`MutationTarget`: action, canonical path, observed parent identity, target
fingerprint and file/directory kind. Request, selection, proposal and original
bound method owner/function identities are rechecked through callbacks.

The existing unchanged `WorkspaceMutations.prepare/apply` primitive performs
no-follow descriptor-relative operations. Every primitive callback checkpoint
also freshly reopens the *named* root/parent and compares its original identity,
not only an earlier retained descriptor. This check must not require the removed
target to remain present after unlink/rmdir. Native target comparisons surround
pre-effect callbacks. Directory deletion's native ENOTEMPTY/EEXIST refusal is a
known no-removal conflict, not a recursive fallback.

After removal, read-only `prepare('mkdir', path)` observes the missing slot and
same parent; it never creates anything. The bounded success receipt rechecks
that absence and every grant, including after guard cleanup and before body
handoff. If a replacement appears, it is preserved, never removed by the receipt.

Any attempted removal with unconfirmed outcome, post-removal durability/close/
authority failure, or unconfirmed acknowledgement returns a redacted
`500 {"outcome":"unknown","retry":false,...}` if a response is still possible.
After headers, receipt failure withholds the body. There is no retry, rollback,
undo, journal or recovery operation. A caller must not infer settled deletion
from response headers alone. A containing host must account for dispatched but
abandoned/failed responses, quarantine uncertain mutation and retain ownership;
the standalone service does not provide that lifecycle policy by itself.

These are finite observations under trusted cooperative host callbacks and
exclusive tree ownership. The guard must exclude external writers, root renames
and mount changes. No OS compare-and-swap against adversarial writers, stale
browser-version protection, arbitrary mutually-revoking-callback fixed point,
or authorization atomicity across socket delivery is claimed.

## Source and evidence

Go `server/server_mgmt.go:fsDeletePath` returns the same success DTO but uses
`os.RemoveAll`, including recursive directory removal. This subset intentionally
refuses recursion and top-level manifests, requires distinct deletion authority,
uses current no-follow fingerprints, and conservatively reports uncertainty.
No SQL/visibility cleanup or whole-skill delete is implemented.

`test_skill_path_delete.py` uses only temporary self-owned synthetic files,
original JWT/WSGI and original filesystem primitives. It covers independent
grants, exact receipts, Unicode paths, links/special targets, missing/nonempty
cases, manifest refusal, late parent/target changes, proposal/request drift,
post-effect sync/close/interruption, guard/header revocation, replacement-slot
preservation, receipt close/exhaustion and foreign bound-owner rejection.
There is no user-data removal, database, provider, tool execution or browser test.
Exact host/UI composition, authenticated loopback acceptance and independent
whole-tree review remain required before publication.

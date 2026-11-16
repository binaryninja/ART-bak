# Bounded standalone skill content snapshots

`artex.skills.file_read.SkillFileReader` supplies the read-only prerequisite for
`server/server_mgmt.go:fsReadFile`. It reads one explicitly authorized skill and
canonical relative file under a pinned SkillDirectoryScope. It does not parse or
execute instructions, invoke a skill, write files, register routes or infer a
filesystem root from cwd/home.

Separate name+file authorization, current-scope observer and lifecycle guard are
required. Requests pin the exact selection, scope revision/root identity,
callbacks and bounds. No filesystem open occurs before authorization. Default
raw file bound is 2 MiB, maximum 20 MiB; relative path depth is bounded. Root
ancestors and child directories use descriptor-relative no-follow opening.
Only same-device regular single-link files are read; symlinks, hardlinks, special
files and cross-device nodes refuse. Existing ScopedFilesystem._read uses
O_NONBLOCK and bounded reads with before/after full stat fingerprints, avoiding
FIFO hangs and refusing observed file replacement/change.

Returned SkillFileSnapshot contains inert content, canonical relative filename
and a reader-issued receipt. UTF-8 is decoded; each invalid byte becomes U+FFFD
as with the source JSON path, including incomplete sequences. NUL is retained as
text for a future JSON serializer to escape. Decoded UTF-8 may expand to at most
three times the raw-byte bound. This is not a binary-download API.

Receipt issuance is identity-bound in a weak registry and sealed to original
selection/scope, directory/file stat fingerprints and raw/decoded-content SHA256.
Copied, foreign or mutated receipts and substituted content are refused.
validate_disclosure takes the entire snapshot, rechecks authority and then reopens
the no-follow pathname chain to compare the original directory/file metadata.
Code-owned path checks follow the final policy callbacks and guard exit. A future
HTTP service must retain the exact snapshot and validate it after serialization.
Receipts disappear from the registry when no longer referenced; no persistent
credential or archive is created.

These are observations, not an OS transactional snapshot. Metadata revalidation
does not reread or rehash the file; the host guard must exclude uncooperative
writes, renames and mount changes. Finite cooperative policy observers are not a
universal mutually-mutating callback or concurrency guarantee. Raw filesystem
exceptions are internal only and must be sanitized by a future service; no error
messages or configured paths should be copied to clients.

Validation: 19 focused tests and all 164 skill-focused tests pass. Temporary POSIX
fixtures cover nested/empty/missing content, invalid UTF-8 and NUL, byte/depth/path
bounds, no I/O before authorization, symlink/hardlink/FIFO refusal, copied/foreign/
mutated receipts, content substitution, observed mid-read changes, ancestor
replacement, guard-exit/final-current modification and post-read revocation.
Independent review cleared the current reader within the cooperative-writer
boundary, including additional probes for final-component symlink replacement,
root swap during current-scope observation, snapshot/receipt/selection mutation
during disclosure and a post-read extra hardlink. All refused disclosure.

**FS156 isolated acceptance** passed five actual filesystem groups:
1. Bounded snapshots/issued receipts, malformed/incomplete UTF-8 and NUL behavior,
   observed descendant O_NOFOLLOW/content O_NONBLOCK opens, and authority/current/
   request refusal before any filesystem open.
2. File/directory/root/ancestor symlinks, hardlinks and FIFO refused, canonical
   path/depth constraints enforced, exact byte bound accepted and excess refused.
3. Identical-content inode replacement, same-length writes with restored mtime,
   ancestor/root replacement, writes during os.read and guard-exit writes refused.
4. Final-current grant/request revocation or content change, post-read authority/
   current/request retirement and guard-exit revocation refused disclosure.
5. Foreign-reader receipt ownership, substituted content, copied receipts and
   changed issued receipts refused.

All 164 skill-focused tests passed on the frozen snapshot, with no source or
fixture changes during the run. Manifest SHA256:
`cea805d4accde20cc6b4b6323d1a7a95ac6c1b9faa725ce9c009a76c9649939c`.
Reader SHA256, still matching after execution:
`c44561e0665e7ed6fe0174dae61702f739b63f663a81a4eb339f55cf5281d57e`.
FS156 used temporary synthetic files in a fresh credentialless filesystem/user/
network namespace, UID1000, no external route or host workspace/home access.
Contents were only read, never executed. There was no HTTP endpoint, browser,
provider, target or database involvement, shared checkout/index change or
publication. This is bounded observed-mutation component evidence, not an
exhaustive race guarantee or validation against arbitrary concurrent writers.

No HTTP transport, live configured-root use, frontend parity or production
readiness is claimed. Only new reader/test/status files are added by this slice.

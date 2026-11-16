# Bounded standalone skill file-name listing

`artex.skills.file_listing.SkillFileListingReader` is the filesystem prerequisite
for `server/server_mgmt.go:walkSkillFiles` / `fsListFiles`. It returns recursive
relative names, directories with trailing '/', in sorted depth-first WalkDir
order. Empty directories remain visible. No file contents are opened, parsed or
executed, including SKILL.md; no filesystem writes occur.

Caller must supply an exact SkillDirectoryScope with absolute root, device/inode
identity and revision, plus independent name-specific authorization, current-scope
callback and lifecycle guard. Construction is inert. Root opening occurs only
after authorization. ScopedFilesystem provides no-follow root ancestor traversal;
recursive directory opening uses parent descriptors and O_NOFOLLOW/O_DIRECTORY.
Root and child directory identities/metadata are checked before and after traversal.

The root pathname is reopened without following symlinks and its pinned device/
inode checked after the last policy callback, both after read guard exit and in
validate_disclosure. Thus observed root replacement during final callbacks is
refused. A future HTTP serializer must call final disclosure validation itself.
The result is an observed listing, not a perpetual guarantee that each filename
still exists after return.

Strict differences from Go: no symlink entries, hardlinked regular files, special
files or cross-device directories; malformed/noncanonical relative names fail
closed; unreadable/missing entries are not silently omitted. Name text is inert,
not permission to invoke any skill. Missing skills raise FileNotFoundError for a
future service to map to a generic 404, while raw filesystem exceptions are not
intended as client-visible messages.

Bounds cover total discovered entries globally (including pending ancestor
siblings), depth and cumulative UTF-8 relative-name bytes. Counts are checked
before stat/collection; names are sorted only after bounded collection. Oversize
refuses rather than truncating. Directory metadata/identity changes detected
while reading refuse the result.

The caller's guard must exclude uncooperative renames, mounts and writes and
coordinate revocation. Descriptor/metadata checks are not a filesystem-wide
transaction, OS snapshot, or complete TOCTOU prevention. Finite final grant
observers remain cooperative/nonmutating; arbitrary callbacks cannot be made
atomically consistent by repeated checks. No concurrency or hostile-root safety
claim beyond these explicit constraints.

Validation: 20 focused tests and all 112 skill-related tests pass. Tests use real
temporary POSIX trees for nested/empty ordering, missing names, root mismatch,
root/descendant symlink rejection, hardlink/FIFO refusal, byte/depth/entry limits,
no content reads, scope/grant changes and post-read/guard-exit/final-current root
replacement. An injected stat/open replacement is refused, and a counting-scandir
fixture verifies ancestor siblings count toward the global discovery limit.

This is not an HTTP endpoint, app integration, route-count increase, metadata
catalog, skill invocation, live configured-root acceptance or production-readiness
claim. Existing Python modules and original Go files are unchanged. Independent
review is in progress; no publication or staging is performed by this slice.

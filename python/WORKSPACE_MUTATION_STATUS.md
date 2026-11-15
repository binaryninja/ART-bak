# Optional workspace mutation subset

Explicit workspace_mutation_service composition adds only requested mkdir/delete
handlers with exact service type and no inferred root or policies. Authentication
runs before resolution. Each operation requires a fresh immutable inode-bound
proposal and host approval; permanent deletion has a separate approval gate.
Construction performs no filesystem changes and does not enable routes by default.

Descriptor-relative no-follow operations reject root deletion, traversal,
symlinks, hardlinked regular files, special files and cross-device paths. The
supported subset deliberately differs from Go's recursive operations: mkdir
requires an existing parent, and delete accepts only regular single-link files
or empty directories. Nonempty directories are rejected without deleting children.

Root/task/revision and parent/target fingerprints, current authority and an
exclusive host guard must remain consistent. All cooperative writers must honor
that guard; this is not an operating-system sandbox against external writers or
mount changes. Mutation-attempt uncertainty is retained even on interruption;
cleanup cannot mask the primary unknown outcome. No automatic retry or rollback
is claimed after possible effects. Invalid symlink-ancestor paths fail before
mutation with a generic response that exposes no paths.

Tests use only temporary synthetic fixtures. See ISOLATED_QA.md stage 57 for the
initial mismatch and corrected evidence. Original source paths remain unchanged.

Frozen checkpoint: 2,029 authored tests pass; 400 captured SQL statements parse.
Static method/path registrations are 156/261 (105 absent), including optional
and unavailable contracts, not complete-feature or readiness counts.

# Strict existing transcript checkpoint observation

The next restart prerequisite is byte/chain integrity, not another startup SQL
inventory. `inspect_checkpoint` is a low-level opt-in filesystem reader alongside
`TranscriptStore`. Host authorization and task quiescence remain host duties.
No launcher, manager, continuation owner, bootstrap fallback or held handoff
manifest is wired to it. No schema or runtime ownership changes are introduced.

## Profile grounded in existing writers

`TranscriptStore._record` emits a message or boundary dictionary, a nonempty UUID,
session ID, optional sidechain agent/is_sidechain, and a parent UUID naming the
previous record. `_write` produces newline-terminated UTF-8 JSONL. The inspector
checks precisely this linked-chain subset, plus duplicate UUIDs and JSON keys.
Unknown fields are ignored without rewriting. This is deliberately stricter than
`commit`, which accepts arbitrary batches without requiring a parent chain, and
is NOT a validator for all Norma/imported logs or a migration policy. Rejection
never means an existing transcript should be deleted or repaired automatically.

Existing empty files give zero records with a real digest; missing files reject.
Unlike `begin`, no missing transcript is synthesized as an empty snapshot. No lock
or sidechain directories are created. Existing no-follow regular single-link lock
and file are required. The shared session flock excludes cooperating store writers
while the complete bytes and chain are observed. Reads honor store.max_bytes and
max_records (default10,000, hard100,000); this bounds memory input, not waiting time
for an existing writer. Inode/size/mtime/ctime/link rechecks detect ordinary
uncooperative replacement/in-place change. They do not fence malicious writers,
privileged directory renames or future modifications. Existing root/ancestor trust
requirements apply. Symlink/hardlink/missing/corrupt/oversize errors are redacted.

Output includes session/agent, SHA256 of exact bytes, byte and record count, tail
UUID and immutable-init `recovery_authorized=False`, never message payloads.
Hash equality is not authenticity, task membership, authority or completeness.
The host must authorize disclosure of even these identifiers. A caller can compare
this ephemeral observation with a separately trusted checkpoint, but cannot derive
permission to resume from it. No database/file atomicity is claimed.

## Validation

Author: 19 tests across test_transcript_checkpoint.py and test_transcripts.py pass.
Seven new tests cover actual message/boundary/sidechain writers, unchanged files,
missing vs empty, no filesystem creation, malformed identity/parent/UUID/payload,
partial/non-UTF-8 JSONL, byte/record bounds, symlinks/hardlinks, concurrent in-place mutation
and a cooperating writer blocked until observation completes. Independent review
is pending. No genuine restart/continuation acceptance claim.

## Remaining recovery protocol

A future clean-handoff protocol still needs an authenticated, complete durable
inventory of task/bootstrap/config/graph/transcripts, durable execution epochs and
CAS takeover, writer fencing across all effect paths, old-owner quiescence,
clock/process/finalizer reconciliation, and crash-point tests across SQL and file
commits. Existing same-process suspension capabilities cannot be rebuilt from this
observation or a prepared bootstrap row. Pending/uncertain owners must stay held.

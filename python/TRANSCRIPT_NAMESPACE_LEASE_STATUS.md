# Opt-in canonical transcript namespace lease

Use an existing trusted local root, then bind and acquire before constructing
runtime resources:

```python
store = TranscriptStore(explicit_existing_root)
lease = RuntimeLease.for_transcripts(store)
lease.acquire()
transcript = ChatTranscriptAdapter(store)
# Construct the original TaskRuntimeAssembly/listener with this exact store,
# then RuntimeSession(listener, lease). Nothing starts automatically.
```

This uses the existing RuntimeLease, not a second ownership primitive. The
factory is filesystem-inert and one-shot on an exact unused native TranscriptStore.
The fixed `.artex-runtime.lock` name is derived from that store's absolute root;
normal path spelling aliases select the same OS lock inode. An unrelated filename
cannot be substituted after binding. Any previous native I/O on that store object
refuses late binding. A different store object at the same path is not an ownership
capability: RuntimeSession requires the exact bound runtime store before claiming
its lease. Existing arbitrary-path leases and unbound stores retain their legacy
contracts; this stricter mode is explicit, never inferred from matching paths.
An independent original-binding pin detects a single accidental lease-pointer
change before I/O instead of silently falling back to the legacy path. This is
robustness against drift, not protection against arbitrary private-field edits.
Owner selection invokes its captured original helper, so replacing that one
method cannot select legacy I/O before the original-method checks are reached.

## Original native paths and local lifetime

All native TranscriptStore operations using its original `_locked` path, including
canonical ChatTranscriptAdapter admission/finalization, enter a counted lease I/O
scope before root/session locks and retain it through their synchronous cleanup.
The public `inspect_checkpoint` helper shares that scope without creating any
session locks or files. Its read, validation and cleanup are counted, and a final
lease check must succeed before it returns its non-authoritative observation.
The lease mutex never spans transcript locks or caller callbacks. Release, claim
and transfer refuse while a bound I/O scope remains active. Thus even an admitted
canonical I/O call outside a host step prevents RuntimeSession's final release;
when it returns, an explicit stop retry may finish. There is no force release or
background retry. Existing guarded session release permits are unchanged.

The actual opened root descriptor must match the leased directory's device/inode
before a session lock/file can be created or read. Each bound entry and successful
exit also checks original object/root-locking method identity, PID, stored path, lock inode,
file type/link count/permissions and live root ancestry. Final observed drift
withholds the new result, not earlier filesystem effects; reconciliation, never
blind replay, remains the caller's duty.

Bound namespace traversal follows no symlinks in any path component. Ancestors
must belong to root or the effective user and cannot be group/other-writable
unless sticky, allowing ordinary root-owned `/tmp` safely. The final namespace
must satisfy the existing effective-user-owned, non-group/other-writable lease
storage policy. Lock files remain private, regular and single-link; symlinks,
hardlinks, replacement and permissive lock modes refuse. Acquisition never
truncates or unlinks the lock. Native transcript file/descendant no-follow,
single-link, bounded-write and atomic replacement rules remain unchanged.

## Trust boundary and limitations

This is cooperative single-host POSIX namespace exclusion and local I/O lifetime
accounting, not a sandbox or a task epoch. All participating writers must use the
bound original store and canonical lock. Legacy/unbound stores, raw/private store
entry points, other programs, replaced implementations, privileged/same-user
filesystem mutation, detached work and cross-host/NFS locking are outside the
contract. Namespace contents/descendants and the original native close contracts
remain trusted. No fork while holding the lease is supported.

The count observes callbacks through their return/raise, not successful I/O or
resource reconciliation. Ordinary I/O/BaseException unwinding does not itself
unlock the process lease. A failed close or fsync can leave an uncertain local
resource or commit; zero active calls is not proof that such failures were undone
or all descriptors closed. The resource owner must reconcile them. Existing
RuntimeLease final OS-close ambiguity is unchanged, so an unresolved result after
that attempt must not be described as verified kernel-lock retention.

The lease is not tied to a database incarnation. Different transcript namespaces
remain independent even if configured against the same database. Process death,
lock availability or a successful transcript read does not authorize task restart,
HELD adoption, provider retry or use of ChatTranscriptAdapter.recover_interrupted.
Current manager/token/grant, exact task scope, transcript and cancellation rules
are unchanged. No epoch, checkpoint, database connection, provider operation,
listener, default CLI behavior or recovery path is added.

## Authored evidence

Tests use actual native filesystem/lease/store/adapter/session code with inert
SQL. Independent Python child processes exercise both flock and the native
canonical factory: contention while held, acquisition only after release, stable
lock inode and no transcript read before acquisition. Cases include aliases,
root/ancestor symlinks, hardlinks, unsafe ancestry/modes, path/root/lock replacement,
PID drift, original method changes, late/wrong-object binding, binding-vs-first-I/O
races, actual canonical admission write tails, session-lock waits, native directory
cleanup tails, BaseException, original guarded permits and session shutdown with
outstanding canonical I/O. Checkpoint cases cover missing/acquired/released lease,
legacy compatibility, read/cleanup tails, final-check drift, control-flow interruption
and actual RuntimeSession release refusal/retry. Both single-field binding-pointer
drift directions and instance/class owner-helper substitution refuse before
files are opened or created. No live PostgreSQL, provider, listening socket, target,
or restart/adoption acceptance is claimed. Independent review remains required.

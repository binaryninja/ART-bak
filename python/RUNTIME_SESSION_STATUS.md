# Explicit development process lease and runtime session

This is a launcher building block, not a supported end-user CLI or production
runtime. Acquire `RuntimeLease(absolute_path)` before constructing any runtime
resources. The caller remains responsible for cleaning up partial assembly before
releasing an unclaimed lease. Construct a fresh exact RuntimeListener with the
existing explicit RuntimeResourceOwner, then `RuntimeSession(listener, lease)`.
Session construction starts nothing and claims the lease for one session only.

`start(shutdown_timeout=10)` starts the loopback listener, then the existing pump.
Partial startup failure runs the listener's existing admission-stop, HTTP/pump
shutdown, cancellation, drain and resource/pool closure path. It releases the
lease only after the original listener stop method and callback-free closed-lifetime
proof confirm stopped. A returned ListenerStop value alone is not proof. Unresolved
startup or shutdown before release retains the listener, resources and lease; explicit stop may retry drain,
but start never retries an effectful partial start. Generic results do not log
exception payloads. Busy/reentrant operations return a busy result without
releasing ownership. Timeout bounds are cooperative, not callback preemption.

The POSIX nonblocking flock lease is a cooperating-process exclusion mechanism.
The parent directory must be owned by the effective user and not group/other
writable. The final directory/file may not be symlinks; the file must be regular,
private, owned and single-link. Descriptor/path inode checks detect observed
replacement at acquisition, explicit validation and startup boundaries. There is
no continuous lease monitor; all runtime users must preserve the claimed lease.
Files are opened close-on-exec, never truncated, and never unlinked
on release, avoiding the ordinary unlink/recreate lock split. One lease cannot
be claimed by two sessions. Session claim/transfer now creates a private release
permit: the public session object alone cannot release or transfer its lease. This
is an internal cooperative API boundary, not Python object-access isolation.

All cooperating hosts must select the same file in trusted local storage and
acquire it BEFORE assembly. Ancestors and owner-controlled code must be trusted.
This is not a distributed/database fencing token or an adversarial filesystem
boundary. In particular it cannot stop a directory owner replacing a live lock
or prevent code bypassing the API. No fork while holding the lease is supported;
PID checks refuse child use/release, but inherited descriptors can prolong locks.
Process death releases the OS lock but does not prove old database/runtime effects
settled. There is no automatic recovery or stale-task takeover.

The original lease/session tests pass. Actual separate Python processes verify
contention and post-release acquisition. Storage tests cover symlink, permissions, replacement,
preserved inode, and PID mismatch. Lifecycle tests use the existing real assembly
with inert stores and mocked start boundaries: success order/idempotency, partial
startup cleanup, unresolved drain retention and explicit reconciliation, exception
and malformed stop results, single-session claims, reentrancy and invalid input.
No real listening socket, live provider, target or production database is used.
No default mount, signal handler, CLI execution/config discovery, deployment or
merge is added.

## Full-lifetime host closure candidate

The existing process lease remains the ownership primitive. This change closes a
real shutdown gap rather than introducing a second lock: previously the host could
accept owned work or metadata writes after a successful drain while the listener
closed resources, and a structurally successful replacement stop could unlock the
session. The normal development session/listener now completes these stages:

1. Permanently seal original host admission, then preserve the existing external
   admission, HTTP/pump stop, cancellation, and manual/continuation wait stages.
2. Obtain an actual clean host drain. Its private proof is bound to the exact result
   object and current accepted-operation serial. Accepted lifecycle calls, owned
   work, polling, and hosted metadata writes invalidate any earlier proof, even if
   the later operation has already returned. Copied or fabricated values do not
   mint the private proof.
3. Atomically close the host execution lifetime under its own condition mutex,
   only when admission is sealed, that proof is still current, and all host slots
   are idle. It neither calls a callback nor releases a task or resource owner.
   Closure rejects future host effects, including pause/inspection/settlement.
   Before closure, existing authorized pause and cancellation remain available;
   denied pause does not gain permission, and retained paused continuations still
   prevent clean drain.
4. Close resources and drain the pool, then verify the original closed stages
   before releasing the session's process lock. Resource failure leaves the host
   permanently closed and the lock held. Explicit retry resumes resource/pool
   cleanup, not cancellation or now-forbidden host draining.

The existing owned-callback contract is unchanged: a callback must return only
when all of its local work has ceased, without detached work. Direct manager,
runner, provider, store, filesystem or component calls outside this host/listener
ownership contract are not fenced. A policy callback is not made trustworthy by
this lease, nor does this change replace existing current/grant checks. The host
serial is only private same-process provenance, not a durable epoch or checkpoint.

The lock must already be acquired before assembly, and the same canonical lock
file must be selected by all cooperating launchers. The scope is a live process
using trusted local POSIX storage, not protection against malicious same-user code,
lock-path replacement, a remote host, inherited fork descriptors, or broken kernel
locking. A failure during the final OS descriptor-close attempt is unresolved:
shutdown must not claim the OS lock is still held merely from a generic error.
There is no provider-outcome reconciliation, DB disconnect fencing, recovery,
restart/adoption permission, checkpoint, new task generation, or new persistence.

Eleven added tests exercise the actual host/listener/session and POSIX lease with
inert SQL and no listening socket. Independent child Python processes verify the
lock stays held during blocked owned callbacks and resource cleanup, then becomes
available after verified stop. Adversarial cases cover copied/fabricated/stale
proofs, closure-vs-admission races, callback interruption, early public-owner
release/transfer refusal, cleanup retry, post-close metadata/host entry refusal,
post-close claim callback refusal, and unchanged authorized/denied pause behavior.
Full exact-tree validation and independent review results are recorded separately;
no live PostgreSQL, provider, target or process-restart test is implied.

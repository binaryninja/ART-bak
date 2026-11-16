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
lease only after an exact ListenerStop confirms stopped. Unresolved startup or
shutdown retains the listener, resources and lease; explicit stop may retry drain,
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
be claimed by two sessions; ordinary direct release of a claimed lease is refused.

All cooperating hosts must select the same file in trusted local storage and
acquire it BEFORE assembly. Ancestors and owner-controlled code must be trusted.
This is not a distributed/database fencing token or an adversarial filesystem
boundary. In particular it cannot stop a directory owner replacing a live lock
or prevent code bypassing the API. No fork while holding the lease is supported;
PID checks refuse child use/release, but inherited descriptors can prolong locks.
Process death releases the OS lock but does not prove old database/runtime effects
settled. There is no automatic recovery or stale-task takeover.

Eleven new tests pass. Actual separate Python processes verify contention and
post-release acquisition. Storage tests cover symlink, permissions, replacement,
preserved inode, and PID mismatch. Lifecycle tests use the existing real assembly
with inert stores and mocked start boundaries: success order/idempotency, partial
startup cleanup, unresolved drain retention and explicit reconciliation, exception
and malformed stop results, single-session claims, reentrancy and invalid input.
No real listening socket, live provider, target or production database is used.
Full frozen suite and independent review are pending. No default mount, signal
handler, CLI execution/config discovery, deployment or merge is added.

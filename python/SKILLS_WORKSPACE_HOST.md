# Opt-in Skills-only development host

This separate launcher is **not the ARTEX task runtime replacement**. It starts
no PostgreSQL, task engine, provider, tool execution, subprocess tool, archive,
ledger or background task pump. Skill text is data, never instructions to execute.
Nothing is mounted in the default application and no existing CLI is changed.

## Explicit operator setup

Use a POSIX system and the official optional `bcrypt>=5,<6` dependency. Choose an
existing absolute, canonical, current-user-owned **mode0700** skills root. Each
skill directory, including its `SKILL.md`, must already exist; this host does not
create top-level skills. Keep only intended skill data there. Do not use a shared
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
the authenticated local operator may create/overwrite text files and create
subdirectories anywhere within that explicit skills root. This is single-admin
workspace authority, not per-skill or per-user authorization. A successful file
PUT is an upsert, not browser-version compare-and-swap. Existing directories are
idempotent; parent directories must exist. Existing file and directory service
limits and filesystem restrictions remain unchanged. The host caps content reads
at 256 KiB and uses the existing bounded request/body/connection transport.

Authentication is initialized with real bcrypt before any socket accepts requests;
unauthenticated initialization therefore cannot take over the host. The hash and
random JWT key are memory-only. Changing a password over the existing auth API is
memory-only and does not update the operator's input file. Restart invalidates
all old tokens and reloads the explicit password file. The launcher never prints
passwords, hashes, signing keys, tokens, request paths or query parameters.

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
reachable through a valid skill route. The shared RLock covers all five service
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
output. Fault probes distinguish in-process injections from actual HTTP behavior.
Lowercase HTTP PUT currently refuses 405 in the original write overlay; normalized
host quarantine accounting is additionally tested with a synthetic lowercase 500.

No PostgreSQL, live provider, user files, real target, browser, production service
or external network was used. No mutation is retried. Unknown-case test teardown
is not evidence that production can cleanly release unknown ownership. Independent
review and exact combined frontend acceptance remain separate gates; passing this
launcher does not establish the broader Python runtime's functional parity.

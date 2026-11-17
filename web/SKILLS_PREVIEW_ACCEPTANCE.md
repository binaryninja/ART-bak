# Repeatable Skills preview smoke check

This explicit, optional acceptance command runs the original frontend `api.ts`
and Skills state model through a real prebuilt Next proxy to the published Python
Skills CLI. It is **not a browser test**: a small Node fixture provides storage,
cookie and fetch globals; no React component is mounted. It complements, rather
than replaces, the default Node/unit/type/build checks.

## Prepare and run

Use a disposable POSIX checkout with Node 24.22.1, its already-installed locked
frontend dependencies, Python 3.11+ and the official `bcrypt` auth extra installed
in that Python interpreter. The runner installs nothing and does not inherit
`PYTHONPATH` for optional dependencies. There is no automatic dependency fallback.
Build a matching, non-mock preview first, then keep its source/dependencies/build
unchanged throughout the run. No existing development server may use either port.

```sh
cd web
ARTEX_PYTHON_PREVIEW=1 AUTOPENTEST_API=http://127.0.0.1:8787 npm run build
cd ..
PYTHONPATH=python python python/integration/skills_preview_acceptance.py \
  --frontend-port 3000 --backend-port 8787
```

The backend port must match the build's rewrite. The existing preview launcher
rejects missing/invalid artifacts, wrong profiles and wrong-backend rewrites, but
does not detect a stale build with the same profile and port. Matching current
source to the built browser bundle remains an operator prerequisite. Choose different explicit ports and
rebuild if these are occupied; the runner does not attach to an existing host.
Only IPv4 loopback is used. Assertions must remain enabled.

The runner creates a new private temporary directory with synthetic skill data,
a random synthetic password outside each Skills root, private logs and JSON
results. It accepts no existing root, credentials, URL, database or target. Its
children receive a minimal environment without ambient provider/database keys,
proxy settings, `NODE_OPTIONS` or Next profile overrides. This is **not** an OS or
network sandbox: source and installed dependencies are trusted executable code.

## Verified path and evidence

Both read-only and write-enabled phases check real bcrypt/JWT login and required
authentication, mode discovery, initial catalog and manifest reads. Read-only mode
must suppress model writes and refuse direct API writes without filesystem effects.
The write-enabled phase checks Unicode skill creation, nested folder/file creation,
edit/save/read/catalog, exact receipts, duplicate-create 409 and Unicode HEAD
length/body semantics. Both serve the real Skills page and one referenced script.
The Python runner independently checks the synthetic files, then requires natural
frontend and backend SIGTERM exits with status zero before reacquiring the Skills
lease. It does not retry mutations, inject startup failures, enable deletion or
import, start a task, connect to PostgreSQL or contact a model/provider/target.

The printed private evidence directory is retained, including failures. Inspect
`result.json` and each mode's `result.json`; the runner exits nonzero on failure.
Logs can contain synthetic fixture values and are not automatically published.
A bounded timeout is a failure, never a successful drain. On a failed run only,
the runner attempts to terminate every still-running owned synthetic child process
group and records `forced_fixture_cleanup: true`; that is explicitly **not**
passing shutdown evidence. Signal, wait and log-close failures are recorded by
stage/type in `cleanup_errors`, without copying exception messages. One failure
does not skip attempts for the other children, log closure or phase evidence.
Cleanup is best-effort: a failed signal/wait can leave a child alive. A failure to
write the evidence itself exits nonzero; complete evidence is not guaranteed when
its storage is unavailable.

Ordinary SIGINT/SIGTERM cancellation unwinds into that failed-fixture cleanup.
Cancellation first received during finalization is latched without interrupting
the remaining cleanup attempts. Repeated cancellation signals are suppressed
during cleanup, whose per-child waits are bounded. Forced process termination (SIGKILL), host loss or an
interpreter crash cannot run Python finalizers and can leave fixtures/services
behind. No global process sweep or real-user-data cleanup is performed.
Phase and top-level result writes share the same cancellation deferral. If the
first signal arrives inside a result write, its cancelled/failed flags are saved
before cancellation propagates as a nonzero CLI result.

This is an optional local check, not a new CI job, rendered-browser acceptance,
production readiness, import/metadata coverage, recovery test or full Go parity
claim. The prebuilt server does not fix the separate development watcher issue.

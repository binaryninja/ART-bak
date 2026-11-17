# Explicit foreground signal-to-drain bridge

`RuntimeSession.run_until_signalled(signals, stop_event)` is an opt-in development
entry point using a fresh, inert `RuntimeSignalScope`. It temporarily owns SIGINT
and SIGTERM on the main Python thread, observes a signal latch in the existing
foreground loop, and invokes the existing listener shutdown sequence. The ordinary
`run_until_stopped` API still installs no signal handlers. Nothing changes the
packaged CLI, default assembly, HTTP routes, provider configuration or task policy.

Source mapping: `cmd/artex/main.go:93-96,128-144` registers interrupt/termination
and starts shutdown on notification. This slice ports that entry into shutdown,
not Go's updater/restart exit codes, cancellation-cause machinery, process exit,
all manager close behavior or a production launcher.

## Ownership and behavior

The handler records only the first observed signal number. It does not set the
Event, take locks, call prior handlers, log, stop the listener, cancel tasks, close
resources or release the lease. This follows Python's explicit warning against
synchronization primitives inside signal handlers: they can deadlock when the
interrupted main thread already holds the same lock. See the
[official signal documentation](https://docs.python.org/3/library/signal.html#signals-and-threads).
Repeated signals coalesce and do not force exit or interrupt an ongoing drain.

The foreground loop polls the latch at its existing configured interval. A signal
observed before startup suppresses listener/pump startup. A signal during a startup
or shutdown callback does not preempt that callback. Callbacks, C extensions, SQL
and provider operations remain cooperative and can delay observation indefinitely;
poll_interval is not an overall shutdown latency guarantee. SIGKILL/crashes cannot
be handled. The startup sequence is not interrupted between listener and pump:
a signal during listener startup can still be followed by pump startup before the
next foreground observation. No signal-mask changes, helper thread, background watcher or fork is
introduced. Hosts must not fork or independently replace handlers during a run.

The existing admission-stop, transport/pump stop, owned cancellation and runtime
drain order remains authoritative. A failed/unresolved drain retains the exact
session and process lease; no retry, forced release, recovery capability or
rollback is inferred. The returned `SignalForegroundResult` separately reports
the existing foreground/shutdown result, observed signal number, and whether
original handlers were restored. It does not choose an exit status or authorize
the host to exit with unresolved ownership.

Signal restoration runs after the foreground call, including exceptional exits.
Only handlers still owned by this scope are changed; a foreign replacement is
left untouched and restoration reports false. Failed restoration retains the
process-wide scope claim and the caller's scope object supports an explicit retry.
Unknown non-Python original handlers reject before installation because Python
cannot restore them. A partially failed installation attempts restoration, starts
no runtime and raises a sanitized error; the caller must retain and inspect the
scope and original unused session. Non-main, nested and reused scopes reject.
There is no default-handler fallback or invisible forced cleanup.
Restoration describes Python handler identities, not an exact OS sigaction or
SA_RESTART configuration; Python's signal API does not expose that full snapshot.

Restored original handlers apply again after the method returns, even if shutdown
was unresolved. The host is responsible for retaining the process and reconciling
that result. OS termination always releases OS locks; a retained Python lease is
not crash safety, durable ownership or protection against a later forced exit.

## Validation

Eighteen new authored tests pass. They cover inert construction, first-signal
coalescing while an Event condition is held, restoration/retry, partial installation
failure after an installed handler, main-thread/PID/nested/reuse refusal, unknown
original handlers, invalid arguments, pre-requested stop, startup failure,
exceptional exit and unresolved shutdown without an automatic retry.
Independent review caught an initial refactor regression: ignoring a True result
from Event.wait could lose an accepted stop notification if another host thread
cleared the Event before the next is_set. The foreground driver again honors the
wait result directly; a deterministic regression test preserves the existing
non-signal API contract. The initial candidate's green suite did not catch it.

Three tests launch real child Python processes using inert runtime stores, actual
loopback HTTP, listener and pump threads, and a real filesystem lease. An
authenticated probe succeeds before the parent sends SIGINT or SIGTERM. Clean
drain stops both threads, closes resources once, restores original handlers and
allows a new lease. Repeated SIGINT/SIGTERM while the resource-close callback is
running do not interrupt cleanup. A controlled unresolved host drain retains the
lease and resources, refuses a competing lease, then closes only on explicit retry.
No live database, provider credentials, targets or upstream scripts are used.

The 53 focused signal/session/foreground/listener/development tests pass. The
corrected original-base (`55c5feb`) suite passes all 4,156 authored Python tests, with no skips
reported (41.057 seconds). `git diff --check` is clean. Optional pglast is not
installed and SQL syntax QA was not rerun; this slice changes no SQL. No frontend
or live PostgreSQL acceptance is claimed.

Independent read-only re-review cleared the corrected code and reran all 53
focused tests (2.441 seconds). Every entry in the frozen v3 source manifest was
verified before and after testing. Additional independent probes checked that a
stale restored scope cannot overwrite a newer scope, and a first-install
pre-effect failure restores originals and permits a fresh scope.

Reviewed source hashes:

- runtime_signals.py: `5acf0ac91498cad6606cc2344fbe5452f224ce0bf5297b3a9ebba82ec3d8078a`
- runtime_session.py: `d05fdb9dd295e96639666699e9b9b5ac8ae7d1f45aadfb56a187b0686892b92e`
- test_runtime_signals.py: `f2ea37dce10e16d87fb2ce618a92af65ca698c96486d95d7f0fc0ebfa98ea635`

The reviewed snapshot manifest is
`2dbf154b4be98bc93d3f89bdd97a8f32e4728ae34ac3bca3a0927adc99c69bc3`.
This validation paragraph was added after review; source and tests are unchanged.
The candidate was then applied without conflicts atop `2e7e1c2`, preserving the
separate explicit-frontend-origin changes. All 59 combined signal/lifecycle/origin
focused tests pass, and all 4,162 Python tests pass with no skips reported. The
reviewed signal source/test hashes above are unchanged in that integrated tree.
This is local candidate work only; no staging, commit, publication, merge or
deployment has occurred.

# Foreground development runtime lifecycle

`RuntimeSession.run_until_stopped(stop_event, poll_interval=0.1,
shutdown_timeout=10)` provides an explicit blocking run over an already assembled
session. It starts the existing listener and engine pump, observes the stop event,
lease identity, existing listener bindings, listener thread, pump thread and pump
failure state, then invokes the existing session drain. A pre-set event never
starts the listener or pump. One driver may own a fresh session; it is not a way
to adopt an already-running host. Hosts must not independently tick/start/stop the
same session while the foreground driver owns it.

The returned ForegroundResult distinguishes stop requested, startup failure,
pump failure, stopped threads, unavailable ownership and interruption. Its
SessionResult reports whether shutdown actually completed. Startup failure's
existing cleanup is not automatically retried. An unresolved result retains the
session/lease and requires explicit host reconciliation; the function does not
exit the process, force-close resources, retry execution or falsely declare drain.
Generic result labels exclude exception payloads.

The host still explicitly acquires a RuntimeLease before constructing resources,
provides the exact assembled RuntimeListener and its original shutdown owner,
and sets a threading.Event when it wants to stop. Example control flow after
those explicit construction steps:

```python
session = RuntimeSession(listener, acquired_lease)
result = session.run_until_stopped(stop_requested)
if not result.shutdown.stopped:
    # Retain session and all owned resources for explicit reconciliation.
    # Do not release the lease or exit merely because the driver returned.
    reconcile_owned_runtime(session, result)
```

The example's reconciliation callback is host-provided, not an implementation or
permission supplied by this module. No config-based factory imports, default
credentials, provider selection, signal handlers, CLI command, schema installation
or recovery policy is added. Polling is a finite liveness observation, not atomic
revocation, application readiness or guaranteed task progress. Callback and SQL
execution remain cooperative and may outlast requested shutdown timeout. A process
termination signal without a host-installed handler still terminates the process;
KeyboardInterrupt/SystemExit reaching the driver trigger a drain attempt but are
not a proof of cancellation of arbitrary in-flight effects.

Five new tests pass. One uses the real development loopback socket, real idle
engine pump and foreground thread with inert stores: missing JWT returns 401,
valid synthetic JWT returns 200, requested shutdown joins both threads and releases
the lease, and no provider call occurs. Other tests cover pre-requested stop,
unretried failed startup, health/interruption failure labels, retained unresolved
lease, invalid options and duplicate driver refusal. Health-failure boundaries
are mocked; this is not a live database/provider or task-completion acceptance run.
Independent review and full frozen suite remain pending. No production readiness,
default launch, merge or deployment is claimed.

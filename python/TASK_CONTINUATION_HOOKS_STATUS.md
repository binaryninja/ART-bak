# Optional zero-timeout continuation registry hooks

Explicit `host.install_continuation(actual_owner)` installs one original typed
owner on the same manager/runner. No existing app/setup enables it by default.
`host.pause` reserves original prepared ownership before cancellation; verified
worker cleanup/drain and successful pause persistence precede confirmation of a
usable private suspension. The original canceled owner remains retained even
when its execution registry row is removed. Timed, terminal and unprepared
continuations are rejected by the owner. No restart/SQL-only adoption.

`host.resume_suspended` or existing facade `control_task(...,'resume')` reserves a
fresh manager token, verifies the private owner, constructs an inert replacement,
performs the owner's exact paused snapshot CAS, installs the exact new runner
handle, merges the persisted task, and confirms installed ownership outside the
manager lock. Execution remains denied while the installed suspension slot
exists. Only successful final acknowledgement consumes it. Unknown outcomes
retain original/new generations and quarantine; no compensation/retry pretending
rollback. Owner method replacements cannot substitute authority.

Source task pause cancels active worker with task_pause: verified canonical
cleanup returns that claimed intent to open. Explicit worker_pause remains paused
and planner-kill remains stopped. These hooks do not bulk reopen any graph state,
replay goals, alter immutable bootstrap receipts/configuration or resurrect
terminal tasks. First resumed owned advance is architecture's engine-only seam.

Capacity: existing TaskHTTPManagerFacade still requires its shared admission
reservation for resume. Missing capacity policy refuses before writes. Lower-level
manager/host calls remain unsupported alongside a scheduler unless the caller
owns that same reservation, as with existing direct start. Queued continuation
admission is explicitly unsupported: no automatic dequeue or scheduler bypass.

Shutdown: all suspension phases, including clean paused, remain inventoried as
continuation ownership. Host all_settled/drain does not authorize closing their
resources. No release-only shutdown or durable restart recovery protocol is
implemented; callers must not discard retained suspension resources.

Tests use genuine initialized zero-task fixture, actual manager/host/runner and
BootstrapHandle, temporary canonical transcripts and inert SQL/provider fixtures.
Independent integration review and immutable PostgreSQL stage141 now pass for
this narrow core path; see ISOLATED_QA.md. Explicit application setup and JWT
control acceptance remain separate work.

# Task detail read diagnostics

The existing task screen already submits through `POST /api/tasks`, refreshes the
task list and opens `/function/tasks/detail?id=...`. This change does not create,
start or execute tasks and does not establish a runnable Python application.

Previously every initial detail-read failure was displayed as if the task had
been deleted, archived or did not exist. The detail screen now distinguishes a
received HTTP 404 from transport, permission, unavailable-capability and other
read failures. Even for 404 it reports only that response, not proof of deletion
or archival. The original server error text is rendered as ordinary React text.

The error view offers `Retry read`, calling only the existing task/stats reader.
Its existing request-identity fence coalesces duplicate retries and discards old
successes/errors after task navigation or unmount. Read errors stay visible while
a retry is pending. A success clears the diagnostic. Automatic five-second polling
remains; a poll failure after a confirmed task keeps the last displayed task. On a
query-ID change, an old task or error is not displayed as the new task.

Pause/resume/archive/profile mutation code, API transport, creation behavior,
backend, authentication, runtime setup, SSE and session bootstrap are unchanged.
The diagnostic cannot repair missing runner, provider or read-service assembly.
It does not cancel or time out an already-pending read.

Focused tests execute the actual component with inert child components, router,
timers and deferred reads. Unexpected/mutation API access is forbidden. Existing
task-control contract tests retain their assertions; their source-extraction
boundary and read-feedback setter adapter are updated for this display change.
No task execution, live service, database, listener or browser is involved.

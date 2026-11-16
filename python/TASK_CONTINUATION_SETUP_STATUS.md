# Explicit zero-timeout continuation setup

NEW `enable_task_continuation(exact TaskRuntimeApplication, authorize=...)`
installs one exact owner on the existing host/runner/manager and returns that
same application with the owner. It checks original app/pump/admission/resource
bindings and original installer/inventory methods. Require no execution runs,
suspensions, deadlines, active host operation, handles or started pump. Setup
calls no provider, action grant or task-writing SQL. Duplicate setup is refused.
Concurrent use during startup installation is unsupported and detected
conservatively; a partially installed owner is never silently removed as rollback.

No route is added. Existing original JWT POST `/api/tasks/{id}/control` already
calls the shared task facade/admission controller, which reaches the explicitly
installed same-process continuation. Default absence remains unchanged. Queued,
timed, restart and terminal continuations remain unsupported.

## Important capacity limitation

The current shared admission controller counts the retained cancelled paused
handle as pending capacity. At capacity 1, HTTP resume is therefore refused before
resume SQL. No bypass or broad subtraction was added. The limited successful
HTTP fixture explicitly uses capacity 2 with one paused task. Supporting resume
at otherwise full capacity requires a separate exact same-suspension replacement
reservation, not ignoring all paused or cancelled handles.

Nine initial owner/independent tests pass, including actual existing JWT
pause→resume→new engine with unchanged prepared receipt/no goals replay, capacity1
prewrite refusal, unauthorized JWT, default absence, duplicate install, replaced
installer and a forged bundle carrying a foreign pump. The latter reproduced a
missing composition check and is now refused by original app/pump checks. Fresh
independent clearance and actual PostgreSQL helper acceptance now pass (below).

Clean paused suspensions still intentionally prevent host all_settled and pool
shutdown. This helper neither relinquishes them nor enables recovery after closing
resources. Shutdown relinquishment remains a separately reviewed protocol.

Further independent composition checks reproduced foreign facade and replaced
JWT acceptance. The helper now checks original facade/TaskService and shared
admission methods, original protected task-control handler closure, original
capacity-guard closure, and original JWT/login owner bound to the same executor.
These checks inspect code/resource identity without invoking policy callbacks.
All eleven current owner/review tests and final independent/actual acceptance
pass; evidence remains separate from the core continuation checkpoint.

## Actual database and JWT acceptance

Stage143 passes two fresh PostgreSQL runs through original JWT task control:
explicit capacity two permits fresh engine-only continuation without goals replay;
capacity one returns 503 before resume UPDATE and retains paused ownership.
Eleven focused helper tests pass. See ISOLATED_QA.md for immutable evidence.
This does not solve same-generation replacement capacity or shutdown release.

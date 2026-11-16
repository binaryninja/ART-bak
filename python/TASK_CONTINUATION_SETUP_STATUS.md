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

## Exact retained-generation capacity replacement

The admission controller still counts every retained handle, including a clean
cancelled paused generation. For resume only, it may reserve one private credit
for that exact zero-timeout suspension and its original cancelled token/handle.
The credit belongs to the current controller admission claim and thread. A
successful resume replaces the old entry rather than adding execution capacity.
Other tasks, queued starts, unknown handles and ordinary cancellations cannot
borrow the credit. Full capacity therefore permits this one-for-one replacement,
not an extra task. Concurrent or nested admission remains blocked.

The existing continuation grant and persisted paused/version/configuration checks
still run before resume SQL. Failure releases only the transient credit, retaining
any quarantined old/new generation owners. If release itself fails while an
operation is failing, admission is quarantined without masking the original error.

The startup helper remains inert and explicit. Original setup coverage includes
JWT denial, default absence, duplicate install, replaced installer, foreign pump,
foreign facade and replaced JWT rejection. New tests separately cover private
credit identity, copying, staleness, wrong task, concurrent consumption, other
occupancy, changed configuration, response loss and installation failure.

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
That is historical pre-credit evidence. The subsequent capacity-credit acceptance
below supersedes the capacity-one refusal, but not the shutdown limitation.

## Capacity-credit acceptance

Twenty-one focused setup and credit tests pass, plus fourteen admission tests
(35 total). The single-row unit fixture models extra occupancy explicitly; it
does not pretend to have admitted another real database task. Genuine PostgreSQL
acceptance and its two-task occupancy case are recorded separately in
ISOLATED_QA.md. No automatic scheduling, shutdown release, timed continuation or
restart recovery is added.

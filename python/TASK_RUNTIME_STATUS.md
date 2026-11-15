# Task/runtime checkpoint

Historical checkpoint. For current persisted-role task assembly, see
[PERSISTED_TASK_RUNTIME_STATUS.md](PERSISTED_TASK_RUNTIME_STATUS.md); for current
test counts and remaining limitations see README.md and ISOLATED_QA.md.

**373 offline authored tests pass.** This is not Go differential testing or a
production-ready backend. The route inventory declares 17 contracts against 261
Go method/path routes; 244 remain unregistered.

## Implemented and integrated

- `db.tasks.TaskStore`: parameter-bound task creation/hydration/metadata and
  lifecycle persistence. Exploration/origin/task/source/company/LLM-chain writes
  are transaction-bound. Unsupported interception-rule creation and destructive
  deletion fail explicitly before writing rather than being silently skipped.
- `manager.TaskManager`: one-process synchronized registry, public DTO whitelist,
  lossless state hydration, admission, category metadata synchronization,
  duplicate-start reservation and generation-scoped cancellation/drain gates.
  An injected runner must acknowledge all owned work stopped. A signal alone
  cannot be treated as successful cancellation. Failed launch after commit may
  leave a persisted paused task; inspect state before retrying creation.
- `server.tasks.TaskService`: the actual four Go routes for list/create/get and
  pause/resume control. No invented `/status` or `/stop` routes. The WSGI factory
  registers these with explicit manager/profile-resolver injection. Missing
  runner, metrics, bootstrap or profile prerequisites return an error, not a
  success that implies execution started.
- `providers`: OpenAI-compatible and Anthropic completion/SSE serialization,
  normalized tool/usage/refusal data and cleanup, using mandatory injected HTTP
  transport. Pool/recorder integration tests run with inert fixtures. No default
  network transport, credential discovery or tool execution exists.
- `db.logs`, `server.logs`: parameter-bound history and authenticated log snapshot
  contracts. Ring capture is explicitly supplied; no logger redirection, async
  writer or SSE listener is started. English classification retains legacy log
  markers. Nonpositive history limits deliberately normalize to 200.

## Independent review repairs

The review identified and regression-tested HEAD/GET routing, invalid UTF-8
password handling, omitted database fields causing stale cached lifecycle state,
unpinned rename failure after DB write, required empty DTO arrays, and the
pause/start reservation window. See `REVIEW_TASK_STAGE.md` for evidence and limits.

## Still missing

A production runner, scope/approval-enforced tool bindings, provider HTTP timeout
and destination policies, real metrics/archive adapter, FIFO concurrency scheduler,
timeout monitor, startup recovery, full graph stores and most application routes.
The task manager requires one shared instance; its locks do not establish
multi-process coordination. Live PostgreSQL concurrency and real driver/bcrypt/
provider interoperability remain untested. No upstream Go suite, live target,
provider request or scanner execution was performed.

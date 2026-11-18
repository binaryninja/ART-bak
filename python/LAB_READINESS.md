# Python lab readiness at the published existing-task-driver snapshot

This runbook targets commit
`2f77824b0e1a5d3724dcc795170c68cb8d4d8ce2`, tree
`a7f2dac55c05a18229debea3fbfdf6a12fcf82b1`. It describes local development
capabilities at that exact revision. Later local candidates are not included.

| Path | What is available at this pin | What a successful run establishes |
| --- | --- | --- |
| Skills workspace + prebuilt frontend | Runnable explicit Skills CLI and matching Python preview | Login and supported Skills reads/writes; no task or model execution |
| `python -m artex` | Configuration check or metadata/CRUD development API | Configuration syntax, or that separately configured API; no task pump |
| Existing `TaskRuntimeApplication` | Published bounded `drive_existing_task(...)` API and original `tick()` | Existing-task admission/advancement under the original host; completion requires fresh selected-task settlement |
| Synthetic existing-task quickstart | Published focused test with injected responses and in-memory SQL | Scripted developer acceptance only; no standalone launcher or real-model task |
| Integrated lab task launcher | **Missing at this pin** | No command in this document claims to assemble and run a task from scratch |

This pin includes `artex.agent.task_step_driver`,
[`TASK_STEP_DRIVER_STATUS.md`](TASK_STEP_DRIVER_STATUS.md) and
[`SYNTHETIC_EXISTING_TASK_QUICKSTART.md`](SYNTHETIC_EXISTING_TASK_QUICKSTART.md).
The companion guides retain historical candidate/base notes; this document binds
their now-published source to the exact revision above. The driver is an
in-process API, not a config-to-runtime factory or CLI. The dedicated task team
still owns the missing integrated real-task assembly contract.

## 1. Obtain the exact source and runtime dependencies

Use a new checkout; keep existing worktrees and lab data separate:

```sh
git clone https://github.com/binaryninja/ART-bak.git artex-lab-2f77824b
cd artex-lab-2f77824b
git checkout --detach 2f77824b0e1a5d3724dcc795170c68cb8d4d8ce2
test "$(git rev-parse HEAD)" = 2f77824b0e1a5d3724dcc795170c68cb8d4d8ce2
test "$(git rev-parse 'HEAD^{tree}')" = a7f2dac55c05a18229debea3fbfdf6a12fcf82b1
```

The commands below run from this repository root unless they enter a subshell.
The package declares Python 3.11 or later, but the checked-in
[`ci/requirements-linux.txt`](ci/requirements-linux.txt) wheel lock covers
**Linux x86_64 CPython 3.11/3.12 only**. The example selects an already-installed
CPython 3.12; substituting an already-installed 3.11 stays within that lock.
Do not treat this lock as a macOS, Windows, ARM or other-Python installation recipe.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip --isolated install \
  --disable-pip-version-check --no-cache-dir --only-binary=:all: \
  --require-hashes --index-url https://pypi.org/simple \
  -r python/ci/requirements-linux.txt
.venv/bin/python -m pip check
.venv/bin/python -c "import bcrypt,pglast,psycopg,psycopg_binary,typing_extensions,zstandard; assert psycopg.pq.__impl__ == 'binary'"
```

This installs the locked runtime/test dependencies and uses the pinned source
through `PYTHONPATH=python`; it does not install the application wheel. The lock
includes psycopg/psycopg-binary 3.2.13, bcrypt 5.0.0, pglast 8.8,
typing-extensions 4.16.0 and zstandard 0.25.0. Skills needs bcrypt; PostgreSQL
needs psycopg; pglast is syntax QA and zstandard is for archive functionality.

**Published packaging gap:** installed-wheel inspection of the earlier
`3759642b` base found that it omitted `artex/traffic/schema.sql`. The new
`2f77824b` pin leaves that packaging configuration unchanged; no newly built
`2f77824b` wheel was inspected for this document. The source checkout contains
the resource.
A separate package-data correction has independent local review, but is absent
from this published pin. This runbook does not present the published wheel as a
complete resource installation.
Also, `pyproject.toml` requests `setuptools>=68` for builds: the runtime lock does
not pin build tooling. An unconstrained `pip install ./python` or isolated wheel
build is not the same reproducibility claim as the locked dependency command.
Bind any corrected wheel recipe to its separately reviewed revision and artifact.

The separate **unpublished** packaging candidate
`0f44302be20147ff05e6e681840177dd8177f33c` (tree
`84baf2aa22195cae4559291a0ee6e2784bf1606c`) adds that existing resource and an
inert repository installation checker. Its author installed an exact wheel both
without dependencies and with the lock above, outside the source tree, on
CPython **3.12.14 / Linux x86_64 / glibc2.41**. Seven resource/module checks and
seven help/configuration checks passed in each environment. The recorded wheel
SHA-256 is `961f1a825ce400df93e3e632a22c98fe88f430779a38fc423737cc7983cc7c56`.
It was built with setuptools84.2.1 resolved from the unpinned build requirement;
rebuilding need not produce that wheel hash. No Python3.11, live database,
provider or task-readiness result follows from this installed-wheel evidence.

That packaging candidate received an independent scoped review. A later separate
sdist check verified its package-file inclusion and a wheel rebuilt from it;
neither result establishes task/runtime readiness. These artifacts were built on
the earlier `3759642b` base and do not include the newly published driver. They
are not replacement application wheels for `2f77824b`. A future combined wheel
needs its own source binding, build and installation check. The candidate's
`python/tools/check_installed_package.py` is a repository utility, not a wheel
console script and not present at the published pin. The observed verification
procedure uses the installation interpreter with `-I -B`, empty `PYTHONPATH`, an
outside-checkout working directory and the checker from that exact candidate.
Do not assume this source runbook has installed the corrected wheel or delivered
the checker; the commands below continue to use the published source checkout.

## 2. Prepare the verified Skills frontend profile

Use **Node 24.22.1**, the version selected by the published frontend workflow.
The commands here select the separately verified **local npm 11.9.0** profile;
preserved hosted evidence used npm **11.19.1**. These are recorded environments,
not a claim that every Node24.22.1 installation bundles the same npm version.
Have that runtime available before installing dependencies; no Node installer is
embedded here. Start with a fresh checkout without `web/node_modules` or generated
frontend output, as required by the lockfile preflight.

```sh
test "$(node --version)" = v24.22.1
test "$(npm --version)" = 11.9.0
ARTEX_LAB_WEB_STATE="$(mktemp -d)"
mkdir -m 700 "$ARTEX_LAB_WEB_STATE/home" "$ARTEX_LAB_WEB_STATE/tmp" "$ARTEX_LAB_WEB_STATE/cache"
: > "$ARTEX_LAB_WEB_STATE/home/npmrc"
: > "$ARTEX_LAB_WEB_STATE/home/global-npmrc"
(
  cd web
  env -i PATH="$PATH" HOME="$ARTEX_LAB_WEB_STATE/home" TMPDIR="$ARTEX_LAB_WEB_STATE/tmp" \
    node ci/verify-lockfile.mjs
  env -i PATH="$PATH" HOME="$ARTEX_LAB_WEB_STATE/home" TMPDIR="$ARTEX_LAB_WEB_STATE/tmp" \
    CI=true HUSKY=0 npm ci --ignore-scripts --no-audit --fund=false \
    --registry=https://registry.npmjs.org --cache="$ARTEX_LAB_WEB_STATE/cache" \
    --userconfig="$ARTEX_LAB_WEB_STATE/home/npmrc" \
    --globalconfig="$ARTEX_LAB_WEB_STATE/home/global-npmrc"
  env -i PATH="$PATH" HOME="$ARTEX_LAB_WEB_STATE/home" TMPDIR="$ARTEX_LAB_WEB_STATE/tmp" \
    CI=true HUSKY=0 NEXT_TELEMETRY_DISABLED=1 \
    ARTEX_PYTHON_PREVIEW=1 AUTOPENTEST_API=http://127.0.0.1:8787 npm run build
)
```

Keep this checkout, its dependency installation and `.next` build together.
Changing source or the backend port requires a matching rebuild. A default,
mock or static-export build is not this preview profile. The prebuilt launcher
checks profile/rewrite artifacts, but cannot prove source freshness from a build
with the same profile and port. See
[`web/SKILLS_PREVIEW_ACCEPTANCE.md`](../web/SKILLS_PREVIEW_ACCEPTANCE.md).

## 3. Run the Skills-only workspace

First provision the inputs described in
[`SKILLS_WORKSPACE_HOST.md`](SKILLS_WORKSPACE_HOST.md):

- An existing absolute, canonical, exclusively owned Skills directory, mode0700.
  It may be empty. Keep only intended Skills data there; skill text remains data.
- An existing absolute, current-user-owned, single-link regular password file,
  mode0600, outside that directory, with no symlink ancestors. Its UTF-8 password
  must have at least eight characters and at most 72 bytes, with at most one
  trailing LF. Supply it privately; do not put its contents in shell history.
- Free loopback ports 8787 and 3000. These commands do not attach to an existing
  server. Existing owners/locks or uncertain prior shutdown require reconciliation,
  not lock-file removal or a different bootstrap path.

Replace the two absolute paths below with those preprovisioned inputs. In terminal
A, from the repository root, start the read-only backend:

```sh
PYTHONPATH=python .venv/bin/python -m artex.skills_workspace \
  --root /absolute/private/skills \
  --password-file /absolute/private/auth/password \
  --exclusive-owner --port 8787 \
  --frontend-origin http://127.0.0.1:3000
```

In terminal B, from the same root, start the matching prebuilt frontend:

```sh
env -u NEXT_EXPORT -u NEXT_PUBLIC_MOCK -u NODE_OPTIONS \
  node web/dev/python-preview-server.mjs --frontend-port 3000 --backend-port 8787
```

Open `http://127.0.0.1:3000/system/skills`, authenticate as `ARTEX` using the
private password, and inspect the catalog/editor. Add `--enable-writes` to the
backend command only for the intended local Skills editing session. Deletion
and import are separate capabilities and are not enabled by this runbook.
This process uses no PostgreSQL, task engine, provider or tool execution.

For the independently repeatable **synthetic API/model smoke**, stop the manual
servers first and retain the matching build. The existing command generates its
own private roots/password and checks read-only and write-enabled phases:

```sh
PYTHONPATH=python .venv/bin/python python/integration/skills_preview_acceptance.py \
  --frontend-port 3000 --backend-port 8787
```

Inspect the printed evidence directory and `result.json`. Passing this smoke
means the published Node client/model, real preview proxy, Python CLI, synthetic
files and natural shutdown checks passed. **It does not mount React or test a
browser.** Keep a manual browser observation separate. On normal manual shutdown,
signal each owned frontend/backend process and observe its exit. An unresolved
backend may deliberately retain ownership; do not describe forced termination
or an uncertain write as clean completion.

## 4. Metadata CLI configuration is a separate path

The published metadata CLI reads `ARTEX_PG_DSN` and `ARTEX_SIGNING_KEY_HEX` from
its process environment. The latter must be canonical hex encoding of at least
32 bytes. Have the authorized host supply these privately; this document does
not generate credentials or print them. With those inputs already supplied:

```sh
PYTHONPATH=python .venv/bin/python -m artex --check-config
```

The success message explicitly says dependencies, database and schema were not
checked. With dependencies and a compatible existing database separately ready,
the following is the published metadata/CRUD development listener:

```sh
PYTHONPATH=python .venv/bin/python -m artex --serve --port 8787
```

Run it separately from Skills on that port. It is loopback-only, single-threaded
stdlib WSGI with no task runner, pump, provider construction, frontend hosting or
automatic resume. A successful server start is not an integrated task acceptance.
Its ordinary CLI tests mock database/server boundaries. There is no published
`--run-task`, `--provider-key-file` or task-runtime launcher option.

## 5. Prerequisites for an existing-application task integration

This section specifies inputs to an in-process integration; it is not a new
setup script. The caller must already own the assembled application and task.

| Required input | Published contract |
| --- | --- |
| Database/executor | One exact transactional executor shared by task, agent and profile stores. `PostgresExecutor` uses an idle autocommit connection and psycopg `RawCursor`; `ExecutorPool.postgres` is lazy. Caller owns lifetime and connection configuration. |
| Compatible existing schema | Packaged schema plus the components used by this host. `db.schema.bootstrap_empty_database` supports only a fresh empty database and does not provide upgrade/seed parity. It is not a repair command for an existing task. |
| Bootstrap journal | The Python bootstrap path uses separate `public.artex_task_bootstrap`, declared in `agent/bootstrap.py`; it is not in the base schema script. Any required installation is a separate provisioning task, outside this runbook. |
| Existing task and exploration | Valid persisted identities, graph state and hydrated profile chain. Task listing also reads `task_llm_profiles`, `task_relations` and `task_scope`. No synthetic ID is a substitute for an existing selected task. |
| Roles and profiles | Enabled persisted `goals`, `planner` and `worker` roles; valid authorized role-bound/active task-chain profile selection. Stored global fallback requires its original explicit opt-in. |
| Host assembly | Original `TaskRuntimeAssembly`, provider resolver, scoped durable transcript adapter, explicit role prompts, catalogs, limits, policy revision and authorization/current/lifecycle/input callbacks; signing key/password codec and bounded single-process application ownership. |
| Current execution owner | The selected task either has its original runnable handle in this application's runner, or satisfies the driver's narrowly checked fresh-existing-task admission conditions below. `load_existing()` registers records without resuming them. Initialized-task adoption after restart is explicitly unsupported. |

For an already assembled application, retain these existing resources; the driver
must not replace its executor or policy callbacks. Missing schema, profile or
ownership is a failed prerequisite. No task creation, schema installer, fresh
manager, continuation/recovery path or lease adjustment is supplied here. The
published disposable PostgreSQL acceptance fixture creates its own schema/tasks;
it is evidence for that fixture, not a way to provision the selected existing task.

### Inputs awaiting the dedicated task team's frozen contract

The task team owns assembly; this document does not design another selector or
factory. Persisted role selection already exists. Before calling a future task
integration, the operator and that contract must identify:

- The exact application/executor owner and existing selected task/exploration,
  its phase and original installed handle or eligible fresh-existing-task state,
  plus the permitted exclusive manual driving mode.
- The concrete role prompts, catalog/tool policy, limits and policy revision;
  current/lifecycle, input-validation and disclosure authorities must come from
  the authorized host, not copied always-allow test callbacks.
- The explicit durable transcript/output location and ownership, and the
  supported scoped reads for actual output, persisted goals and settlement.
- The selected stored role/profile identities, credential supplier and model;
  strict transport origins, outbound policy and response/call limits. A valid
  credential or HTTP 200 is not proof of either task fulfillment or target authority.
- Who retains and reconciles the original resources after busy, stopped,
  unresolved, failed or interrupted work; no hidden default grants or new
  bootstrap/lease choices are implied.

Exact argument names and construction steps remain pending that team's frozen
contract. No mapping from this list to an invented JSON config or CLI is supplied.

### Provider credentials and transport

`ProfileFactory` receives credentials in an explicitly supplied/persisted
`LLMProfile.api_key`; the direct adapter accepts host-supplied `Config.api_key`.
The task resolver requires the actual authorized persisted stores and profile
factory. Public DTOs/redacted key hints are not replacement credentials. Profile
saving through HTTP additionally requires an explicitly wired mutation
coordinator; the default metadata CLI does not imply this wiring.

There is **no published provider credential-file reader, API-key CLI flag or
automatic environment fallback** in this path. The Skills `--password-file` is
only its login password. If a lab keeps a provider key in a private file, reading
and supplying it is the external authorized host's responsibility; naming a file
does not configure ARTEX. Do not place keys in this checkout, Skills data, example
JSON, argv or evidence logs. No credential is provided by this runbook.

Provider construction also requires a host-supplied transport factory. The
optional `HTTPTransport` requires explicitly allowlisted origins; HTTPS checks
certificates/hostnames. It connects directly and ignores environment proxy
variables; an origin allowlist does not establish enterprise egress policy,
DNS/IP pinning or corporate-proxy support. The host must supply a compatible
reviewed transport/egress policy before any real call. Literal loopback HTTP
additionally requires its explicit opt-in. Nothing here grants a live call or
invents an origin/model. Scripted
response callables and real network transports must be reported separately.
`ProfileFactory` supports OpenAI/Anthropic formats in this path and rejects
unsupported proxy/session-header/rate-limit settings rather than enabling them.

### Published driver and synthetic quickstart

The published API is
`drive_existing_task(application, task_id, *, max_ticks=16)` from
`artex.agent.task_step_driver`. It requires the exact original application/pump,
a canonical positive int64 **string** task ID, and an integer tick budget from
1 through 1,000. The manager must already contain the selected zero-timeout task.
The caller must be its exclusive manual controller: no concurrent listener,
independent admission/tick loop, unrelated retained handles, retained timed or
continuation owners, or prior/current background pump thread.

If no handle is owned, the driver can admit only fresh, raw-created, unpaused,
unqueued metadata through the original start facade. This creates no task row.
The original bootstrap still checks prior receipts/graph eligibility on the first
tick; it can reject **after admission changed status**, retaining uncertainty.
The DTO checks do not prove that initialization is absent. Initialized tasks are
not adopted in a new application, and paused/queued/terminal tasks are not resumed.

The result exposes `task_id`, `state`, `ticks`, `advanced`, `status`, `started`
and `completed`. Only `state="completed"` makes `completed` true: the current
call must observe selected-task settlement, `done`, retired target ownership,
an empty runner and an all-settled host result. `already_terminal` is not a new
completion. Other outcomes include `tick_limit`, `busy`, `stopped`, `unresolved`,
`cancelled` and `inactive`; exceptions can follow admission without returning a
result. Keep the original application/work for reconciliation. A tick-budget
continuation is possible only through a later explicit call on the same live
owner; this is not automatic retry, crash recovery or a hard time limit.

For a runnable **synthetic developer example**, use the exact preflight and single
named-test instructions in
[`SYNTHETIC_EXISTING_TASK_QUICKSTART.md`](SYNTHETIC_EXISTING_TASK_QUICKSTART.md).
At this pin its files are published. The fixture already contains task5/origin1,
uses eight injected responses and in-memory SQL, and asserts four ticks ending
in retired ownership. Its native `record_fact`/`prove_goal` calls are scripted
graph-fixture operations. This is not the separate real-model tool-free arithmetic
proof below, a user task visible in the web UI, or a real PostgreSQL/model run.
No fixture execution is performed by reading this runbook.

### Host-wide ticks and time limits

The existing in-process API is `TaskRuntimeApplication.tick()`. It performs
binding checks and delegates to the application's pump. It is a **host-wide**
tick: up to the configured number of owned handles may advance, not just the
task being observed. `TickResult.advanced` does not itself mean completion.
The published driver checks for unrelated work and uses this host-wide API; its
per-pump lock serializes calls to that driver only. The checks are observations,
not an atomic fence against another controller. Do not call it while a listener,
thread or other loop independently drives or admits work on that runner.

A bounded count of calls is not a hard wall-clock deadline. A synchronous
provider/tool callback can block within one call; transport timeouts and
cancellation are cooperative. A polling interval also does not replace planner
heartbeat gating. Pump stop is not task cancellation or successful resource
drain. Preserve the host's actual settlement and ownership results.

The driver does not pause/cancel tasks, drain/close resources, release ownership,
sleep or retry failed effects. A completed result is not a storage-shutdown
receipt. The missing piece is the dedicated team's real-task assembly and
operator-facing integration, not another driver. Do not substitute a new
bootstrap/demo or change readiness checks to fill that gap.

## 6. Benign arithmetic/note acceptance specification

This is **inert test data and an acceptance specification**, not a task-creation
request, executable demo or recorded result. A separately authorized host must
provide the existing task/application and supported output/persistence facilities.
No shell, network-target or external file tool is required by the proposed work.

Keep three acceptance classes distinct:

1. **Synthetic fixtures:** injected fixed responses and synthetic data establish
   only the tested plumbing. No real-model or target result follows from them.
2. **Real-model TOOL-FREE local-data proof:** the proposed arithmetic/note work
   uses only its supplied numbers/text and a separately authorized real model.
   The dedicated assembly contract must establish this tool-free mode and the
   required goal/output persistence; do not silently enable task tools to make
   the example pass. This proof is still pending, not a delivered real task.
3. **Target-facing tasks:** require a separately reviewed containment and
   evidence policy, explicit target scope and corresponding host authority.
   They are outside this example; neither of the first two classes authorizes
   them or demonstrates their readiness.

| Field | Required value or observation |
| --- | --- |
| Intended work | Compute `6 * 7` and produce the note `Lab arithmetic check: 6 × 7 = 42.` |
| Expected answer | `42`; the note is the exact string above. These are expectations, not observed model output. |
| Scripted phase label | `scripted-provider`; fixed injected responses test assembly/persistence/settlement plumbing only. |
| Real phase label | `real-provider-tool-free-local-data`; requires the frozen assembly contract, separately authorized real transport/model/credential configuration and actual call evidence. A scripted pass never satisfies it. |
| Selected identity | Record the actual existing task and exploration identifiers, source tree and redacted model/profile identity. Never invent IDs or log keys. |
| Actual output | Capture the actual selected-task answer and note through the host's supported scoped transcript/output path. Compare them with the expected values; unavailable output is not a pass. |
| Persisted goals | Read back the selected exploration's persisted goals and their satisfied/evidence state through the authorized existing store/read API. Check the arithmetic/note requirements against actual output. |
| Selected-task settlement | Confirm the original host settled this task and its manager reports `done`, with no retained unresolved execution for that task. Completion of another task, a returned tick, UI status or provider stop event is insufficient. |
| Ownership after the run | Record actual cleanup/drain observations separately. A correct answer does not prove all host resources drained. |

Do not implement “save a note” by silently adding a filesystem tool. Here the note
is textual output retained by the already-supported scoped transcript/output
facility. If that facility cannot provide the note or persistence evidence, report
the missing capability and keep the result incomplete. A later external note-file
operation would need its own explicit supported host/tool scope.

Before a run, its result record should say `not_run`, with actual output, persisted
goal observations and selected-task settlement unset. Afterward, preserve failure
or uncertainty as observed. A green Skills smoke, successful dependency install,
metadata API response or synthetic fixture cannot fill these three evidence fields.

## Evidence boundary for this document

The earlier published `3759642b` thirteen-component tree has recorded successful hosted Python,
frontend and disposable-PostgreSQL workflows. Local full Python/SQL runs retained
24 known namespace-environment errors; SQL syntax capture found 528 statements
and zero syntax failures. The published Skills API/model smoke has passed both
modes with synthetic data and the real prebuilt proxy/CLI. Those results do not
establish rendered-browser acceptance, real-provider behavior or a user-run
integrated lab task. Those old counts and workflow results are not new full-suite
evidence for `2f77824b`. The new pin supplies the driver, its focused synthetic
tests and guides; use exact-revision validation records for its test outcomes.

This documentation candidate itself starts no service, provisions no database,
creates no task and calls no model. Check commands against the stated pin and
record the actual environment/results when a separately authorized lab run occurs.
The wheel package-data correction and any later UI/assembly changes need their
own exact revision/publication evidence before being described as installed here.
The published driver does not remove the missing real-task-launcher prerequisite.

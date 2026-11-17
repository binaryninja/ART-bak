# Synthetic existing-task example: developer quickstart

**This is a fixture-based developer example, not a standalone ARTEX app launch.**
It exercises one already-present synthetic task with injected model responses.
It does not create a task, start a web server, open PostgreSQL, call a real model,
or send traffic to a target. Do not substitute production data or live credentials.

## Version gate

The original guide and matching driver were prepared from base
`9e119493b06df097d77f32e5c0fa9ed3061e25f4`. That base does **not** contain
`artex.agent.task_step_driver` or its focused fixture. The frozen driver candidate
is tree `b338877130f554ee856b65ca052648bbd4732ba5`.

This guide's embedded preflight and single named example were checked on that
exact driver tree, then on an isolated union of published commit
`3759642b27e71db3de5874b4725c19ad50f0cd24`, the candidate's three unchanged driver
files and this guide. The companion verification records both source identities.
This narrow check closes the example-command version gap; it is not independent
driver review, broad regression acceptance, publication or a full-application
release. Current-head driver integration still requires its own review and gates.
File presence alone does not certify those steps.

Use the commands below only in a separate, reviewed checkout containing the
matching driver and test. Do not switch or repurpose a QA branch, overwrite local
changes, copy held fixtures, or add a task-creation/SQL-seeding workaround. The
preflight is expected to refuse an unchanged base checkout without the matching
candidate.

## 1. Check Python and the example files

Run from the repository root using Python 3.11 or newer. These shell examples are
for macOS/Linux; replace `python3` with your Python 3.11+ executable if needed.
No optional PostgreSQL, bcrypt, archive or QA dependencies are intended for this
focused synthetic fixture. Do not install those extras to conceal a wrong-version
or wrong-test failure.

This preflight checks only Python and file presence. It imports no ARTEX runtime
module, opens no listener/database and reads no credentials:

```sh
python3 -I -B - <<'PY'
import sys
from pathlib import Path

if sys.version_info < (3, 11):
    print("Python 3.11 or newer is required.", file=sys.stderr)
    raise SystemExit(2)
required = (
    Path("python/artex/agent/task_step_driver.py"),
    Path("python/tests/test_task_step_driver.py"),
)
missing = [str(path) for path in required if not path.is_file()]
if missing:
    print("Missing matching example files: " + ", ".join(missing), file=sys.stderr)
    print("Stop: obtain the reviewed driver/fixture checkout; do not create replacements.", file=sys.stderr)
    raise SystemExit(2)
print("Preflight passed: Python and required files are present.")
print("This is not review approval, runtime readiness or execution authorization.")
PY
```

## 2. Run the single synthetic example

The focused test uses an existing published in-memory SQL fixture that already
contains synthetic task ID 5, injected provider responses and temporary transcript
files. It does not call TaskStore.create or POST a task. This is not a persistent
user task and will not appear in a running web UI.

The command explicitly selects the example and refuses an empty test selection;
a zero-test success is not acceptance:

```sh
python3 -I -B - <<'PY'
import sys
import unittest
from pathlib import Path

if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11 or newer is required.")
root = Path.cwd()
required = (
    root / "python/artex/agent/task_step_driver.py",
    root / "python/tests/test_task_step_driver.py",
)
if not all(path.is_file() for path in required):
    raise SystemExit("Missing reviewed driver/fixture. Run the preflight first.")
sys.path[:0] = [str(root / "python"), str(root / "python/tests")]
name = "test_task_step_driver.TaskStepDriverTests.test_existing_task_runs_goals_planner_worker_proof_done"
suite = unittest.defaultTestLoader.loadTestsFromName(name)
if suite.countTestCases() != 1:
    raise SystemExit("Expected exactly one synthetic example test; refusing this selection.")
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
PY
```

Expected acceptance is the named test followed by `Ran 1 test` and `OK`, with exit
code 0. The selected test asserts completion in four synchronous ticks using
eight synthetic responses, retired task ownership, and no task/exploration INSERT,
socket creation or thread start during the drive. Those metrics are assertions
in the test, not extra lines printed by the command. A missing module/test,
traceback, nonzero exit or zero tests is a failure. The companion verification
records the exact tested union; the command is not evidence by itself.

For the entire focused driver test module, use the worker-documented developer
command from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=python:python/tests python3 -m unittest test_task_step_driver -v
```

Do not replace it with broad test discovery or integration suites that include
unreviewed or held scenarios.

## What the driver provides, and what is still missing

The new API is `drive_existing_task(application, task_id, *, max_ticks=16)`.
The caller must already own the exact assembled TaskRuntimeApplication and an
existing zero-timeout task registered with its manager. It must be the exclusive
manual controller: do not run it alongside listeners, independent admission/ticks
or a prior/current background pump. Its lock serializes calls to this helper only;
its observations are not an atomic fence against other controllers. A task with
no prior initialization is a required eligibility condition, not something the file
preflight or lifecycle DTO certifies. The original bootstrap's first tick must
establish absence of prior receipts/goals/intent graph. Admission can already have
changed task status before a refusal. Even with zero provider calls, that refusal
may retain uncertain ownership requiring inspection; it is not a clean
"never started" outcome. Do not retry or discard that owner automatically.

The driver uses the existing start facade and synchronous application ticks. It
is not an application factory, database initializer, provider-config wizard or
CLI launcher.

The result exposes `task_id`, `state`, `ticks`, `advanced`, `status`, `started`
and a `completed` property. Treat only verified current-call completion as success;
tick count or provider output alone is insufficient. Tick-budget exhaustion can
continue only with the same live application/task owner, not by adopting an
initialized task in a fresh process. `max_ticks` bounds iterations, not elapsed
wall time. The driver does not automatically pause tasks, close resources or
resolve an uncertain outcome. Do not turn synthetic fixture allow-callbacks into
live execution policy.

A real local application example still needs an explicitly reviewed assembly,
existing-task provenance, independent execution/target authority, provider setup,
resource ownership and shutdown handling. This guide neither supplies nor bypasses
those prerequisites. It does not authorize adoption, crash recovery or resume.

The existing `python -m artex --serve` command is a metadata/CRUD launcher without
a task runner; it is not the next step for executing this example. `--check-config`
and `artex.runtime_config_check` validate syntax only, not readiness. A separate
Skills UI guide proves its own supported scope, not task execution. No standalone
end-to-end local app/task launch is claimed here.

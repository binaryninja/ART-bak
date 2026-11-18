# Optional local PostgreSQL acceptance

`assistant_assessment_acceptance.py` creates and removes its own disposable
database on the PostgreSQL server named by `ARTEX_ACCEPTANCE_SOURCE_DSN`. It
copies the selected provider profile without its credential, configures a new
assistant through the authenticated HTTP router, runs a real tool-assisted
conversation, verifies its result through a fresh database connection, and
checks durable event delivery and shutdown. Supply `LITELLM_API_KEY` separately.

```sh
PYTHONPATH=python python python/integration/assistant_assessment_acceptance.py \
  --source-profile-id 2 --receipt /absolute/private/path/assistant-receipt.json
```

The assessment launcher exposes custom assistants through `/api/agents` and
`/api/conversations`. `servicenow_analyst` is a seeded coordinator using the
same generic interfaces. Its tools submit and read registered assessments;
the existing goals/planner/worker engine owns assessment execution. Completed
ServiceNow inventory automatically admits `servicenow.public_rest` when public
operations exist. Probe recipes are pinned to method, route, and reviewed
source hash; changed or protected handlers receive metadata-only requests.
Trigger deliveries that were in flight when a host stopped remain `uncertain`
and are not automatically replayed.

The separate `provider_fixture_acceptance.py` launcher runs one real provider
conversation with only `read_acceptance_fixture`. It requires the same empty,
disposable local database and explicit identity checks described below. Supply
an HTTPS provider base URL, model identifier, and the **name** of an environment
variable containing its API key. The stored profile contains no credential.

```sh
PYTHONPATH=python python python/integration/provider_fixture_acceptance.py \
  --host 127.0.0.1 --port 55439 --user artexqa \
  --database artex_test_provider_run \
  --confirm-disposable artex_test_provider_run \
  --base-url https://api.openai.com/v1 --model YOUR_MODEL \
  --api-key-env OPENAI_API_KEY
```

This launcher submits exactly one task through `AgentHost`/`AgentRunner`. An
admission barrier prevents provider work until the manager has installed the
original handle and token. The original `TaskManager.guard_generation` covers
the bounded conversation and synchronous result sink. PostgreSQL `llm_records`
holds the two normalized exchanges; a fresh connection verifies the linked
tool call, random result, final answer, and completed task row. The launcher
then verifies retirement, runner removal, zero pending work, drain, host closure,
and pool closure. Its private, newly created output directory contains the file
transcript and `evidence.json`, including revision/source hashes and generation
identity. A failed run retains its database and directory; never replay an
uncertain task. Repeating acceptance requires another empty database. No
autonomous graph, planner, worker, shell, or pentesting tools are installed.

This opt-in script is **outside unit-test discovery**. It creates schema and synthetic fixtures in an already-created, empty, dedicated disposable database. Never point it at real or shared data. It does not create/drop a database, launch a server, install dependencies, execute commands recorded in history, or contact a model/provider/target.

## Prerequisites

Use your own isolated disposable PostgreSQL instance and a fresh database named `artex_test_<suffix>`. PostgreSQL 17 is the tested version. The current Python package, `psycopg` with a compatible binary implementation and `bcrypt` must already be installed. Provision these separately; this runner installs nothing.

Use explicit local trust authentication for this disposable instance. Passwords/DSNs/service names are not accepted. The runner rejects ambient `PG*` environment variables and uses an empty temporary passfile. Literal `127.0.0.1`/`::1` or an owner-only mode0700, nonsymlink Unix-socket directory under the system temporary directory is required. Loopback alone does **not** make a server disposable: the operator must isolate it and exclude other clients/writers.

From the repository root, using the appropriate existing Python environment:

```sh
PYTHONPATH=python python python/integration/local_postgres_acceptance.py \
  --host 127.0.0.1 --port 5432 --user qa \
  --database artex_test_acceptance \
  --confirm-disposable artex_test_acceptance
```

The confirmation must repeat the exact database name. No private connection defaults are used. The source schema bootstrap checks the actual database identity, empty public schema and absence of other user schemas/objects/extensions, under its advisory lock, before DDL. Nonempty databases refuse execution. The script never cleans or drops database objects. After a successful or partially completed run the operator must dispose of the instance; a rerun against populated schema refuses.

## Evidence scope

- Actual optional bootstrap application, bcrypt/JWT, direct WSGI HTTP task creation and installed manager generation.
- First explicit step prepares persisted goals but does not mark the task complete; the same outer handle/token continues through planner, intent worker, harmless fixture fact/proof and manager completion.
- Real scoped token and command-history reads over synthetic rows.
- Actual PostgreSQL intercept-history joins with exact textual task keys (`007` versus `7`), plus a raw-permission revocation after SQL fetch that must hide the response.
- Read-only pool diagnostics from a real Registry with synthetic profiles and redacted errors; actual Guard ring eviction with inert audit records. No intercept decisions, live waiters, tool execution, visibility changes or pool resets are exercised.
- All model responses and policy grants are fixed in-process fixtures. Signing material/passwords are generated and not logged. Transcripts are generated in a temporary directory and removed afterward.

This is not a network-listener/browser test, production configuration, security sandbox, real model run, timed-engine acceptance, autonomous scheduler, full Go parity suite or proof of safe live grants. No default application enabling. A test failure can leave database fixtures; errors are intentionally terse and no automatic retry is attempted. Inspect only your disposable database. The environment used to run this script must itself be isolated; the script does not create an OS/network sandbox.

Assertions must be enabled. The runner refuses Python `-O`/`-OO` or
`PYTHONOPTIMIZE` before connecting or creating schema objects.

## Optional Skills preview smoke

The separate `skills_preview_acceptance.py` uses only freshly generated local
Skills fixtures and an already-built Next preview, with no PostgreSQL. See
[`web/SKILLS_PREVIEW_ACCEPTANCE.md`](../../web/SKILLS_PREVIEW_ACCEPTANCE.md) for
prerequisites, the exact command, covered paths and evidence limitations.

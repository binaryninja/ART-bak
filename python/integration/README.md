# Optional local PostgreSQL acceptance

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

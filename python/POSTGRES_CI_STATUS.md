# Disposable PostgreSQL baseline CI candidate

This candidate adds a separate, read-only GitHub-hosted PR workflow for the
**existing** `integration/local_postgres_acceptance.py` baseline. It does not
change that harness, production code, its disposable-database validators, the
Python unit workflow, release workflow or application defaults. No held demo,
execution-epoch or prompt-reader source is included or fetched.

## Scope and execution boundary

The new `python-postgres.yml` uses `pull_request` and manual dispatch only,
`contents: read`, GitHub-hosted Ubuntu 24.04 and a ten-minute job limit. It has no
repository/environment secrets, write/OIDC permissions, privileged follow-up
workflow, self-hosted runner, dependency cache, artifact upload, release, deploy
or external provider setup. Checkout credentials are not persisted. The official
checkout/setup-python action commit pins and six hash-locked wheels are the same
as the existing Python CI; dependency installation occurs in a fresh venv.

**This is not an untrusted-code sandbox.** Only the PostgreSQL container has
networking disabled. The Python baseline runs as the hosted runner user and has
the host's ordinary network and local Docker-daemon capabilities. Arbitrary PR
code could use those capabilities, and could change this workflow. Preserve
GitHub's fork-approval controls and review workflow/source changes. This design
supplies no user secrets, live providers or existing databases to that code; it
does not make malicious code safe or enforce host-wide egress denial.

The helper requires an explicit `--github-hosted-disposable` flag, assertions,
Linux x86_64, a non-root UID/GID, GitHub-hosted job markers and the normal local
Docker client. These are accidental-use guards, not cryptographic runner
attestation. There is no DSN, hostname, port, Docker context, arbitrary script,
image or patch URL input. Ambient `PG*` settings are refused. Container commands
use the fixed local Docker socket and a fresh empty Docker client configuration;
personal Docker logins, remote daemon defaults and registries are not inherited.
If the public official pull is unavailable or rate-limited, the job fails rather
than switching registries or using a credential fallback.

## Database fixture containment

The pinned Linux/amd64 Docker Official Image is PostgreSQL 17.12 Bookworm:

- Manifest: `postgres@sha256:99d1a998c76487d3abb48b141505bda5dcf8089c0c4cd7be40f75de314fa32f0`
- Image config ID: `sha256:431b070f8300b219ab7e6597e7ebb158df31767739896f53a936cfc749d8437a`
- [Official published image metadata](https://github.com/docker-library/repo-info/blob/master/repos/postgres/remote/17.12-bookworm.md)
  and [official image documentation](https://hub.docker.com/_/postgres).

The digest selects immutable content rather than resolving a mutable tag. The
helper also checks the image config ID after pull. The image's existing
entrypoint supports a caller-supplied non-root UID through its bundled
nss-wrapper initialization; no sudo, host package install or root database
process is requested.

A new owner-only directory under `/tmp` holds this attempt's empty data,
owner-only socket directory, empty passfile and private client configuration.
The socket is **beneath the child process's selected TMPDIR**, preserving the
unchanged acceptance harness's temporary-path ownership checks. The container
runs with the runner's numeric UID/GID and exactly two runner bind mounts: fresh
data at `/var/lib/postgresql/data`, and the private socket directory at
`/run/artex-ci`. Neither the repository, home, credentials nor Docker socket is
mounted into PostgreSQL.

The database container has `--network none`, no published ports, all capabilities
dropped, no-new-privileges and a read-only root. Writable tmpfs/shared memory,
process count, memory and CPU are bounded. The image's bootstrap psql uses a
separate container-only default Unix socket; the host uses the private bind.
`listen_addresses` is empty, TCP HBA rules reject connections, and the local
socket directory is owner-only with socket permissions0700. A synthetic fixed
fixture password satisfies the image initializer; it is not a user credential,
and neither client needs it. The actual Docker isolation selectors and bind
paths are inspected before starting the container.

Readiness requires the final PostgreSQL process, not the image's temporary
bootstrap server. A subsequent connection verifies exact database
`artex_test_ci_baseline`, user `qa`, Unix transport, PostgreSQL version170012 and
empty listen addresses. The unchanged harness then independently checks its
explicit connection identity and empty/disposable schema before DDL. No real or
shared database can be selected through the helper's interface.

## Test and resource ownership

The helper launches only the existing baseline script from the checked-out
revision, with explicit private socket, user, port, database and matching
confirmation. It supplies a cleaned environment without provider, libpq, token
or Docker context settings. The baseline uses fixed in-process provider fixtures,
real bcrypt/JWT, schema/bootstrap, a proof-completed task, direct WSGI calls and
scoped diagnostic reads. It does not exercise a network listener or browser.
See [integration/README.md](integration/README.md) for its exact assertions.

Image pull, startup, connections, statements, acceptance child and cleanup have
bounded waits. On timeout/interruption the helper terminates its own child
process group. Cleanup selects only the random per-attempt owner label, rejects
ambiguous identities, removes that container and its anonymous volumes, proves
it absent, then deletes only the generated private directory. It never uses
system-wide prune, drops an existing database or deletes by a caller-supplied
container name. A lost create response still enters ownership-based cleanup.
Unresolved cleanup makes the job fail and retains the private files rather than
claiming successful disposal. Host teardown remains the backstop for abrupt
runner loss, cancellation or SIGKILL that prevents the bounded cleanup finishing.

A successful job means **this baseline** and explicit cleanup succeeded. It does
not clear the held runtime-demo/epoch/prompt-reader gates. Those require their
own reviewed exact source and fixture changes in a future authorized test ref;
this workflow never downloads/applies held patches or silently skips them.

## Validation limits and next gate

Local tests for this candidate use mocked Docker/database boundaries and actual
stdlib guard subprocesses only. No local Docker daemon, PostgreSQL server,
security relaxation, protocol workaround or remote workflow was invoked during
preparation. Local restrictions remain in force.

Actual image initialization under these mount/UID/security settings and actual
GitHub-hosted PostgreSQL acceptance remain **unverified until the exact reviewed
candidate is deliberately published and its run inspected**. Passing mock tests,
SQL parsing or the already passing Python PR workflow does not establish that
result. The next gate is review of this immutable candidate, then a separately
authorized hosted run of the baseline, not a claim of restored local capability.

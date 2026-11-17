# Python pull-request checks

This candidate adds `.github/workflows/python-port.yml` without changing the
existing release workflow, runtime code, application defaults or permissions.
It is a regression gate for the authored Python tests, not replacement-server,
real-provider, browser or production acceptance.

## Existing workflow and trust boundary

At base `8285047d639cc9123e12555f939062ac3896d400`, the only existing workflow is
`release.yml`: a `v*` tag-triggered Go/frontend build, GitHub release and Docker
publication workflow. It has write permission and Docker credentials. This
candidate neither calls nor changes it.

The new workflow runs on `pull_request` and `workflow_dispatch` only, with
`contents: read` and no other token grants. It does not use `pull_request_target`,
`workflow_run`, a self-hosted runner, environments, secrets, provider configuration,
caches, artifact uploads, package publishing, deployment or repository writes.
Checkout does not persist credentials, and no submodules are fetched. The default
PR merge revision is tested rather than interpolating an untrusted branch name
into shell commands. A manual run tests its selected ref. No path filter can leave
this check pending merely because a PR changes a different directory.

Each run uses GitHub-hosted `ubuntu-24.04`, at most two matrix jobs and a ten-minute
per-job timeout. Updated runs cancel older runs for the same PR. Python 3.11 checks
the declared minimum version; Python 3.12 covers the locally exercised interpreter
family. Both intentionally use the available stable patch release for their minor
version. The hosted runner image, CPython patch and bundled pip are not immutable
artifacts; do not describe the entire environment as reproducible byte-for-byte.

Dependencies are installed in a new virtual environment, without installing the
project or running its build scripts. The six packages in
`ci/requirements-linux.txt` have exact versions and official PyPI wheel SHA256
hashes for Linux x86_64 CPython 3.11/3.12. Pip must verify hashes, accept binary
wheels only, and use the explicit official index. There is no source-build or
unhashed fallback. `pip check` and imports exercise all selected extras, including
the binary psycopg implementation and archive codec.

Test processes receive an empty environment except for the executable path,
private temporary HOME/TMPDIR, PYTHONPATH and Python I/O/user-site settings. This
prevents inherited application/provider/database configuration from silently
selecting a live resource. Existing tests use synthetic/injected providers and
local fixtures; some intentionally create loopback listeners and child processes.
The runner is **not an egress-blocking sandbox**, and this workflow cannot make
arbitrary code submitted in a PR safe. Preserve GitHub's fork approval controls
and review workflow/dependency changes before trusting them. No user credentials
or real provider endpoint is required or supplied.

## Checks and limitations

1. Full `unittest` discovery under `python/tests`, with zstandard installed so the
   optional archive test module is exercised rather than skipped.
2. `python/tools/check_sql_syntax.py`, which runs authored tests again and parses
   constructed SQL with pglast. Parsing is not PostgreSQL execution and does not
   prove schema/type/locking/transaction correctness.

This workflow does not launch PostgreSQL, run the optional disposable-database
acceptance script, run the unpublished runtime demo or epoch candidate, test Next
or a browser, execute the upstream Go suite, or prove feature parity. Actual
GitHub Actions acceptance, including the Python 3.11 runtime, remains unverified
until this candidate is deliberately published and its exact run inspected.
A local YAML parse is not an Actions run. It does not change branch protection or
make either job a required check automatically. Manual dispatch is available only
once GitHub recognizes the workflow on the default branch; fork runs can require
maintainer approval under repository policy.

## Dependency provenance and update procedure

Official action release/commit references:

- [actions/checkout v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1),
  pinned to `3d3c42e5aac5ba805825da76410c181273ba90b1`.
- [actions/setup-python v7.1.0](https://github.com/actions/setup-python/releases/tag/v7.1.0),
  pinned to `6d4fd22b20465ccc0f7354476588e8df7c9ca0e0`.

Wheel versions are bcrypt 5.0.0, pglast 8.8, psycopg/psycopg-binary 3.2.13,
typing-extensions 4.16.0 and zstandard 0.25.0. The hashes were taken from the
respective official `https://pypi.org/pypi/NAME/VERSION/json` release records,
excluding source distributions, yanked files and other architectures. The lock is
for this workflow, not a new cross-platform installation contract; existing
`pyproject.toml` ranges are unchanged.

Update pins intentionally: review upstream release changes, retain official
sources/full action commit SHAs, refresh hashes, verify wheel resolution for both
matrix versions, then rerun both full tests and SQL parsing. Do not remove
`--require-hashes` or allow source builds just to make dependency resolution pass.
The [GitHub secure-use guidance](https://docs.github.com/en/actions/reference/security/secure-use)
and [Python workflow guidance](https://docs.github.com/en/actions/tutorials/build-and-test-code/python)
describe the corresponding Actions trust and interpreter boundaries.

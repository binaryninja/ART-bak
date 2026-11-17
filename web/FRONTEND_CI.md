# Frontend PR checks

`frontend-port.yml` adds an independent Linux x64 / Node 24.22.1 CI check. It does
not modify the release workflow, publish artifacts, deploy a site, change branch
protection or make a check required. There are no path filters: unrelated PR edits
still produce a check result. It runs on `pull_request` (the normal merge revision)
and explicit `workflow_dispatch`, never `pull_request_target` or privileged
`workflow_run` chaining.

## Verified commands

1. Validate the existing npm lockfile: v3, matching root dependency ranges, only
   canonical HTTPS `registry.npmjs.org` tarballs, SHA-512 integrity on every locked
   package, and no file/git/workspace links or npm config/shrinkwrap override.
   Fresh `node_modules` is required. The guard deliberately accepts only the
   current simple exact/caret/tilde semver-range contract; new dependency forms
   require a reviewed update, not a silent fallback.
2. `npm ci --ignore-scripts --no-audit --fund=false` using the official registry,
   a fresh private job cache, empty user/global npm config and cleared environment.
   No npm lifecycle hooks, including the root Husky prepare hook, are run.
3. All Node model/transport/contract tests, including real loopback fixtures and
   the registry/lock/config guards. A no-dependency synthetic npm fixture verifies
   that `--ignore-scripts` skips root lifecycle hooks without network access.
4. Actual `next typegen`, followed by full `tsc --noEmit --incremental false`.
5. The normal production frontend build, with no Python/mock profile forced.
6. A fresh explicit Python prebuilt-preview build. Its actual serialized metadata
   must pass the production launcher's Python/non-mock/same-origin and exact
   `http://127.0.0.1:8787` rewrite checks. No backend listener is started.
7. A fresh Python-profile static export, checking the emitted Skills and Tasks
   HTML files. The preview build flag is absent for static export.

Next and TypeScript are called from the installed lockfile directly, without
`npx` downloads or automatic dependency fallback. Build output is deleted only
inside the disposable checked-out job between profiles. Telemetry is disabled.
Install scripts remain disabled; native platform packages must work as delivered
in the official lockfile. A future dependency requiring an install hook should
fail this check and receive separate review, not silently enable scripts.

## Trust and credentials

The workflow uses SHA-pinned official checkout/setup-node actions, a hosted
`ubuntu-24.04` runner, read-only `contents` permission and no secrets or registry
credentials. Checkout does not persist credentials or initialize submodules/LFS.
Setup-node automatic package-manager caching is explicitly disabled; no shared
cache or build artifact is consumed by another trust context. The installation
cache exists only within this job. Application tests/builds receive an allowlisted
clean environment rather than inherited provider/database/account configuration.

This is **not an untrusted-code or network sandbox**. PR source, tests, Next config,
build plugins and imported packages execute code on the ordinary ephemeral runner.
They can use that runner's normal network/filesystem capabilities. Disabling npm
install hooks does not make subsequent dependency imports inert. The provenance
checker is reviewed source in the PR too, not an immutable policy against a
malicious PR that rewrites the workflow/checker. Review workflow changes, keep
GitHub's fork-approval protections, and never attach secrets, a self-hosted runner,
write permissions or privileged artifact consumers to this job.

Actions are pinned to verified official refs:
- [checkout v7.0.1](https://github.com/actions/checkout/tree/3d3c42e5aac5ba805825da76410c181273ba90b1)
- [setup-node v6](https://github.com/actions/setup-node/tree/249970729cb0ef3589644e2896645e5dc5ba9c38)
- [Node v24.22.1](https://github.com/nodejs/node/tree/v24.22.1)
- [npm ci and ignore-scripts documentation](https://docs.npmjs.com/cli/v11/commands/npm-ci/)

## Evidence limits

Local replay should use the exact source tree, a fresh dependency installation and
cleared environment. A permitted offline replay from a previously verified
registry cache checks script-free install/build compatibility; it does not prove
GitHub's live registry availability or execute the hosted workflow. Actual GitHub
acceptance remains separate until this reviewed candidate is explicitly published
and its exact run is observed.

Typecheck/build/static HTML success is not rendered-browser acceptance, backend
task execution, PostgreSQL acceptance, security audit, dependency-vulnerability
clearance or repair of the existing development-mode Watchpack shutdown issue.
The repository's pre-existing Biome backlog is not silently promoted to a blocking
lint gate by this workflow; new files should not add diagnostics.

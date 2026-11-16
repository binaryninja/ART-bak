# Frontend validation inspection and first isolated attempt

## Source inspection (no repository script executed during inspection)

- Frontend root `web/`; no repository-local AGENTS.md or .npmrc found in inspected
  tree. Execution remains limited to the approved disposable QA scope.
- package-lock v3: 773 package entries including root; all772 resolved artifacts
  point to registry.npmjs.org, with integrity values. No git/file/alternate-host
  dependencies found in the lock. This is provenance inventory, not a supply-chain
  audit. Locked Next16.2.9, React19.2.7, TypeScript5.9.3, Biome2.5.1.
- Node minimum Next>=20.9; lint-staged>=20.17. Available Node24.22.1/npm11.9.0
  satisfy declared engines, but a supported engine range is not build proof.
- Scripts: dev=`next dev`, build=`next build`, build:static=`NEXT_EXPORT=1 next
  build`, start=`next start`; lint/check use Biome; format/check:fix modify source.
  No unit/browser test script or configured Jest/Vitest/Playwright suite found.
- Root prepare runs Husky. Installed-package lock metadata flags sharp0.34.5
  hasInstallScript. Native SWC/Tailwind oxide/Biome/sharp artifacts are dependencies;
  install-script suppression may need explicit reviewed platform setup later.
- `.husky/pre-commit` runs generate:presets, stages theme.ts, runs lint-staged
  rewriting checks. Do not install hooks or commit during QA. The generator reads
  CSS, invokes Biome through child_process, writes theme.ts. Its comment mentions a
  pre-push hook, but the inspected actual hook is pre-commit. Not part of build.
- Next config enables React compiler and Tailwind PostCSS. Static export disables
  image optimization; normal mode rewrites /api to AUTOPENTEST_API, defaultlocalhost8787.
  Mock mode bypasses backend but cannot prove Python integration. allowedDevOrigins
  includes any IPv4 pattern; do not start this dev server on public/LAN interfaces.
- Tracked fonts/registry.ts currently uses local packaged Geist fonts, with an
  existing temporary-fallback comment. Do not silently restore another file or
  claim visual font parity. Browser API requests and EventSource are real request
  surfaces: dev SSE defaults directly to hostname:8787 with query JWT. This conflicts
  with Python listener default same-origin policy for a :3000 page. Any later browser
  test needs explicit reviewed same-origin setup, not broadly permissive CORS.

## Minimal approved isolation sequence

The user approved original frontend build/tests after inspection in a disposable,
network-restricted environment without credentials or live targets. Earlier Go/PG
approval alone was not used as frontend approval. This does not authorize production
build deployment, arbitrary package scripts outside isolation or user data access.

1. Capture immutable web sources and hashes; separate writable disposable copy.
2. Fetch ONLY locked dependencies separately using sanitized env and empty npm config:
   `npm ci --ignore-scripts --no-audit --no-fund`. No ambient .npmrc, tokens, proxies,
   hooks or registry credentials. Official registry/integrity checks required.
3. With dependencies available, run actual original build/check scripts inside outer
   user+network namespace and bwrap PID/IPC/UTS namespace. Selected read-only runtime,
   writable disposable work/results, empty temp HOME; no repository/home credentials,
   no network routes, HUSKY=0, NEXT_TELEMETRY_DISABLED=1, npm offline and ignore-scripts.
   `npm run build`, `npm run check`; optionally `tsc --noEmit --incremental false`
   then separate static-export build, restoring fresh disposable source each mode.
   No format/fix/generator, git hooks or npm downloads during execution.
4. Treat compile, lint, static-export and browser behavior as separate results.
   Browser/HTTP integration needs additional bounded test fixture design, loopback
   binding and synthetic API records; no live provider/target. Build success alone
   proves neither API parity nor task execution correctness.

## Recorded first attempt

Immutable source manifest SHA256:
`efc63248a310aab588a014a2a54c1ddcef8b4fd474a163c91bca46e8c574a9fd`
(210 source files). Immutable source and execution logs retained as QA evidence.
No source/package/lock files in the repository were changed.

- Dependency fetch failed: registry.npmjs.org resolution EAI_AGAIN. A separate
  explicit escalated fetch with scripts disabled also failed resolution; npm11.9.0
  additionally reported `Exit handler never called`. No useful dependencies installed.
  This is an environment/dependency-access blocker, not a frontend source defect.
- Isolation worked using outer `unshare --user --map-root-user --net` plus bwrap
  without its network setup (direct bwrap --unshare-all hit NETLINK_ROUTE denial).
  Only selected runtimes/work/results mounted; private workspace/home files absent,
  namespace had no routes. No socket listener or external request started.
- Actual original `npm run build`: exit127, `next: not found`.
- Actual original `npm run check`: exit127, `biome: not found`.
- Therefore build/type/lint/static-export/browser results remain UNVERIFIED. Do not
  report these dependency failures as a successful build or actionable TS error.

Next prerequisite: permitted official-registry connectivity or a verified complete
lock-matching offline dependency cache. Reuse the captured manifest for comparable
results; don't bypass isolation, obtain credentials or change versions to force a
passing result. No hidden source-generated network access has been proven absent
by this limited inspection; network isolation remains necessary at execution time.

### Offline-cache follow-up

Read-only inspection found a20MiB local npm cache, plus the existing
trusted TypeScript-only parser installation. Compared locked SRI digests against
cache content-addressed blobs: **1 of772 locked package artifacts present and
hash-verified**, TypeScript; no found-blob hash mismatch. Copied that cache into the
disposable QA directory before any install attempt, preserving original cache.
`npm ci --offline --ignore-scripts --no-audit --no-fund` failed ENOTCACHED (zwitch2.0.4
reported first). No complete lock-matching cache is available among inspected
shared/tmp caches. Offline inventory and logs retained with the snapshot evidence.
Actual build/check commands ran, but their Next/Biome executables never started.

### Scoped network authorization recheck

After explicit user approval for official-npm-registry dependency access only,
an elevated, credential-free HEAD check to registry.npmjs.org still failed with
curl exit6, unable to resolve host. The execution environment has no supported
mechanism to enable the approved registry access. No firewall/DNS/system settings,
proxy, alternate endpoint, credentials or execution isolation were changed.
Approval is present, but the environment capability remains unavailable; dependency
fetch and actual compiler/linter execution remain blocked pending platform support
or a complete verified cache.

### Dependency-independent source syntax check

The verified TypeScript **5.9.3** parser parsed an immutable snapshot of all **191
tracked frontend source files** with supported TS/TSX/JS extensions: **152 TSX,
35 TS, and 4 JS**. Each file used its matching ScriptKind; `.mjs`/`.cjs` use JS,
`.mts`/`.cts` use TS. Result: **zero syntactic diagnostics**.

Source manifest SHA-256:
`5e26d8638b12b2269119e6e5c147c4821e79cafd7483ca1029d2d3e7c5b3fe45`.
The manifest records every relative path, byte length and content hash; snapshot
bytes were rechecked before parsing. Parser/harness hashes and machine-readable
results are retained with QA evidence. No project modules, Next configuration,
plugins or package scripts were loaded or executed. No dependency resolution,
installation or network access was performed for this check.

This establishes syntax only. It is **not** a TypeScript semantic/type check,
Next build, Biome result, runtime execution or browser validation. Untracked files,
non-JavaScript assets and project dependency availability are outside this check.
The npm dependency blocker above remains unchanged.

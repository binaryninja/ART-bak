import { verifyLockfile } from "../ci/verify-lockfile.mjs";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";

function fixture() {
  const manifest = { name: "fixture", version: "1.0.0", dependencies: { synthetic: "^1.0.0" } };
  return {
    manifest,
    lock: {
      lockfileVersion: 3,
      packages: {
        "": structuredClone(manifest),
        "node_modules/synthetic": {
          version: "1.0.0",
          resolved: "https://registry.npmjs.org/synthetic/-/synthetic-1.0.0.tgz",
          integrity: `sha512-${Buffer.alloc(64).toString("base64")}`,
        },
      },
    },
  };
}

test("actual checked-in npm graph is registry-only and SHA-512 locked", () => {
  const manifest = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
  const lock = JSON.parse(readFileSync(new URL("../package-lock.json", import.meta.url), "utf8"));
  assert.equal(verifyLockfile(manifest, lock), Object.keys(lock.packages).length - 1);
});

test("missing integrity, aliases, alternate origins and local/git links refuse", () => {
  const mutations = [
    (f) => {
      f.lock.lockfileVersion = 2;
    },
    (f) => {
      f.lock.packages[""].version = "2.0.0";
    },
    (f) => {
      f.manifest.workspaces = ["private"];
    },
    (f) => {
      f.manifest.overrides = {};
    },
    (f) => {
      f.manifest.dependencies.synthetic = "git+https://example.invalid/repo";
    },
    (f) => {
      f.manifest.dependencies.synthetic = "file:/tmp/private";
    },
    (f) => {
      f.manifest.dependencies.synthetic = "npm:other@1.0.0";
    },
    (f) => {
      delete f.lock.packages["node_modules/synthetic"].integrity;
    },
    (f) => {
      f.lock.packages["node_modules/synthetic"].integrity = "sha1-obsolete";
    },
    (f) => {
      f.lock.packages["node_modules/synthetic"].link = true;
    },
    (f) => {
      f.lock.packages["../other"] = f.lock.packages["node_modules/synthetic"];
    },
  ];
  for (const value of [
    "http://registry.npmjs.org/x.tgz",
    "https://registry.npmjs.org.evil.invalid/x.tgz",
    "https://evil.invalid/x.tgz",
    "https://user:secret@registry.npmjs.org/x.tgz",
    "https://registry.npmjs.org:444/x.tgz",
    "https://registry.npmjs.org/x.tgz?token=private",
    "https://registry.npmjs.org/x.tgz#fragment",
    "file:/tmp/x.tgz",
    "git+https://github.com/example/repo.git",
  ])
    mutations.push((f) => {
      f.lock.packages["node_modules/synthetic"].resolved = value;
    });
  for (const mutate of mutations) {
    const f = fixture();
    mutate(f);
    assert.throws(() => verifyLockfile(f.manifest, f.lock), /Official registry lockfile/);
  }
});

test("preflight refuses npm override/config/install residue without printing their contents", () => {
  const root = mkdtempSync(join(tmpdir(), "artex-ci-preflight-"));
  try {
    mkdirSync(join(root, "web/ci"), { recursive: true });
    copyFileSync(new URL("../ci/verify-lockfile.mjs", import.meta.url), join(root, "web/ci/verify-lockfile.mjs"));
    const f = fixture();
    writeFileSync(join(root, "web/package.json"), JSON.stringify(f.manifest));
    writeFileSync(join(root, "web/package-lock.json"), JSON.stringify(f.lock));
    const run = () =>
      spawnSync(process.execPath, [join(root, "web/ci/verify-lockfile.mjs")], {
        env: {},
        encoding: "utf8",
        timeout: 5000,
      });
    assert.equal(run().status, 0);
    for (const name of ["web/.npmrc", ".npmrc", "web/npm-shrinkwrap.json", "web/node_modules"]) {
      writeFileSync(join(root, name), "synthetic-private-do-not-log");
      const result = run();
      assert.equal(result.status, 1);
      assert.equal(result.stdout, "");
      assert.ok(!result.stderr.includes("synthetic-private-do-not-log"));
      rmSync(join(root, name));
    }
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("actual npm ci ignore-scripts skips root lifecycle hooks without network", () => {
  const root = mkdtempSync(join(tmpdir(), "artex-ci-npm-"));
  try {
    const home = join(root, "home");
    mkdirSync(home);
    const marker = join(root, "hook-ran");
    const script = `node -e ${JSON.stringify(`require('node:fs').appendFileSync(${JSON.stringify(marker)}, 'ran')`)}`;
    writeFileSync(
      join(root, "package.json"),
      JSON.stringify({
        name: "synthetic-ci-no-dependencies",
        version: "1.0.0",
        scripts: Object.fromEntries(
          ["preinstall", "install", "postinstall", "prepare", "postprepare"].map((name) => [name, script]),
        ),
      }),
    );
    writeFileSync(
      join(root, "package-lock.json"),
      JSON.stringify({
        name: "synthetic-ci-no-dependencies",
        version: "1.0.0",
        lockfileVersion: 3,
        requires: true,
        packages: { "": { name: "synthetic-ci-no-dependencies", version: "1.0.0", hasInstallScript: true } },
      }),
    );
    writeFileSync(join(home, "npmrc"), "");
    writeFileSync(join(home, "global-npmrc"), "");
    const npm = join(dirname(process.execPath), process.platform === "win32" ? "npm.cmd" : "npm");
    const result = spawnSync(
      npm,
      [
        "ci",
        "--offline",
        "--ignore-scripts",
        "--no-audit",
        "--fund=false",
        `--cache=${join(root, "cache")}`,
        `--userconfig=${join(home, "npmrc")}`,
        `--globalconfig=${join(home, "global-npmrc")}`,
      ],
      {
        cwd: root,
        env: { PATH: `${dirname(process.execPath)}:/usr/bin:/bin`, HOME: home, TMPDIR: root, CI: "true" },
        encoding: "utf8",
        timeout: 15000,
      },
    );
    assert.equal(result.status, 0, result.stderr);
    assert.equal(existsSync(marker), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

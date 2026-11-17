// Inert build/manifest and pre-import refusal tests. Actual Next lifecycle is separate.

import { previewBackend, previewEnvironment, validatePreviewBuild } from "../dev/python-preview-profile.mjs";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

function artifact() {
  return {
    buildId: "synthetic-build_1\n",
    required: {
      version: 1,
      config: { env: { ...previewEnvironment }, distDir: ".next", basePath: "", trailingSlash: false },
    },
    routes: {
      version: 3,
      basePath: "",
      rewrites: {
        beforeFiles: [],
        afterFiles: [
          {
            source: "/api/:path*",
            destination: "http://127.0.0.1:8787/api/:path*",
            regex: "^/api(?:/((?:[^/]+?)(?:/(?:[^/]+?))*))?(?:/)?$",
          },
        ],
        fallback: [],
      },
    },
  };
}

test("only canonical explicit unprivileged IPv4 loopback backend is accepted", () => {
  assert.equal(previewBackend("http://127.0.0.1:8787"), "http://127.0.0.1:8787");
  for (const value of [
    undefined,
    null,
    8787,
    "http://127.0.0.1:80",
    "http://127.0.0.1:65536",
    "http://127.0.0.1:08787",
    "http://localhost:8787",
    "https://127.0.0.1:8787",
    "http://[::1]:8787",
    "http://127.0.0.1:8787/",
    "http://127.0.0.1:8787@evil.test",
    "http://127.0.0.1:8787?x",
    "http://127.0.0.1:8787\n",
  ])
    assert.throws(() => previewBackend(value));
});

test("valid exact Python production artifact is accepted without opening a server", () => {
  assert.deepEqual(validatePreviewBuild(artifact(), 8787), {
    buildId: "synthetic-build_1",
    backend: "http://127.0.0.1:8787",
  });
});

test("wrong or missing build, client profile, mock and SSE settings refuse", () => {
  const changes = [
    (a) => {
      a.buildId = "";
    },
    (a) => {
      a.buildId = "../../other";
    },
    (a) => {
      a.buildId = "x\ny";
    },
    (a) => {
      a.required.version = 2;
    },
    (a) => {
      delete a.required.config.env;
    },
    (a) => {
      a.required.config.env.NEXT_PUBLIC_ARTEX_BACKEND_PROFILE = "go";
    },
    (a) => {
      a.required.config.env.NEXT_PUBLIC_MOCK = "1";
    },
    (a) => {
      a.required.config.env.NEXT_PUBLIC_SSE_BASE = "http://evil.test";
    },
    (a) => {
      a.required.config.env.extra = "unexpected";
    },
    (a) => {
      a.required.config.output = "export";
    },
    (a) => {
      a.required.config.output = "standalone";
    },
    (a) => {
      a.required.config.distDir = "other";
    },
    (a) => {
      a.required.config.basePath = "/prefix";
    },
    (a) => {
      a.required.config.trailingSlash = true;
    },
    (a) => {
      a.routes.version = 2;
    },
    (a) => {
      a.routes.basePath = "/prefix";
    },
  ];
  for (const change of changes) {
    const value = artifact();
    change(value);
    assert.throws(() => validatePreviewBuild(value, 8787));
  }
  assert.throws(() => validatePreviewBuild(artifact(), 8788));
  assert.throws(() => validatePreviewBuild(artifact(), "8787"));
});

test("only one exact serialized API rewrite, with no alternate authority or condition", () => {
  const changes = [
    (a) => {
      a.routes.rewrites.beforeFiles.push(a.routes.rewrites.afterFiles[0]);
    },
    (a) => {
      a.routes.rewrites.fallback.push(a.routes.rewrites.afterFiles[0]);
    },
    (a) => {
      a.routes.rewrites.afterFiles.push(a.routes.rewrites.afterFiles[0]);
    },
    (a) => {
      a.routes.rewrites.afterFiles = [];
    },
    (a) => {
      a.routes.rewrites.afterFiles[0].destination = "http://evil.test/api/:path*";
    },
    (a) => {
      a.routes.rewrites.afterFiles[0].source = "/:path*";
    },
    (a) => {
      a.routes.rewrites.afterFiles[0].has = [];
    },
    (a) => {
      a.routes.rewrites.afterFiles[0].missing = [];
    },
    (a) => {
      a.routes.rewrites.afterFiles[0].regex = "^.*$";
    },
  ];
  for (const change of changes) {
    const value = artifact();
    change(value);
    assert.throws(() => validatePreviewBuild(value, 8787));
  }
});

function config(env) {
  return spawnSync(
    process.execPath,
    [
      "--input-type=module",
      "-e",
      `const {default:c}=await import(${JSON.stringify(new URL("../next.config.mjs", import.meta.url).href)});console.log(JSON.stringify({env:c.env,output:c.output,rewrites:c.rewrites?await c.rewrites():[]}))`,
    ],
    { env, encoding: "utf8", timeout: 10000 },
  );
}

test("opt-in build config fixes client profile and canonical rewrite", () => {
  const result = config({
    ARTEX_PYTHON_PREVIEW: "1",
    AUTOPENTEST_API: "http://127.0.0.1:8787",
    NEXT_PUBLIC_ARTEX_BACKEND_PROFILE: "go",
    NEXT_PUBLIC_SSE_BASE: "http://elsewhere.test",
  });
  assert.equal(result.status, 0);
  assert.deepEqual(JSON.parse(result.stdout), {
    env: previewEnvironment,
    rewrites: [{ source: "/api/:path*", destination: "http://127.0.0.1:8787/api/:path*" }],
  });
});

test("incompatible preview builds reject instead of silently building mock/export assets", () => {
  for (const env of [
    {},
    { AUTOPENTEST_API: "http://evil.test:8787" },
    { AUTOPENTEST_API: "http://127.0.0.1:8787", NEXT_EXPORT: "1" },
    { AUTOPENTEST_API: "http://127.0.0.1:8787", NEXT_PUBLIC_MOCK: "1" },
  ])
    assert.notEqual(config({ ...env, ARTEX_PYTHON_PREVIEW: "1" }).status, 0);
});

test("default development, static export and mock config remain unchanged", () => {
  assert.deepEqual(JSON.parse(config({}).stdout), {
    rewrites: [{ source: "/api/:path*", destination: "http://localhost:8787/api/:path*" }],
  });
  assert.deepEqual(JSON.parse(config({ NEXT_EXPORT: "1" }).stdout), { output: "export", rewrites: [] });
  assert.deepEqual(JSON.parse(config({ NEXT_PUBLIC_MOCK: "1" }).stdout), { rewrites: [] });
});

test("CLI rejects stale, missing, malformed and symlink build metadata before importing Next", () => {
  const root = mkdtempSync(join(tmpdir(), "artex-preview-guard-"));
  try {
    mkdirSync(join(root, "dev"));
    mkdirSync(join(root, ".next"));
    mkdirSync(join(root, "node_modules/next"), { recursive: true });
    for (const name of [
      "python-preview-server.mjs",
      "python-preview-profile.mjs",
      "python-dev-server.mjs",
      "python-sse-bridge.mjs",
    ])
      copyFileSync(fileURLToPath(new URL(`../dev/${name}`, import.meta.url)), join(root, "dev", name));
    writeFileSync(
      join(root, "node_modules/next/package.json"),
      JSON.stringify({ type: "module", exports: "./index.mjs" }),
    );
    const marker = join(root, "next-imported");
    writeFileSync(
      join(root, "node_modules/next/index.mjs"),
      `import{writeFileSync}from'node:fs';writeFileSync(${JSON.stringify(marker)},'imported');throw new Error('must not import');export default()=>{};`,
    );
    const run = () =>
      spawnSync(
        process.execPath,
        [join(root, "dev/python-preview-server.mjs"), "--frontend-port", "3000", "--backend-port", "8787"],
        { env: {}, encoding: "utf8", timeout: 5000 },
      );
    const refuse = () => {
      const result = run();
      assert.equal(result.status, 1);
      assert.equal(existsSync(marker), false);
      assert.equal(result.stdout, "");
      assert.match(result.stderr, /^Python preview frontend could not start\./);
    };
    refuse();
    const value = artifact();
    value.required.config.env.NEXT_PUBLIC_MOCK = "1";
    writeFileSync(join(root, ".next/BUILD_ID"), value.buildId);
    writeFileSync(join(root, ".next/required-server-files.json"), JSON.stringify(value.required));
    writeFileSync(join(root, ".next/routes-manifest.json"), JSON.stringify(value.routes));
    refuse();
    writeFileSync(join(root, ".next/required-server-files.json"), "{");
    refuse();
    rmSync(join(root, ".next/required-server-files.json"));
    writeFileSync(join(root, "other.json"), JSON.stringify(artifact().required));
    symlinkSync(join(root, "other.json"), join(root, ".next/required-server-files.json"));
    refuse();
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

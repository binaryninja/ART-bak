import { ApiError } from "../src/lib/mutation-contract.mjs";
import {
  confirmedSkillContent,
  createSkillWorkspace,
  skillFileKey,
  skillResourcePath,
} from "../src/lib/skill-workspace.mjs";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { stripTypeScriptTypes } from "node:module";
import test from "node:test";
import vm from "node:vm";

const view = await readFile(new URL("../src/app/(main)/system/skills/page.tsx", import.meta.url), "utf8");
const flush = async () => {
  for (let i = 0; i < 12; i++) await Promise.resolve();
};
function deferred() {
  let resolve, reject;
  const promise = new Promise((a, b) => {
    resolve = a;
    reject = b;
  });
  return { promise, resolve, reject };
}
function harness(options) {
  const calls = {};
  const api = Object.fromEntries(
    [
      "readSkillFile",
      "writeSkillFile",
      "createSkillDir",
      "createSkill",
      "deleteSkillPath",
      "importSkill",
      "skillWorkspaceProfile",
    ].map((method) => {
      calls[method] = [];
      return [
        method,
        (...args) => {
          const request = { ...deferred(), args };
          calls[method].push(request);
          return request.promise;
        },
      ];
    }),
  );
  const model = createSkillWorkspace(api, options);
  const get = (skill = "alpha", path = "SKILL.md") => model.getSnapshot().drafts.get(skillFileKey(skill, path));
  const load = async (skill = "alpha", path = "SKILL.md", content = "cached") => {
    model.read(skill, path);
    await flush();
    calls.readSkillFile.at(-1).resolve(content);
    await flush();
  };
  const reload = () => model.read("alpha", "SKILL.md", { reload: true });
  return { model, calls, get, load, reload };
}

test("an explicit clean-file reload replaces cached content and baseline without writing", async () => {
  const h = harness();
  await h.load();
  h.model.read("alpha", "SKILL.md");
  await flush();
  assert.equal(h.calls.readSkillFile.length, 1, "ordinary navigation still uses the cache");
  h.reload();
  assert.equal(h.get().loading, true);
  assert.equal(h.get().loaded, true);
  assert.equal(h.get().content, "cached");
  await flush();
  assert.equal(h.calls.readSkillFile.length, 2);
  h.calls.readSkillFile[1].resolve("host changed");
  await flush();
  assert.equal(h.get().content, "host changed");
  assert.equal(h.get().baseline, "host changed");
  assert.equal(h.get().dirty, false);
  assert.equal(h.get().loading, false);
  for (const [method, requests] of Object.entries(h.calls)) {
    if (method !== "readSkillFile") assert.equal(requests.length, 0);
  }
});

test("reload uses the exact Unicode resource and leaves other drafts alone", async () => {
  const h = harness();
  await h.load();
  await h.load("技能", "资料/a b.md", "old");
  h.model.edit("alpha", "SKILL.md", "unsaved");
  h.model.read("技能", "资料/a b.md", { reload: true });
  await flush();
  assert.deepEqual(h.calls.readSkillFile.at(-1).args, ["技能", "资料/a b.md"]);
  h.calls.readSkillFile.at(-1).resolve("新");
  await flush();
  assert.equal(h.get("技能", "资料/a b.md").content, "新");
  assert.equal(h.get().content, "unsaved");
});

test("dirty and unloaded drafts reject explicit reload without a request", async () => {
  const h = harness();
  h.reload();
  await flush();
  assert.equal(h.calls.readSkillFile.length, 0);
  await h.load();
  h.model.edit("alpha", "SKILL.md", "unsaved");
  h.reload();
  await flush();
  assert.equal(h.calls.readSkillFile.length, 1);
  assert.equal(h.get().content, "unsaved");
});

test("same-turn duplicate reloads dispatch once and do not own the active cleanup", async () => {
  const h = harness();
  await h.load();
  h.reload();
  const duplicateCleanup = h.reload();
  duplicateCleanup();
  await flush();
  assert.equal(h.calls.readSkillFile.length, 2);
  assert.equal(h.get().loading, true);
  h.calls.readSkillFile[1].resolve("fresh");
  await flush();
  assert.equal(h.get().content, "fresh");
});

for (const outcome of ["success", "failure", "invalid"]) {
  test(`an edit retires reload immediately and ignores late ${outcome}`, async () => {
    const h = harness();
    await h.load();
    h.reload();
    await flush();
    h.model.edit("alpha", "SKILL.md", "new local text");
    assert.equal(h.get().loading, false);
    if (outcome === "failure") h.calls.readSkillFile[1].reject(Error("stale failure"));
    else h.calls.readSkillFile[1].resolve(outcome === "invalid" ? 12 : "stale host text");
    await flush();
    assert.equal(h.get().content, "new local text");
    assert.equal(h.get().baseline, "cached");
    assert.equal(h.get().dirty, true);
    assert.equal(h.get().error, null);
  });
}

test("editing back to the old baseline still cannot revive an earlier reload", async () => {
  const h = harness();
  await h.load();
  h.reload();
  await flush();
  h.model.edit("alpha", "SKILL.md", "intervening");
  h.model.edit("alpha", "SKILL.md", "cached");
  h.calls.readSkillFile[1].resolve("late");
  await flush();
  assert.equal(h.get().content, "cached");
  assert.equal(h.get().dirty, false);
  h.reload();
  await flush();
  assert.equal(h.calls.readSkillFile.length, 3);
});

for (const value of [Error("host unavailable"), undefined, { content: "wrong shape" }]) {
  test(`reload failure preserves cached content and allows explicit retry: ${String(value)}`, async () => {
    const h = harness();
    await h.load();
    h.reload();
    await flush();
    if (value instanceof Error) h.calls.readSkillFile[1].reject(value);
    else h.calls.readSkillFile[1].resolve(value);
    await flush();
    assert.equal(h.get().content, "cached");
    assert.equal(h.get().baseline, "cached");
    assert.equal(h.get().loaded, true);
    assert.equal(h.get().loading, false);
    assert.ok(h.get().error);
    h.reload();
    assert.equal(h.get().error, null);
    await flush();
    h.calls.readSkillFile[2].resolve("");
    await flush();
    assert.equal(h.get().content, "");
    assert.equal(h.get().dirty, false);
  });
}

test("cleanup retires only its own reload and late completion cannot replace a newer reload", async () => {
  const h = harness();
  await h.load();
  const oldCleanup = h.reload();
  await flush();
  oldCleanup();
  assert.equal(h.get().content, "cached");
  assert.equal(h.get().loading, false);
  h.reload();
  await flush();
  oldCleanup();
  h.calls.readSkillFile[1].reject(Error("retired"));
  await flush();
  assert.equal(h.get().loading, true);
  assert.equal(h.get().error, null);
  h.calls.readSkillFile[2].resolve("new owner");
  await flush();
  assert.equal(h.get().content, "new owner");
});

test("cleanup before dispatch does not issue an unnecessary request", async () => {
  const h = harness();
  await h.load();
  h.reload()();
  await flush();
  assert.equal(h.calls.readSkillFile.length, 1);
  assert.equal(h.get().content, "cached");
  assert.equal(h.get().loading, false);
});

for (const method of ["createSkill", "createSkillDir", "deleteSkillPath", "importSkill"]) {
  test(`reload waits for pending ${method} without changing its ownership`, async () => {
    const h = harness();
    await h.load();
    let pending;
    if (method === "createSkill") pending = h.model.createSkill({ name: "new-skill", description: "New" });
    else if (method === "createSkillDir") pending = h.model.create("alpha", "folder", "dir");
    else if (method === "deleteSkillPath") pending = h.model.remove("beta", "notes.md");
    else pending = h.model.importArchive(new File(["zip"], "test.zip"));
    h.reload();
    await flush();
    assert.equal(h.calls.readSkillFile.length, 1);
    assert.equal(h.calls[method].length, 1);
    h.calls[method][0].reject(new ApiError(403, { error: "refused" }, "refused"));
    await pending;
    h.reload();
    await flush();
    assert.equal(h.calls.readSkillFile.length, 2);
  });
}

test("a write dispatched after reload retires its read and keeps the acknowledged baseline", async () => {
  const h = harness();
  await h.load();
  h.reload();
  await flush();
  const pending = h.model.create("alpha", "SKILL.md", "file");
  h.calls.writeSkillFile[0].resolve({ ok: true });
  assert.equal(await pending, true);
  h.calls.readSkillFile[1].resolve("before write");
  await flush();
  assert.equal(h.get().content, "");
  assert.equal(h.get().baseline, "");
});

test("confirmed deletion retires reload and deleted drafts cannot be reloaded", async () => {
  const h = harness();
  await h.load();
  h.reload();
  await flush();
  const pending = h.model.remove("alpha", "SKILL.md");
  h.calls.deleteSkillPath[0].resolve({ deleted: "SKILL.md" });
  assert.equal(await pending, true);
  h.calls.readSkillFile[1].resolve("late deleted content");
  await flush();
  h.reload();
  await flush();
  assert.equal(h.calls.readSkillFile.length, 2);
  assert.equal(h.get().deleted, true);
  assert.equal(h.get().content, "cached");
});

test("read-only host mode permits reload without enabling mutations", async () => {
  const h = harness({ requireProfile: true });
  h.model.readProfile();
  await flush();
  h.calls.skillWorkspaceProfile[0].resolve({ profile: "skills-workspace", writes_enabled: false });
  await flush();
  await h.load();
  h.reload();
  await flush();
  h.calls.readSkillFile[1].resolve("readonly host");
  await flush();
  assert.equal(h.get().content, "readonly host");
  assert.equal(h.model.getSnapshot().writeModeEnabled, false);
});

for (const kind of ["file", "root", "delete", "import"]) {
  test(`successful reload does not reconcile ${kind} uncertainty`, async () => {
    const h = harness();
    await h.load();
    let pending, method, latch;
    if (kind === "file") {
      pending = h.model.create("alpha", "other", "file");
      method = "writeSkillFile";
      latch = "uncertain";
    } else if (kind === "root") {
      pending = h.model.createSkill({ name: "new-skill", description: "New" });
      method = "createSkill";
      latch = "rootUncertain";
    } else if (kind === "delete") {
      pending = h.model.remove("alpha", "other");
      method = "deleteSkillPath";
      latch = "deletionUncertain";
    } else {
      pending = h.model.importArchive(new File(["zip"], "test.zip"));
      method = "importSkill";
      latch = "importUncertain";
    }
    h.calls[method][0].reject(new ApiError(503, { unknown: true, retry: false }, "unconfirmed"));
    await pending;
    const before = h.model.getSnapshot()[latch];
    h.reload();
    await flush();
    assert.equal(h.calls.readSkillFile.length, 2);
    h.calls.readSkillFile[1].resolve("observed host");
    await flush();
    assert.deepEqual(h.model.getSnapshot()[latch], before);
    assert.equal(await h.model.create("alpha", "blocked", "dir"), false);
  });
}

function callback(source, start, end) {
  const from = source.indexOf(start),
    to = source.indexOf(end, from + start.length);
  assert.ok(from >= 0 && to > from, `missing callback ${start}`);
  return source.slice(from, to);
}
function pageCallbacks(model) {
  const selected = { skill: "alpha", path: "SKILL.md" };
  const context = vm.createContext({
    selected,
    selectionRef: { current: selected },
    alive: { current: true },
    fileReloadCleanup: { current: null },
    workspace: model,
    skillFileKey,
    setSelectedState() {
      /* Inert React state sink; selectionRef supplies the tested identity. */
    },
  });
  vm.runInContext(
    stripTypeScriptTypes(
      callback(view, "  function reloadFile()", "  async function saveFile()") +
        callback(view, "  function setSelected(", "  const catalogRequest"),
    ),
    context,
  );
  return context;
}

test("actual page reload callback synchronously suppresses duplicate clicks and stale selections", async () => {
  const h = harness();
  await h.load();
  const page = pageCallbacks(h.model);
  page.reloadFile();
  page.reloadFile();
  await flush();
  assert.equal(h.calls.readSkillFile.length, 2);
  assert.equal(h.get().loading, true);
  page.setSelected({ skill: "beta", path: "other.md" });
  assert.equal(h.get().loading, false);
  page.reloadFile();
  h.calls.readSkillFile[1].resolve("late navigation");
  await flush();
  assert.equal(h.calls.readSkillFile.length, 2);
  assert.equal(h.get().content, "cached");
});

test("actual page keeps cached editor visible on reload failure and retires reload on unmount", () => {
  assert.match(view, /fileDraft\?\.loaded \|\| \(!fileDraft\?\.error && !fileLoading\)/u);
  assert.match(view, /Reload failed; showing cached text/u);
  assert.match(view, /!fileDraft\.loaded && <Button[^\n]+Retry read/u);
  assert.match(
    view,
    /React\.useEffect\(\(\) => \(\) => \{\s+fileReloadCleanup\.current\?\.\(\);\s+fileReloadCleanup\.current = null;/u,
  );
  assert.match(
    view,
    /title="Read this file again without discarding unsaved text\. This does not reconcile unconfirmed writes\."/u,
  );
});

test("actual file API requests an authenticated uncached read using the exact resource", async () => {
  const source = await readFile(new URL("../src/lib/api.ts", import.meta.url), "utf8");
  const calls = [];
  const context = vm.createContext({
    MOCK: false,
    getToken: () => "synthetic-token",
    ApiError,
    skillResourcePath,
    confirmedSkillContent,
    fetch: async (...args) => {
      calls.push(args);
      return { status: 200, ok: true, json: async () => ({ file: "资料/a b.md", content: "new host text" }) };
    },
  });
  const http = callback(source, "export async function http<", "\n// sseUrl").replace("export async", "async");
  const method = callback(source, "  readSkillFile:", "  writeSkillFile:");
  vm.runInContext(stripTypeScriptTypes(`${http}\nglobalThis.api = {${method}};`), context);
  assert.equal(await context.api.readSkillFile("技能", "资料/a b.md"), "new host text");
  assert.equal(calls[0][0], "/api/skills/%E6%8A%80%E8%83%BD/files/%E8%B5%84%E6%96%99/a%20b.md");
  assert.equal(calls[0][1].cache, "no-store");
  assert.equal(calls[0][1].headers.Authorization, "Bearer synthetic-token");
  assert.equal(calls.length, 1);
});

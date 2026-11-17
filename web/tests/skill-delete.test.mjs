import { ApiError } from "../src/lib/mutation-contract.mjs";
import {
  confirmedSkillDeletion,
  confirmedSkillWorkspaceProfile,
  createSkillWorkspace,
  skillDeletionPath,
  skillFileKey,
  skillResourcePath,
} from "../src/lib/skill-workspace.mjs";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { stripTypeScriptTypes } from "node:module";
import test from "node:test";
import vm from "node:vm";

const apiSource = await readFile(new URL("../src/lib/api.ts", import.meta.url), "utf8");
const view = await readFile(new URL("../src/app/(main)/system/skills/page.tsx", import.meta.url), "utf8");
const mock = await readFile(new URL("../src/lib/mock/handler.ts", import.meta.url), "utf8");
const profile = (deletes = true) => ({ profile: "skills-workspace", writes_enabled: true, deletes_enabled: deletes });
function section(source, start, end) {
  const a = source.indexOf(start),
    b = source.indexOf(end, a + start.length);
  assert(a >= 0 && b > a, start);
  return source.slice(a, b);
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((a, b) => {
    resolve = a;
    reject = b;
  });
  return { promise, resolve, reject };
}
async function flush() {
  for (let n = 0; n < 15; n++) await Promise.resolve();
}
function harness(required = true) {
  const deletes = [],
    writes = [],
    dirs = [],
    creates = [],
    reads = [],
    profiles = [];
  const api = Object.fromEntries(
    [
      ["deleteSkillPath", deletes],
      ["writeSkillFile", writes],
      ["createSkillDir", dirs],
      ["createSkill", creates],
      ["readSkillFile", reads],
      ["skillWorkspaceProfile", profiles],
    ].map(([name, calls]) => [
      name,
      (...args) => {
        const d = deferred();
        calls.push({ args, ...d });
        return d.promise;
      },
    ]),
  );
  const model = createSkillWorkspace(api, { requireProfile: required });
  return {
    model,
    deletes,
    writes,
    dirs,
    creates,
    reads,
    profiles,
    draft: (path = "x", skill = "alpha") => model.getSnapshot().drafts.get(skillFileKey(skill, path)),
    confirm: async (value = profile()) => {
      model.readProfile();
      await flush();
      profiles.at(-1).resolve(value);
      await flush();
    },
    load: async (path = "x", skill = "alpha", content = "saved text") => {
      model.read(skill, path);
      await flush();
      reads.at(-1).resolve(content);
      await flush();
    },
  };
}
function apiHarness(result, fixture = false) {
  const calls = [];
  const context = vm.createContext({
    ApiError,
    confirmedSkillDeletion,
    skillDeletionPath,
    skillResourcePath,
    MOCK: fixture,
    getToken: () => "synthetic-token",
    fetch: async (...args) => {
      calls.push(args);
      if (result instanceof Error) throw result;
      return result;
    },
  });
  const mockBody = section(mock, '  if (path === "/skills/missing")', "  // ── visibility");
  const source =
    section(apiSource, "export async function http<", "\n// sseUrl").replace("export async", "async") +
    `\nfunction mockHandle(method, path) {const m=method,seg=path.slice(1).split('/'),b={};${mockBody}}` +
    `\nglobalThis.api={${section(apiSource, "  deleteSkillPath:", "  skillUsage:")}};`;
  vm.runInContext(stripTypeScriptTypes(source), context);
  return { api: context.api, calls };
}
const response = (value, status = 200) => ({ status, ok: status >= 200 && status < 300, json: async () => value });

test("separate deletion hint is strict, backwards compatible and never inferred from writes", () => {
  const legacy = { profile: "skills-workspace", writes_enabled: true };
  assert.deepEqual(confirmedSkillWorkspaceProfile(legacy), legacy);
  for (const value of [profile(), profile(false), { ...profile(false), writes_enabled: false }])
    assert.deepEqual(confirmedSkillWorkspaceProfile(value), value);
  for (const value of [
    { ...profile(), writes_enabled: false },
    { ...profile(), deletes_enabled: 1 },
    { ...profile(), deletes_enabled: "true" },
    { ...profile(), deletes_enabled: null },
    { ...profile(), authority: true },
    { profile: "skills-workspace", deletes_enabled: true },
  ])
    assert.throws(() => confirmedSkillWorkspaceProfile(value));
});
test("exact deletion receipt rejects wrong paths, unconfirmed flags and generic successes", () => {
  assert.deepEqual(confirmedSkillDeletion({ deleted: "目录/a b.txt" }, "目录/a b.txt"), { deleted: "目录/a b.txt" });
  for (const value of [
    null,
    [],
    {},
    { ok: true },
    { deleted: "other" },
    ...["pending", "unknown", "outcome", "retry"].map((key) => ({ deleted: "x", [key]: false })),
  ]) {
    assert.throws(
      () => confirmedSkillDeletion(value, "x"),
      (error) => error instanceof ApiError && error.unknown && error.retry === false,
    );
  }
});
test("actual API authenticates a bodyless/queryless encoded DELETE and validates HTTP200 plus identity", async () => {
  const h = apiHarness(response({ deleted: "目录/a b.txt" }));
  assert.equal((await h.api.deleteSkillPath("技能", "目录/a b.txt")).deleted, "目录/a b.txt");
  const [url, init] = h.calls[0];
  assert.equal(url, "/api/skills/%E6%8A%80%E8%83%BD/files/%E7%9B%AE%E5%BD%95/a%20b.txt");
  assert.equal(init.method, "DELETE");
  assert.equal(init.body, undefined);
  assert.equal(init.headers.Authorization, "Bearer synthetic-token");
  assert.equal(init.headers["Content-Type"], undefined);
  for (const status of [201, 202, 204])
    await assert.rejects(apiHarness(response({ deleted: "x" }, status)).api.deleteSkillPath("alpha", "x"));
  for (const path of ["../x", "/x", "a//b", "x?q=1", "x#part", "x%2Fy"])
    await assert.rejects(h.api.deleteSkillPath("alpha", path));
  assert.equal(h.calls.length, 1);
});
test("actual MOCK deep-path receipt preserves Unicode/space identity and sends no fetch", async () => {
  const h = apiHarness(null, true);
  assert.equal((await h.api.deleteSkillPath("技能", "目录/a b.txt")).deleted, "目录/a b.txt");
  assert.equal(h.calls.length, 0);
});
test("Python initial/loading/legacy/false/error modes never dispatch a deletion", async () => {
  const h = harness();
  assert.equal(await h.model.remove("alpha", "x"), false);
  const old = { profile: "skills-workspace", writes_enabled: true };
  for (const value of [old, profile(false), { ...profile(), writes_enabled: false }]) {
    await h.confirm(value);
    assert.equal(await h.model.remove("alpha", "x"), false);
  }
  h.model.readProfile();
  await flush();
  assert.equal(await h.model.remove("alpha", "x"), false);
  h.profiles.at(-1).reject(new ApiError(404, {}, "unsupported"));
  await flush();
  assert.equal(await h.model.remove("alpha", "x"), false);
  assert.equal(h.deletes.length, 0);
});
test("latest mode owner alone enables deletion; cleanup/recheck closes the synchronous gate", async () => {
  const h = harness();
  h.model.readProfile();
  await flush();
  const old = h.profiles[0];
  const cleanup = h.model.readProfile();
  await flush();
  old.resolve(profile());
  await flush();
  assert.equal(h.model.getSnapshot().deleteModeEnabled, false);
  h.profiles[1].resolve(profile());
  await flush();
  assert.equal(h.model.getSnapshot().deleteModeEnabled, true);
  cleanup(); // settled cleanup cannot retire a future mode owner
  const retire = h.model.readProfile();
  await flush();
  retire();
  h.profiles[2].resolve(profile());
  await flush();
  assert.equal(h.model.getSnapshot().deleteModeEnabled, false);
});
test("Python refuses required SKILL.md and unsafe paths without API dispatch", async () => {
  const h = harness();
  await h.confirm();
  for (const path of ["SKILL.md", "skill.md", "sKiLl.MD", "ſkill.md", "..", "", "x/../y", "x\\y"])
    assert.equal(await h.model.remove("alpha", path), false);
  assert.equal(h.deletes.length, 0);
  const nested = h.model.remove("alpha", "docs/SKILL.md");
  assert.equal(h.deletes.length, 1);
  h.deletes[0].resolve({ deleted: "docs/SKILL.md" });
  assert.equal(await nested, true);
});
test("immediate delete fence blocks repeats and all other new mutation families across skills", async () => {
  const h = harness();
  await h.confirm();
  await h.load("x");
  await h.load("y", "beta");
  const p = h.model.remove("alpha", "x");
  h.model.edit("beta", "y", "new draft");
  assert.equal(await h.model.remove("beta", "z"), false);
  assert.equal(await h.model.save("beta", "y"), false);
  assert.equal(await h.model.create("beta", "z", "file"), false);
  assert.equal(await h.model.create("beta", "folder", "dir"), false);
  assert.equal(await h.model.createSkill({ name: "gamma", description: "new" }), null);
  assert.equal(h.deletes.length, 1);
  assert.equal(h.writes.length + h.dirs.length + h.creates.length, 0);
  h.deletes[0].resolve({ deleted: "x" });
  assert.equal(await p, true);
  assert.equal(h.model.getSnapshot().deletionPending, null);
  assert.equal(h.draft("y", "beta").content, "new draft");
});
test("save/mkdir/root-create first all prevent deletion even on a different skill", async () => {
  for (const kind of ["save", "dir", "root"]) {
    const h = harness();
    await h.confirm();
    await h.load();
    h.model.edit("alpha", "x", "edit");
    const actions = {
      save: () => h.model.save("alpha", "x"),
      dir: () => h.model.create("alpha", "d", "dir"),
      root: () => h.model.createSkill({ name: "gamma", description: "new" }),
    };
    const receipts = { save: { ok: true }, dir: { dir: "d" }, root: { name: "gamma" } };
    const lists = { save: h.writes, dir: h.dirs, root: h.creates };
    const p = actions[kind]();
    assert.equal(await h.model.remove("beta", "y"), false);
    assert.equal(h.deletes.length, 0);
    lists[kind][0].resolve(receipts[kind]);
    await p;
  }
});
test("dirty exact/descendant drafts prevent dispatch but sibling prefixes and other skills do not", async () => {
  const h = harness();
  await h.confirm();
  await h.load("folder/child");
  h.model.edit("alpha", "folder/child", "draft");
  assert.equal(await h.model.remove("alpha", "folder"), false);
  assert.equal(await h.model.remove("alpha", "folder/child"), false);
  assert.equal(h.deletes.length, 0);
  const p = h.model.remove("alpha", "fold");
  h.deletes[0].resolve({ deleted: "fold" });
  assert.equal(await p, true);
  const q = h.model.remove("beta", "folder");
  h.deletes[1].resolve({ deleted: "folder" });
  assert.equal(await q, true);
  assert.equal(h.draft("folder/child").content, "draft");
});
test("acknowledged removal retains later edits, tombstones exact subtree and retires late reads", async () => {
  const h = harness(false);
  await h.load("folder/old");
  h.model.read("alpha", "folder/late");
  await flush();
  const p = h.model.remove("alpha", "folder");
  h.model.edit("alpha", "folder/old", "newer text");
  h.deletes[0].resolve({ deleted: "folder" });
  assert.equal(await p, true);
  h.reads[1].resolve("stale read");
  await flush();
  assert.equal(h.draft("folder/old").content, "newer text");
  assert.equal(h.draft("folder/old").dirty, true);
  assert.equal(h.draft("folder/old").deleted, true);
  assert.equal(h.draft("folder/late").content, "");
  assert.equal(h.draft("folder/late").deleted, true);
  assert.equal(await h.model.save("alpha", "folder/old"), false);
  assert.equal(await h.model.create("alpha", "folder/new", "file"), false);
  assert.equal(await h.model.create("alpha", "folder/dir", "dir"), false);
  assert.equal(await h.model.remove("alpha", "folder"), false);
  h.model.read("alpha", "folder/new");
  await flush();
  assert.equal(h.reads.length, 2);
  assert.equal(h.draft("folder/new").deleted, true);
  assert.equal(h.draft("folder/new").loaded, true);
  h.model.edit("alpha", "folder/new", "local notes only");
  assert.equal(h.draft("folder/new").content, "local notes only");
  assert.equal(h.model.getSnapshot().deletionUncertain, null);
});
test("known 400/401/403/404/409 refusals retain read ownership/drafts and allow deliberate later actions", async () => {
  for (const status of [400, 401, 403, 404, 409]) {
    const h = harness();
    await h.confirm();
    h.model.read("alpha", "x");
    await flush();
    const p = h.model.remove("alpha", "x");
    h.deletes[0].reject(new ApiError(status, {}, "refused"));
    assert.equal(await p, false);
    h.reads[0].resolve("unchanged");
    await flush();
    assert.equal(h.draft().content, "unchanged");
    assert.equal(h.draft().loaded, true);
    assert.equal(h.model.getSnapshot().deletionUncertain, null);
    assert.equal(h.model.getSnapshot().deletedPaths.size, 0);
    const q = h.model.remove("alpha", "y");
    h.deletes[1].reject(new ApiError(403, {}, "refused"));
    await q;
  }
});
test("unknown receipt, lost response and 5xx latch the whole workspace across mode refresh without retry", async () => {
  for (const error of [
    null,
    new ApiError(0, { unknown: true }, "lost"),
    new ApiError(500, {}, "ambiguous"),
    new ApiError(403, { retry: false }, "unknown"),
  ]) {
    const h = harness();
    await h.confirm();
    await h.load();
    const p = h.model.remove("alpha", "x");
    h.model.edit("alpha", "x", "keep");
    if (error) h.deletes[0].reject(error);
    else h.deletes[0].resolve({ deleted: "wrong" });
    assert.equal(await p, false);
    assert(h.model.getSnapshot().deletionUncertain);
    await h.confirm(profile(false));
    await h.confirm(profile());
    assert.equal(await h.model.remove("beta", "y"), false);
    assert.equal(await h.model.save("alpha", "x"), false);
    assert.equal(await h.model.create("beta", "y", "file"), false);
    assert.equal(await h.model.create("beta", "d", "dir"), false);
    assert.equal(await h.model.createSkill({ name: "gamma", description: "new" }), null);
    assert.equal(h.deletes.length, 1);
    assert.equal(h.draft().content, "keep");
    assert.equal(h.model.getSnapshot().deletedPaths.size, 0);
  }
});
test("actual transport classifies failed/truncated responses conservatively without retry", async () => {
  for (const result of [
    new Error("network"),
    {
      ...response({}),
      json: async () => {
        throw new Error("truncated");
      },
    },
    response({ deleted: "x" }, 202),
  ]) {
    const h = apiHarness(result);
    await assert.rejects(
      h.api.deleteSkillPath("alpha", "x"),
      (e) => e instanceof ApiError && e.unknown && e.retry === false,
    );
    assert.equal(h.calls.length, 1);
  }
});
function pageHarness() {
  const h = harness();
  const p = { kind: "file", skill: "alpha", path: "x" };
  const state = { dialog: p, busy: false, loads: 0, selection: { skill: "alpha", path: "x" }, notices: [] };
  const context = vm.createContext({
    workspace: h.model,
    RESTRICTED_SKILLS: true,
    pendingDelete: p,
    pendingDeleteRef: { current: p },
    deleteRequest: { current: null },
    deleteAlive: { current: true },
    setPendingDeleteState: (value) => {
      state.dialog = value;
    },
    setDeleting: (value) => {
      state.busy = value;
    },
    load: () => {
      state.loads++;
    },
    toast: {
      success: (message) => {
        state.notices.push(message);
      },
    },
    setSelected: () => {
      throw new Error("deletion must not clear selection");
    },
  });
  const source =
    section(view, "  function rootWritesSuspended()", "  const [fileRetry") +
    section(view, "  function setPendingDelete(", "  // Usage counts") +
    section(view, "  async function deletePath(", "  async function saveFile()");
  vm.runInContext(
    stripTypeScriptTypes(`${source}\nglobalThis.actions={runPendingDelete,setPendingDelete,pathDeletionBlocked};`),
    context,
  );
  return { ...h, state, context, ...context.actions };
}
test("actual dialog callback fences same-render repeats and keeps newer selection/draft/dialog", async () => {
  const h = pageHarness();
  await h.confirm();
  await h.load();
  const p = h.runPendingDelete();
  await h.runPendingDelete();
  assert.equal(h.deletes.length, 1);
  h.state.selection = { skill: "beta", path: "y" };
  const newer = { kind: "file", skill: "beta", path: "y" };
  h.setPendingDelete(newer);
  h.model.edit("alpha", "x", "new draft");
  h.deletes[0].resolve({ deleted: "x" });
  await p;
  assert.equal(h.state.dialog, newer);
  assert.equal(h.state.selection.skill, "beta");
  assert.equal(h.draft().content, "new draft");
  assert.equal(h.state.loads, 1);
  assert.equal(h.state.busy, false);
});
test("actual callback preserves its dialog on refusal/unknown and closes only its acknowledged original dialog", async () => {
  for (const kind of ["ok", "refused", "unknown"]) {
    const h = pageHarness();
    await h.confirm();
    const original = h.state.dialog;
    const p = h.runPendingDelete();
    if (kind === "ok") h.deletes[0].resolve({ deleted: "x" });
    else h.deletes[0].reject(new ApiError(kind === "refused" ? 409 : 500, {}, kind));
    await p;
    assert.equal(h.state.dialog, kind === "ok" ? null : original);
    assert.equal(h.state.loads, kind === "ok" ? 1 : 0);
    assert.equal(h.state.busy, false);
  }
});
test("commit-phase retirement prevents deletion callbacks from updating an unmounted page", async () => {
  const h = pageHarness();
  await h.confirm();
  const p = h.runPendingDelete();
  let cleanup;
  h.context.React = {
    useLayoutEffect: (fn) => {
      cleanup = fn();
    },
  };
  const effect = section(view, "  React.useLayoutEffect(() => {\n    deleteAlive", "  function setPendingDelete(");
  vm.runInContext(effect, h.context);
  cleanup();
  h.deletes[0].resolve({ deleted: "x" });
  await p;
  assert.equal(h.state.loads, 0);
  assert.equal(h.state.notices.length, 0);
  assert(h.state.dialog);
  assert.equal(h.context.deleteRequest.current, null);
});
test("actual page blocks stale dialog callbacks, Python SKILL.md and whole-skill action; copy preserves Go recursive semantics", async () => {
  const h = pageHarness();
  await h.confirm();
  h.setPendingDelete({ kind: "file", skill: "beta", path: "y" });
  await h.runPendingDelete();
  assert.equal(h.deletes.length, 0);
  assert.equal(h.pathDeletionBlocked("alpha", "SKILL.md"), true);
  assert.match(view, /p.kind === "skill" \? RESTRICTED_SKILLS \|\| rootWritesSuspended/);
  assert.match(view, /Only an empty folder can be deleted/);
  assert.match(view, /All files in this folder will also be deleted/);
  assert.match(view, /title="Delete skill" disabled=\{RESTRICTED_SKILLS \|\| rootWriteBlocked\}/);
  assert.match(view, /fileDraft.deleted \|\| saving \|\| blocked/);
  assert.match(view, /write mode alone does not enable it/);
  assert.match(view, /All workspace mutations are suspended/);
});

test("failure dialog exposes only its exact target error and labels global unknown with the original target", () => {
  const source = section(view, "  const dialogDeleteError =", "  const folderDeleteDescription");
  const errors = new Map([["alpha", { path: "x", message: "nonempty" }]]);
  const read = (pendingDelete, deletionUncertain = null) => {
    const context = vm.createContext({ pendingDelete, workspaceState: { errors, deletionUncertain } });
    vm.runInContext(`${source}\nglobalThis.result={error:dialogDeleteError,unknown:dialogDeleteUncertain};`, context);
    return context.result;
  };
  assert.equal(read({ kind: "dir", skill: "alpha", path: "x" }).error, "nonempty");
  for (const dialog of [
    null,
    { kind: "skill", skill: "alpha" },
    { kind: "dir", skill: "beta", path: "x" },
    { kind: "file", skill: "alpha", path: "y" },
  ])
    assert.equal(read(dialog).error, null);
  const unknown = { skill: "alpha", path: "x" };
  const replacement = read({ kind: "file", skill: "beta", path: "y" }, unknown);
  assert.equal(replacement.error, null);
  assert.equal(replacement.unknown, unknown);
  const modal = section(view, "        <AlertDialogContent>", "        </AlertDialogContent>");
  assert.match(modal, /dialogDeleteError && <p role="alert"/);
  assert.match(modal, /dialogDeleteUncertain && <p role="alert"/);
  assert.match(modal, /dialogDeleteUncertain.skill/);
  assert.match(modal, /dialogDeleteUncertain.path/);
  assert.match(modal, /including this dialog's target/);
});

test("required deletion path never dispatches the separate whole-skill route in actual API or either model", async () => {
  const inputs = [undefined, null, false, 0, 1, {}, [], ["x"], ""];
  const wire = apiHarness(response({ deleted: "anything" }));
  await assert.rejects(wire.api.deleteSkillPath("alpha"));
  for (const path of inputs) {
    await assert.rejects(wire.api.deleteSkillPath("alpha", path));
    assert.throws(() => confirmedSkillDeletion({ deleted: path }, path));
  }
  assert.equal(wire.calls.length, 0);
  for (const required of [false, true]) {
    const h = harness(required);
    if (required) await h.confirm();
    assert.equal(await h.model.remove("alpha"), false);
    for (const path of inputs) assert.equal(await h.model.remove("alpha", path), false);
    assert.equal(h.deletes.length, 0);
    assert.equal(h.model.getSnapshot().deletionPending, null);
    assert.equal(h.model.getSnapshot().deletionUncertain, null);
  }
  assert.equal(skillResourcePath("alpha"), "/skills/alpha"); // legitimate root helper unchanged
});

import { ApiError, mutationMustWait } from "../src/lib/mutation-contract.mjs";
import {
  confirmedSkillCatalog,
  confirmedSkillContent,
  confirmedSkillDirectory,
  confirmedSkillWrite,
  createSkillWorkspace,
  skillFileKey,
  skillResourcePath,
} from "../src/lib/skill-workspace.mjs";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { stripTypeScriptTypes } from "node:module";
import test from "node:test";
import vm from "node:vm";

const root = new URL("../../", import.meta.url);
const apiSource = await readFile(new URL("web/src/lib/api.ts", root), "utf8");
const view = await readFile(new URL("web/src/app/(main)/system/skills/page.tsx", root), "utf8");
function section(source, start, end) {
  const from = source.indexOf(start),
    to = source.indexOf(end, from + start.length);
  assert.ok(from >= 0 && to > from, `missing source boundary: ${start}`);
  return source.slice(from, to);
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
  for (let i = 0; i < 12; i++) await Promise.resolve();
}
function harness() {
  const reads = [],
    writes = [],
    dirs = [];
  const api = Object.fromEntries(
    [
      ["readSkillFile", reads],
      ["writeSkillFile", writes],
      ["createSkillDir", dirs],
    ].map(([key, list]) => [
      key,
      (...args) => {
        const d = deferred();
        list.push({ args, ...d });
        return d.promise;
      },
    ]),
  );
  const model = createSkillWorkspace(api);
  const get = (skill = "alpha", path = "SKILL.md") => model.getSnapshot().drafts.get(skillFileKey(skill, path));
  const load = async (skill = "alpha", path = "SKILL.md", content = "saved") => {
    const cleanup = model.read(skill, path);
    await flush();
    reads.at(-1).resolve(content);
    await flush();
    return cleanup;
  };
  return { api, model, reads, writes, dirs, get, load };
}
function transport(response) {
  const calls = [];
  const http = section(apiSource, "export async function http<", "\n// sseUrl").replace("export async", "async");
  const helpers = section(apiSource, "const get = <T>", "\n// Go serializes");
  const methods =
    section(apiSource, "  skills:", "  createSkill:") + section(apiSource, "  createSkillDir:", "  deleteSkillPath:");
  const context = vm.createContext({
    ApiError,
    skillResourcePath,
    confirmedSkillWrite,
    confirmedSkillDirectory,
    confirmedSkillContent,
    confirmedSkillCatalog,
    MOCK: false,
    getToken: () => "synthetic-token",
    fetch: async (...args) => {
      calls.push(args);
      if (response instanceof Error) throw response;
      return response;
    },
  });
  vm.runInContext(stripTypeScriptTypes(`${http}\n${helpers}\nglobalThis.api = {${methods}};`), context);
  return { api: context.api, calls };
}
const response = (body, status = 200) => ({ status, ok: status >= 200 && status < 300, json: async () => body });

test("Go/Python confirmed file and folder contracts require exact coherent payloads", () => {
  assert.deepEqual(confirmedSkillWrite({ ok: true, ignored: 1 }), { ok: true });
  assert.deepEqual(confirmedSkillDirectory({ dir: "nested/资料" }, "nested/资料"), { dir: "nested/资料" });
  for (const value of [
    null,
    [],
    {},
    true,
    { ok: 1 },
    { ok: false },
    { ok: true, pending: true },
    { ok: true, unknown: true },
    { ok: true, retry: false },
  ])
    assert.throws(() => confirmedSkillWrite(value), mutationMustWait);
  for (const value of [
    null,
    [],
    {},
    { dir: "wrong" },
    { dir: "nested", pending: true },
    { dir: "nested", retry: false },
  ])
    assert.throws(() => confirmedSkillDirectory(value, "nested"), mutationMustWait);
  assert.equal(confirmedSkillContent({ file: "x", content: "" }, "x"), "");
  for (const value of [null, {}, { file: "wrong", content: "secret" }, { file: "x", content: 1 }])
    assert.throws(() => confirmedSkillContent(value, "x"));
});
test("resource paths encode Unicode and space segments without reinterpreting URL syntax", () => {
  assert.equal(
    skillResourcePath("技能", "资料/a b.md"),
    "/skills/%E6%8A%80%E8%83%BD/files/%E8%B5%84%E6%96%99/a%20b.md",
  );
  assert.notEqual(skillFileKey("a:b", "c"), skillFileKey("a", "b:c"));
  for (const path of ["", "/x", "a//b", "./x", "a/../b", "a/./b", "a/", "a\\b", "a?b", "a#b", "%2e", "a\u0000b"])
    assert.throws(() => skillResourcePath("alpha", path));
});
test("actual API sends Unicode encoded PUT/POST once and validates 200/201 respectively", async () => {
  const put = transport(response({ ok: true }));
  assert.deepEqual(await put.api.writeSkillFile("技能", "资料/a b.md", "新"), { ok: true });
  assert.equal(put.calls[0][0], "/api/skills/%E6%8A%80%E8%83%BD/files/%E8%B5%84%E6%96%99/a%20b.md");
  assert.equal(put.calls[0][1].method, "PUT");
  assert.equal(put.calls[0][1].body, '{"content":"新"}');
  const post = transport(response({ dir: "资料/子目录" }, 201));
  await post.api.createSkillDir("技能", "资料/子目录");
  assert.equal(post.calls[0][0], "/api/skills/%E6%8A%80%E8%83%BD/dirs");
  assert.equal(post.calls[0][1].method, "POST");
  assert.equal(post.calls.length, 1);
});
test("actual file API rejects asynchronous or other unexpected success statuses as unknown", async () => {
  for (const status of [201, 202, 204, 206])
    await assert.rejects(
      transport(response({ ok: true }, status)).api.writeSkillFile("alpha", "x", ""),
      mutationMustWait,
    );
  for (const status of [200, 202, 204])
    await assert.rejects(transport(response({ dir: "x" }, status)).api.createSkillDir("alpha", "x"), mutationMustWait);
  await assert.rejects(transport(response({ ok: false })).api.writeSkillFile("alpha", "x", ""), mutationMustWait);
  await assert.rejects(transport(response({ dir: "y" }, 201)).api.createSkillDir("alpha", "x"), mutationMustWait);
});
test("actual content API checks the returned relative identity and type", async () => {
  assert.equal(
    await transport(response({ file: "资料/x", content: "hello" })).api.readSkillFile("技能", "资料/x"),
    "hello",
  );
  await assert.rejects(transport(response({ file: "y", content: "not x" })).api.readSkillFile("alpha", "x"));
});
test("network/truncated mutation failures retain uncertainty, ordinary explicit denial stays known", async () => {
  await assert.rejects(transport(new Error("lost")).api.writeSkillFile("alpha", "x", ""), mutationMustWait);
  const truncated = {
    status: 503,
    ok: false,
    json: async () => {
      throw Error("truncated");
    },
  };
  await assert.rejects(transport(truncated).api.createSkillDir("alpha", "x"), mutationMustWait);
  await assert.rejects(
    transport(response({ error: "denied" }, 403)).api.writeSkillFile("alpha", "x", ""),
    (e) => e.status === 403 && !mutationMustWait(e),
  );
});
test("drafts hydrate only matching skill/path, retain edits across caller navigation, and do not refetch cached drafts", async () => {
  const h = harness();
  await h.load();
  h.model.edit("alpha", "SKILL.md", "unsaved");
  await h.load("beta", "SKILL.md", "beta");
  h.model.read("alpha", "SKILL.md");
  await flush();
  assert.equal(h.reads.length, 2);
  assert.equal(h.get().content, "unsaved");
  assert.equal(h.get().dirty, true);
  assert.equal(h.get("beta").content, "beta");
});
test("editing or saving before a matching read is loaded cannot dispatch", async () => {
  const h = harness();
  h.model.read("alpha", "SKILL.md");
  h.model.edit("alpha", "SKILL.md", "wrong");
  assert.equal(await h.model.save("alpha", "SKILL.md"), false);
  assert.equal(h.writes.length, 0);
});
test("cleanup retires hanging reads on replay, old completion and cleanup cannot erase replacement", async () => {
  const h = harness();
  const oldCleanup = h.model.read("alpha", "SKILL.md");
  await flush();
  oldCleanup();
  h.model.read("alpha", "SKILL.md");
  await flush();
  oldCleanup();
  assert.equal(h.reads.length, 2);
  h.reads[0].resolve("stale");
  await flush();
  assert.equal(h.get().loaded, false);
  assert.equal(h.get().loading, true);
  h.reads[1].resolve("fresh");
  await flush();
  assert.equal(h.get().content, "fresh");
});
test("late read failure does not clear a later read loading indicator", async () => {
  const h = harness();
  h.model.read("alpha", "SKILL.md");
  await flush();
  h.model.read("alpha", "SKILL.md");
  await flush();
  h.reads[0].reject(Error("old"));
  await flush();
  assert.equal(h.get().error, null);
  assert.equal(h.get().loading, true);
  h.reads[1].reject(Error("current"));
  await flush();
  assert.equal(h.get().error, "current");
  assert.equal(h.get().loaded, false);
  h.model.read("alpha", "SKILL.md");
  await flush();
  h.reads[2].resolve("retry");
  await flush();
  assert.equal(h.get().content, "retry");
});
test("same-turn save repeats coalesce synchronously and snapshot content, not a stale render draft", async () => {
  const h = harness();
  await h.load();
  h.model.edit("alpha", "SKILL.md", "new");
  const first = h.model.save("alpha", "SKILL.md");
  assert.equal(await h.model.save("alpha", "SKILL.md"), false);
  assert.equal(h.writes.length, 1);
  assert.deepEqual(h.writes[0].args, ["alpha", "SKILL.md", "new"]);
  h.writes[0].resolve({ ok: true });
  assert.equal(await first, true);
  assert.equal(h.get().dirty, false);
});
test("save acknowledgement advances only its baseline and preserves a newer same-file edit", async () => {
  const h = harness();
  await h.load();
  h.model.edit("alpha", "SKILL.md", "submitted");
  const op = h.model.save("alpha", "SKILL.md");
  h.model.edit("alpha", "SKILL.md", "newer");
  h.writes[0].resolve({ ok: true });
  await op;
  assert.equal(h.get().content, "newer");
  assert.equal(h.get().baseline, "submitted");
  assert.equal(h.get().dirty, true);
});
test("save A completion never changes B draft or B pending state", async () => {
  const h = harness();
  await h.load();
  await h.load("beta");
  h.model.edit("alpha", "SKILL.md", "A");
  h.model.edit("beta", "SKILL.md", "B");
  const a = h.model.save("alpha", "SKILL.md"),
    b = h.model.save("beta", "SKILL.md");
  h.writes[0].resolve({ ok: true });
  await a;
  assert.equal(h.get("beta").content, "B");
  assert.equal(h.get("beta").dirty, true);
  assert.equal(h.model.getSnapshot().pending.has("beta"), true);
  h.writes[1].resolve({ ok: true });
  await b;
});
test("known write failure preserves draft and permits deliberate retry", async () => {
  const h = harness();
  await h.load();
  h.model.edit("alpha", "SKILL.md", "new");
  const op = h.model.save("alpha", "SKILL.md");
  h.writes[0].reject(new ApiError(403, { error: "denied" }, "failed"));
  assert.equal(await op, false);
  assert.equal(h.get().dirty, true);
  const retry = h.model.save("alpha", "SKILL.md");
  assert.equal(h.writes.length, 2);
  h.writes[1].resolve({ ok: true });
  await retry;
});
test("pending/unknown/no-retry and malformed receipts latch the entire skill, not another skill", async () => {
  for (const payload of [{ pending: true }, { unknown: true }, { retry: false }, null]) {
    const h = harness();
    await h.load();
    h.model.edit("alpha", "SKILL.md", "new");
    const op = h.model.save("alpha", "SKILL.md");
    if (payload) h.writes[0].reject(new ApiError(503, payload, "unconfirmed"));
    else h.writes[0].resolve({ ok: false });
    assert.equal(await op, false);
    assert.equal(h.model.getSnapshot().uncertain.has("alpha"), true);
    assert.equal(await h.model.create("alpha", "other", "file"), false);
    assert.equal(await h.model.save("alpha", "SKILL.md"), false);
    h.model.read("alpha", "SKILL.md");
    assert.equal(h.model.getSnapshot().uncertain.has("alpha"), true);
    const other = h.model.create("beta", "new-dir", "dir");
    h.dirs[0].resolve({ dir: "new-dir" });
    assert.equal(await other, true);
  }
});
test("save and create operations fence one another within a skill in either dispatch order", async () => {
  const h = harness();
  await h.load();
  h.model.edit("alpha", "SKILL.md", "new");
  const mkdir = h.model.create("alpha", "nested", "dir");
  assert.equal(await h.model.save("alpha", "SKILL.md"), false);
  assert.equal(await h.model.create("alpha", "x", "file"), false);
  h.dirs[0].resolve({ dir: "nested" });
  await mkdir;
  const save = h.model.save("alpha", "SKILL.md");
  assert.equal(await h.model.create("alpha", "x", "dir"), false);
  h.writes[0].resolve({ ok: true });
  await save;
});
test("empty-file upsert refuses an unsaved local draft before dispatch", async () => {
  const h = harness();
  await h.load();
  h.model.edit("alpha", "SKILL.md", "keep me");
  assert.equal(await h.model.create("alpha", "SKILL.md", "file"), false);
  assert.equal(h.writes.length, 0);
  assert.equal(h.get().content, "keep me");
});
test("confirmed empty-file upsert refreshes clean cached content but preserves an edit made during dispatch", async () => {
  for (const editDuring of [false, true]) {
    const h = harness();
    await h.load();
    const op = h.model.create("alpha", "SKILL.md", "file");
    if (editDuring) h.model.edit("alpha", "SKILL.md", "newer draft");
    h.writes[0].resolve({ ok: true });
    assert.equal(await op, true);
    assert.equal(h.get().content, editDuring ? "newer draft" : "");
    assert.equal(h.get().dirty, editDuring);
  }
});
test("reads begun before or during create cannot overwrite its acknowledged empty content", async () => {
  const h = harness();
  h.model.read("alpha", "x");
  await flush();
  const op = h.model.create("alpha", "x", "file");
  h.model.read("alpha", "x");
  await flush();
  h.writes[0].resolve({ ok: true });
  await op;
  h.reads[1].resolve("stale during");
  h.reads[0].resolve("stale before");
  await flush();
  assert.equal(h.get("alpha", "x").content, "");
  assert.equal(h.get("alpha", "x").dirty, false);
});
test("malformed folder acknowledgement latches instead of announcing creation", async () => {
  const h = harness();
  const op = h.model.create("alpha", "nested", "dir");
  h.dirs[0].resolve({ dir: "wrong" });
  assert.equal(await op, false);
  assert.equal(h.model.getSnapshot().uncertain.get("alpha").path, "nested");
});
test("invalid paths fail before writes and never alter an existing draft", async () => {
  const h = harness();
  assert.equal(await h.model.create("alpha", "../x", "file"), false);
  assert.equal(h.writes.length, 0);
  assert.equal(h.model.getSnapshot().uncertain.size, 0);
});
test("unsubscribed workspace receives no callback, settled private state does not target a new workspace", async () => {
  const h = harness();
  await h.load();
  h.model.edit("alpha", "SKILL.md", "new");
  let calls = 0;
  const off = h.model.subscribe(() => calls++);
  const op = h.model.save("alpha", "SKILL.md");
  off();
  const before = calls;
  const fresh = harness();
  h.writes[0].resolve({ ok: true });
  await op;
  assert.equal(calls, before);
  assert.equal(fresh.model.getSnapshot().drafts.size, 0);
});

// Exact page callbacks execute with inert setters/APIs. This does not mount React.
function pageHarness() {
  const operations = [],
    notices = [],
    state = {},
    confirmations = [];
  const context = vm.createContext({
    React: { useRef: (x) => ({ current: x }) },
    alive: { current: true },
    cancelRef: { current: false },
    ensureExpanded: () => undefined,
    setCreating: (x) => {
      state.creating = x;
    },
    setNewEntryName: (x) => {
      state.name = x;
    },
    workspace: {
      getSnapshot: () => ({ writeModeEnabled: true, pending: new Map(), uncertain: new Map() }),
      create: (...args) => {
        const d = deferred();
        operations.push({ args, ...d });
        return d.promise;
      },
    },
    toast: { success: (x) => notices.push(x) },
    load: () => {
      state.loads = (state.loads || 0) + 1;
    },
    window: {
      confirm: (text) => {
        confirmations.push(text);
        return state.confirm !== false;
      },
    },
  });
  vm.runInContext(
    stripTypeScriptTypes(
      section(view, "  const creatingRef =", "\n  async function deletePath(") +
        "\nglobalThis.actions = {startCreate,commitCreate,cancelCreate};globalThis.entry = newEntryRef;globalThis.owner = creatingRef;",
    ),
    context,
  );
  return {
    ...context.actions,
    context,
    operations,
    notices,
    state,
    confirmations,
    name: (x) => {
      context.entry.current = x;
    },
  };
}
test("page Enter/blur duplicate consumes exact create owner synchronously and warns on every file upsert", async () => {
  const h = pageHarness();
  h.startCreate("alpha", "nested", "file");
  const owner = h.state.creating;
  h.name("x");
  const first = h.commitCreate(owner),
    second = h.commitCreate(owner);
  await second;
  assert.equal(h.operations.length, 1);
  assert.match(h.confirmations[0], /already exists.*replaced/);
  assert.deepEqual([...h.operations[0].args], ["alpha", "nested/x", "file"]);
  h.operations[0].resolve(true);
  await first;
  assert.equal(h.notices.length, 1);
});
test("page declined replacement dispatches nothing and stale owners cannot commit or cancel newer input", async () => {
  const h = pageHarness();
  h.startCreate("alpha", "", "file");
  const old = h.state.creating;
  h.name("old");
  h.state.confirm = false;
  await h.commitCreate(old);
  assert.equal(h.operations.length, 0);
  h.startCreate("beta", "", "dir");
  const next = h.state.creating;
  h.name("next");
  h.cancelCreate(old);
  await h.commitCreate(old);
  assert.equal(h.context.owner.current, next);
  assert.equal(h.context.entry.current, "next");
  assert.equal(h.operations.length, 0);
});
test("old create completion never clears a newer create input or changes selected file", async () => {
  const h = pageHarness();
  h.startCreate("alpha", "", "dir");
  const owner = h.state.creating;
  h.name("old");
  const op = h.commitCreate(owner);
  h.startCreate("beta", "", "file");
  const next = h.state.creating;
  h.name("new");
  h.operations[0].resolve(true);
  await op;
  assert.equal(h.context.owner.current, next);
  assert.equal(h.context.entry.current, "new");
  assert.doesNotMatch(section(view, "  async function commitCreate(", "\n  function cancelCreate("), /setSelected/);
});
test("unmounted create has no toast/catalog refresh and unknown completion never reports success", async () => {
  for (const result of [false, true]) {
    const h = pageHarness();
    h.startCreate("alpha", "", "dir");
    h.name("x");
    const op = h.commitCreate(h.state.creating);
    if (result) h.context.alive.current = false;
    h.operations[0].resolve(result);
    await op;
    assert.equal(h.notices.length, 0);
    assert.equal(h.state.loads, undefined);
  }
});
test("page wires keyed external snapshot, cleanup reads, current-selection save/edit and restricted profile controls", () => {
  assert.match(view, /useSyncExternalStore\(workspace.subscribe, workspace.getSnapshot, workspace.getSnapshot\)/);
  assert.match(view, /return workspace.read\(readTarget.skill, readTarget.path\)/);
  assert.match(view, /selectionRef.current !== selected/);
  assert.match(view, /selectionRef.current === selected\) workspace.edit/);
  assert.match(view, /if \(!RESTRICTED_SKILLS\) \{\s+api.agents/);
  assert.match(view, /if \(RESTRICTED_SKILLS \|\| !selected/);
  assert.match(view, /!RESTRICTED_SKILLS && <div className="flex flex-col gap-6/);
  assert.match(view, /title="Delete file" disabled=\{RESTRICTED_SKILLS \|\| rootWriteBlocked\}/);
  assert.match(view, /Previously loaded entries may be stale/);
  assert.match(view, /refreshing the catalog does not clear this warning/);
});

test("catalog validates source-shaped identity/files/stats instead of turning malformed success into empty library", async () => {
  const item = { name: "技能", files: ["SKILL.md", "资料/"], calls: 0, tasks: 0, usage_agents: [] };
  assert.deepEqual(confirmedSkillCatalog({ skills: [item] }), [item]);
  assert.deepEqual(await transport(response({ skills: [] })).api.skills(), []);
  for (const body of [
    null,
    {},
    { skills: null },
    { skills: [{}] },
    { skills: [{ ...item, files: [null] }] },
    { skills: [{ ...item, usage_agents: null }] },
  ]) {
    await assert.rejects(transport(response(body)).api.skills());
  }
});
function savePageHarness() {
  const h = harness(),
    notices = [];
  const selected = { skill: "alpha", path: "SKILL.md" };
  const context = vm.createContext({
    selected,
    selectionRef: { current: selected },
    alive: { current: true },
    workspace: h.model,
    toast: { success: (x) => notices.push(x) },
  });
  vm.runInContext(
    stripTypeScriptTypes(
      section(view, "  async function saveFile()", "\n  async function toggleSkillMcp") +
        "\nglobalThis.save = saveFile;",
    ),
    context,
  );
  return { ...h, context, notices, save: context.save };
}
test("actual page save callback refuses a stale selection closure and snapshots synchronous newest edit", async () => {
  const h = savePageHarness();
  await h.load();
  h.model.edit("alpha", "SKILL.md", "first");
  const original = h.context.selected;
  h.context.selectionRef.current = { skill: "beta", path: "other" };
  await h.save();
  assert.equal(h.writes.length, 0);
  h.context.selectionRef.current = original;
  h.model.edit("alpha", "SKILL.md", "latest");
  const op = h.save();
  assert.equal(h.writes[0].args[2], "latest");
  h.writes[0].resolve({ ok: true });
  await op;
});
test("actual page save completion neither clears a newer draft nor reports after unmount", async () => {
  const h = savePageHarness();
  await h.load();
  h.model.edit("alpha", "SKILL.md", "first");
  const op = h.save();
  h.model.edit("alpha", "SKILL.md", "newer");
  h.context.alive.current = false;
  h.writes[0].resolve({ ok: true });
  await op;
  assert.equal(h.get().content, "newer");
  assert.equal(h.get().dirty, true);
  assert.equal(h.notices.length, 0);
});
function catalogHarness(restricted = true) {
  const requests = [],
    ancillary = [],
    state = {};
  const context = vm.createContext({
    RESTRICTED_SKILLS: restricted,
    alive: { current: true },
    catalogRequest: { current: null },
    React: { useCallback: (fn) => fn },
    setCatalogLoading: (value) => {
      state.loading = value;
    },
    setCatalogError: (value) => {
      state.error = value;
    },
    setSkills: (value) => {
      state.skills = value;
    },
    setAgents: () => undefined,
    setMcpOptions: () => undefined,
    setMissing: () => undefined,
    setVisibility: () => undefined,
    api: {
      skills: () => {
        const d = deferred();
        requests.push(d);
        return d.promise;
      },
      ...Object.fromEntries(
        ["agents", "mcpServers", "missingSkills", "skillVisibility"].map((name) => [
          name,
          async () => {
            ancillary.push(name);
            return [];
          },
        ]),
      ),
    },
  });
  vm.runInContext(
    stripTypeScriptTypes(
      section(view, "  const load = React.useCallback(", "\n  React.useEffect(() => { load();") +
        "\nglobalThis.reload = load;",
    ),
    context,
  );
  return { context, state, requests, ancillary, reload: context.reload };
}
test("actual restricted catalog loader makes no unsupported ancillary requests and ignores stale success/error/finally", async () => {
  const h = catalogHarness();
  h.reload();
  h.reload();
  h.requests[0].resolve([{ name: "stale" }]);
  await flush();
  assert.equal(h.state.skills, undefined);
  assert.equal(h.state.loading, true);
  h.requests[1].resolve([{ name: "fresh" }]);
  await flush();
  assert.deepEqual(h.state.skills, [{ name: "fresh" }]);
  assert.equal(h.ancillary.length, 0);
  h.reload();
  h.reload();
  h.requests[2].reject(Error("stale"));
  await flush();
  assert.equal(h.state.error, null);
  assert.equal(h.state.loading, true);
  h.requests[3].reject(Error("current"));
  await flush();
  assert.equal(h.state.error, "current");
  assert.equal(h.state.skills[0].name, "fresh");
});
test("catalog cleanup/unmount suppresses completion and Go ancillary requests remain available", async () => {
  const h = catalogHarness();
  h.reload();
  h.context.alive.current = false;
  h.context.catalogRequest.current = null;
  h.requests[0].resolve([{ name: "retired" }]);
  await flush();
  assert.equal(h.state.skills, undefined);
  const go = catalogHarness(false);
  go.reload();
  go.requests[0].resolve([{ name: "go" }]);
  await flush();
  assert.deepEqual(go.ancillary, ["agents", "mcpServers", "missingSkills", "skillVisibility"]);
});

test("read hydrating an initially absent entry during empty upsert is not mistaken for a user edit", async () => {
  const h = harness();
  const op = h.model.create("alpha", "x", "file");
  h.model.read("alpha", "x");
  await flush();
  h.reads[0].resolve("old content");
  await flush();
  assert.equal(h.get("alpha", "x").revision, 0);
  h.writes[0].resolve({ ok: true });
  await op;
  assert.equal(h.get("alpha", "x").content, "");
  assert.equal(h.get("alpha", "x").dirty, false);
});
test("real edit after hydration during empty upsert is retained, unlike mere hydration", async () => {
  const h = harness();
  const op = h.model.create("alpha", "x", "file");
  h.model.read("alpha", "x");
  await flush();
  h.reads[0].resolve("old content");
  await flush();
  h.model.edit("alpha", "x", "typed during write");
  h.writes[0].resolve({ ok: true });
  await op;
  assert.equal(h.get("alpha", "x").content, "typed during write");
  assert.equal(h.get("alpha", "x").dirty, true);
});
test("known failed mutation exposes a retryable read when it retired the only initial reader", async () => {
  const h = harness();
  h.model.read("alpha", "x");
  await flush();
  const op = h.model.create("alpha", "x", "file");
  h.writes[0].reject(new ApiError(403, { error: "denied" }, "denied"));
  await op;
  h.reads[0].resolve("old owner");
  await flush();
  assert.equal(h.get("alpha", "x").loaded, false);
  assert.match(h.get("alpha", "x").error, /Retry read/);
  assert.equal(h.get("alpha", "x").loading, false);
  h.model.read("alpha", "x");
  await flush();
  h.reads[1].resolve("actual content");
  await flush();
  assert.equal(h.get("alpha", "x").content, "actual content");
  assert.equal(h.get("alpha", "x").error, null);
});
test("retrying the retired initial read after unknown write does not unlock any writes", async () => {
  const h = harness();
  h.model.read("alpha", "x");
  await flush();
  const op = h.model.create("alpha", "x", "file");
  h.writes[0].reject(new ApiError(500, { unknown: true, retry: false }, "unknown"));
  await op;
  assert.match(h.get("alpha", "x").error, /Retry read/);
  h.model.read("alpha", "x");
  await flush();
  h.reads[1].resolve("observed content");
  await flush();
  assert.equal(h.model.getSnapshot().uncertain.has("alpha"), true);
  h.model.edit("alpha", "x", "still a local draft");
  assert.equal(await h.model.save("alpha", "x"), false);
  assert.equal(h.writes.length, 1);
});
test("mutation failure preserves a replacement read owner already in flight", async () => {
  const h = harness();
  h.model.read("alpha", "x");
  await flush();
  const op = h.model.create("alpha", "x", "file");
  h.model.read("alpha", "x");
  await flush();
  h.writes[0].reject(new ApiError(403, {}, "denied"));
  await op;
  assert.equal(h.get("alpha", "x").loading, true);
  assert.equal(h.get("alpha", "x").error, null);
  h.reads[0].resolve("retired");
  h.reads[1].resolve("fresh");
  await flush();
  assert.equal(h.get("alpha", "x").content, "fresh");
});

const mockSource = await readFile(new URL("web/src/lib/mock/handler.ts", root), "utf8");
const mockDataSource = await readFile(new URL("web/src/lib/mock/data.ts", root), "utf8");
function mockTransport() {
  const fixtures = vm.createContext({});
  vm.runInContext(
    stripTypeScriptTypes(
      section(mockDataSource, "export const skills:", "export const skillCalls:").replace("export const", "const") +
        "\nglobalThis.skills = skills;",
    ),
    fixtures,
  );
  const context = vm.createContext({
    MOCK: true,
    ApiError,
    skillResourcePath,
    confirmedSkillWrite,
    confirmedSkillDirectory,
    confirmedSkillContent,
    confirmedSkillCatalog,
    URLSearchParams,
    D: { skills: fixtures.skills },
    delay: async () => undefined,
    advanceMockRetests: () => undefined,
    fetch: () => {
      throw Error("mock mode must not send network requests");
    },
  });
  const http = section(apiSource, "export async function http<", "\n// sseUrl").replace("export async", "async");
  const helpers = section(apiSource, "const get = <T>", "\n// Go serializes");
  const mockEntry = section(mockSource, "function parseBody(", "\nfunction route(").replace("export async", "async");
  const mockRoute = `function route(m, path, seg, q, b) { ${section(mockSource, "  // ── skills ──", "  // ── visibility ──")} throw new Error('unexpected mock route'); }`;
  const methods =
    section(apiSource, "  skills:", "  createSkill:") + section(apiSource, "  createSkillDir:", "  deleteSkillPath:");
  vm.runInContext(
    stripTypeScriptTypes(`${mockEntry}\n${mockRoute}\n${http}\n${helpers}\nglobalThis.api = {${methods}};`),
    context,
  );
  return context.api;
}
test("actual MOCK short-circuit, skill routes and default catalog fixture satisfy strict read/write/folder receipts", async () => {
  const api = mockTransport();
  assert.equal((await api.skills()).length, 3);
  assert.deepEqual(await api.writeSkillFile("api-recon", "SKILL.md", "demo write"), { ok: true });
  assert.deepEqual(await api.createSkillDir("api-recon", "资料/子目录"), { dir: "资料/子目录" });
  assert.match(await api.readSkillFile("技能", "资料/a b.md"), /\(demo\)/);
});
test("MOCK workspace does not latch successful file save or directory acknowledgement", async () => {
  const api = mockTransport(),
    model = createSkillWorkspace(api);
  model.read("api-recon", "SKILL.md");
  await flush();
  model.edit("api-recon", "SKILL.md", "demo edit");
  assert.equal(await model.save("api-recon", "SKILL.md"), true);
  assert.equal(await model.create("api-recon", "nested", "dir"), true);
  assert.equal(model.getSnapshot().uncertain.size, 0);
});

test("successful mkdir at a stale selected-file path also exposes ownerless read retry", async () => {
  const h = harness();
  h.model.read("alpha", "x");
  await flush();
  const op = h.model.create("alpha", "x", "dir");
  h.dirs[0].resolve({ dir: "x" });
  assert.equal(await op, true);
  h.reads[0].resolve("retired old file");
  await flush();
  assert.equal(h.get("alpha", "x").loaded, false);
  assert.equal(h.get("alpha", "x").loading, false);
  assert.match(h.get("alpha", "x").error, /Retry read/);
  assert.equal(h.model.getSnapshot().uncertain.size, 0);
});
test("settled mutation keeps a more specific read error rather than replacing it with generic retry text", async () => {
  const h = harness();
  h.model.read("alpha", "x");
  await flush();
  h.reads[0].reject(Error("File exceeds read limit"));
  await flush();
  const op = h.model.create("alpha", "x", "dir");
  h.dirs[0].resolve({ dir: "x" });
  await op;
  assert.equal(h.get("alpha", "x").error, "File exceeds read limit");
});

test("control-byte rejection is unchanged and retry is explicit in the page read-request identity", () => {
  for (const code of [...Array(32).keys(), 127]) {
    const c = String.fromCharCode(code);
    assert.throws(() => skillResourcePath(`a${c}b`, "x"));
    assert.throws(() => skillResourcePath("alpha", `a${c}b`));
  }
  assert.match(view, /skill: selected\?\.skill, path: selected\?\.path, retry: fileRetry/);
  assert.match(view, /\[selected\?\.skill, selected\?\.path, fileRetry\]/);
  assert.match(view, /\[workspace, readTarget\]/);
});

import { ApiError, mutationMustWait } from "../src/lib/mutation-contract.mjs";
import { confirmedSkillCreation, createSkillWorkspace, validSkillCreationName } from "../src/lib/skill-workspace.mjs";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { stripTypeScriptTypes } from "node:module";
import test from "node:test";
import vm from "node:vm";

const apiSource = await readFile(new URL("../src/lib/api.ts", import.meta.url), "utf8");
const view = await readFile(new URL("../src/app/(main)/system/skills/page.tsx", import.meta.url), "utf8");
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
  for (let i = 0; i < 12; i++) await Promise.resolve();
}
const input = (name = "alpha") => ({ name, description: "Synthetic documentation" });
const receipt = (name = "alpha") => ({ name });
const response = (body, status = 201) => ({ status, ok: status >= 200 && status < 300, json: async () => body });
function transport(result, mock = false) {
  const calls = [];
  const http = section(apiSource, "export async function http<", "\n// sseUrl").replace("export async", "async");
  const method = section(apiSource, "  createSkill:", "  // uploadSkill");
  const context = vm.createContext({
    ApiError,
    confirmedSkillCreation,
    MOCK: mock,
    getToken: () => "synthetic-token",
    mockHandle: async (...args) => {
      calls.push(args);
      return result;
    },
    fetch: async (...args) => {
      calls.push(args);
      if (result instanceof Error) throw result;
      return result;
    },
  });
  vm.runInContext(stripTypeScriptTypes(`${http}\nglobalThis.api={${method}};`), context);
  return { calls, api: context.api };
}
function modelHarness() {
  const creates = [],
    writes = [],
    dirs = [];
  const api = {
    createSkill: (value) => {
      const d = deferred();
      creates.push({ ...d, value });
      return d.promise;
    },
    writeSkillFile: (...args) => {
      const d = deferred();
      writes.push({ ...d, args });
      return d.promise;
    },
    createSkillDir: (...args) => {
      const d = deferred();
      dirs.push({ ...d, args });
      return d.promise;
    },
    readSkillFile: async () => "existing draft",
  };
  const model = createSkillWorkspace(api);
  return {
    model,
    creates,
    writes,
    dirs,
    load: async () => {
      model.read("existing", "SKILL.md");
      await flush();
      model.edit("existing", "SKILL.md", "unsaved text");
    },
  };
}

test("creation names match Unicode letter/digit Go/Python rules and filesystem byte bound", () => {
  for (const name of ["a", "alpha-2", "技能-٢", "École", "a".repeat(64), `a${"𐐀".repeat(63)}`])
    assert.equal(validSkillCreationName(name), true, name);
  for (const name of [
    "",
    "A",
    "1alpha",
    "-a",
    "a-",
    "a--b",
    "a/b",
    "a.b",
    "a b",
    "a\n",
    "e\u0301",
    "😀",
    null,
    {},
    "a".repeat(65),
    "𐐀".repeat(64),
  ])
    assert.equal(validSkillCreationName(name), false, String(name));
});
test("creation receipt requires exact submitted name and no uncertainty envelope", () => {
  assert.deepEqual(confirmedSkillCreation({ name: "技能", extra: true }, "技能"), { name: "技能" });
  for (const value of [
    null,
    [],
    {},
    true,
    { ok: true },
    { name: "other" },
    { name: "alpha", pending: true },
    { name: "alpha", unknown: true },
    { name: "alpha", outcome: "unknown" },
    { name: "alpha", retry: false },
  ])
    assert.throws(() => confirmedSkillCreation(value, "alpha"), mutationMustWait);
});
test("actual API requires HTTP201, serializes the same request and freezes receipt identity", async () => {
  const h = transport(response(receipt("技能")));
  const payload = input("技能");
  const pending = h.api.createSkill(payload);
  payload.name = "later";
  assert.equal((await pending).name, "技能");
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0][0], "/api/skills");
  assert.equal(h.calls[0][1].method, "POST");
  assert.equal(JSON.parse(h.calls[0][1].body).name, "技能");
  for (const status of [200, 202, 204])
    await assert.rejects(transport(response(receipt(), status)).api.createSkill(input()), mutationMustWait);
});
test("malformed, truncated and lost responses remain uncertain while readable denial remains known", async () => {
  for (const r of [
    response({ ok: true }),
    {
      ...response(receipt()),
      json: async () => {
        throw new SyntaxError("truncated");
      },
    },
    new Error("lost"),
  ])
    await assert.rejects(transport(r).api.createSkill(input()), mutationMustWait);
  for (const status of [400, 403, 409, 413])
    await assert.rejects(
      transport(response({ error: "denied" }, status)).api.createSkill(input()),
      (e) => e.status === status && !mutationMustWait(e),
    );
  assert.equal((await transport(receipt("技能"), true).api.createSkill(input("技能"))).name, "技能");
});
test("actual MOCK POST branch keeps exact Unicode named receipt", async () => {
  const source = await readFile(new URL("../src/lib/mock/handler.ts", import.meta.url), "utf8");
  const branch = section(source, '  if (path === "/skills" && m === "POST")', '\n  if (seg[0] === "skills"');
  const result = vm.runInNewContext(`(()=>{${branch}})()`, { path: "/skills", m: "POST", b: input("技能") });
  assert.deepEqual(confirmedSkillCreation(result, "技能"), { name: "技能" });
});
test("root creation fences same-turn duplicates and file/folder writes without clearing drafts", async () => {
  const h = modelHarness();
  await h.load();
  const p = h.model.createSkill(input());
  assert.equal(await h.model.createSkill(input()), null);
  assert.equal(await h.model.save("existing", "SKILL.md"), false);
  assert.equal(await h.model.create("existing", "new", "file"), false);
  assert.equal(await h.model.create("other", "dir", "dir"), false);
  assert.equal(h.creates.length, 1);
  assert.equal(h.writes.length + h.dirs.length, 0);
  h.model.edit("existing", "SKILL.md", "newer draft");
  h.creates[0].resolve(receipt());
  assert.deepEqual(await p, receipt());
  assert.equal(h.model.getSnapshot().drafts.get('["existing","SKILL.md"]').content, "newer draft");
  assert.equal(h.model.getSnapshot().rootPending, null);
});
test("an already pending file mutation excludes root creation until it settles", async () => {
  const h = modelHarness();
  await h.load();
  const save = h.model.save("existing", "SKILL.md");
  assert.equal(await h.model.createSkill(input()), null);
  assert.equal(h.creates.length, 0);
  h.writes[0].resolve({ ok: true });
  await save;
  const create = h.model.createSkill(input());
  assert.equal(h.creates.length, 1);
  h.creates[0].resolve(receipt());
  await create;
});
test("unknown/5xx/malformed creation globally suspends writes despite reads and changed names", async () => {
  for (const failure of [
    new ApiError(0, { unknown: true, retry: false }, "lost"),
    new ApiError(500, { outcome: "unknown", retry: false }, "unknown"),
    new ApiError(503, {}, "unavailable"),
    { name: "wrong" },
  ]) {
    const h = modelHarness();
    await h.load();
    const p = h.model.createSkill(input());
    if (failure instanceof Error) h.creates[0].reject(failure);
    else h.creates[0].resolve(failure);
    assert.equal(await p, null);
    assert(h.model.getSnapshot().rootUncertain);
    h.model.read("other", "SKILL.md");
    await flush();
    h.model.edit("existing", "SKILL.md", "kept after unknown");
    assert.equal(await h.model.createSkill(input("different")), null);
    assert.equal(await h.model.save("existing", "SKILL.md"), false);
    assert.equal(await h.model.create("other", "x", "dir"), false);
    assert.equal(h.creates.length, 1);
    assert.equal(h.writes.length + h.dirs.length, 0);
  }
});
test("known duplicate/read-only/validation/size errors keep draft and permit explicit corrected retry", async () => {
  for (const status of [400, 403, 409, 413]) {
    const h = modelHarness();
    await h.load();
    const p = h.model.createSkill(input());
    h.creates[0].reject(new ApiError(status, { error: "known refusal" }, "refused"));
    assert.equal(await p, null);
    assert.equal(h.model.getSnapshot().rootUncertain, null);
    assert.equal(h.model.getSnapshot().rootError.message, "known refusal");
    const retry = h.model.createSkill(input("corrected"));
    assert.equal(h.creates.length, 2);
    h.creates[1].resolve(receipt("corrected"));
    assert.deepEqual(await retry, receipt("corrected"));
  }
});
test("invalid local input sends nothing and existing file uncertainty cannot be bypassed with a new root", async () => {
  const h = modelHarness();
  assert.equal(await h.model.createSkill(input("bad/name")), null);
  assert.equal(await h.model.createSkill({ name: "alpha", description: " " }), null);
  assert.equal(h.creates.length, 0);
  await h.load();
  const write = h.model.save("existing", "SKILL.md");
  h.writes[0].reject(new ApiError(0, { unknown: true }, "lost"));
  await write;
  assert.equal(await h.model.createSkill(input()), null);
  assert.equal(h.creates.length, 0);
});

function pageHarness(restricted = true) {
  const h = modelHarness(),
    notices = [],
    visibility = [];
  const draft = {
    name: "alpha",
    description: "Original description",
    license: "MIT",
    compatibility: "offline",
    instructions: "Original body",
    mcps: ["demo"],
    visibility: ["agent-one"],
  };
  const state = { draft, open: true, busy: false, loads: 0 };
  const context = vm.createContext({
    workspace: h.model,
    RESTRICTED_SKILLS: restricted,
    validSkillCreationName,
    alive: { current: true },
    newDraftRef: { current: draft },
    newDialog: { current: { open: true } },
    newRequest: { current: null },
    setNewDraft: (value) => {
      state.draft = value;
    },
    setNewOpenState: (value) => {
      state.open = value;
    },
    setCreatingSkill: (value) => {
      state.busy = value;
    },
    load: () => {
      state.loads++;
    },
    toast: { success: (m) => notices.push(["success", m]), error: (m) => notices.push(["error", m]) },
    api: {
      toggleSkillVisibility: (...args) => {
        const d = deferred();
        visibility.push({ ...d, args });
        return d.promise;
      },
    },
  });
  const source =
    section(view, "type NewSkillDraft =", "function fmtTime(") +
    section(view, "  function editNewSkill(", "  const [mcpOptions") +
    section(view, "  async function createNewSkill()", "  // ── Inline input JSX helper");
  vm.runInContext(
    stripTypeScriptTypes(`${source}\nglobalThis.actions={createNewSkill,editNewSkill,setNewOpen};`),
    context,
  );
  return { ...h, state, context, notices, visibility, ...context.actions };
}
test("actual new-skill callback fences repeated clicks and Python omits all unsupported fields/calls", async () => {
  const h = pageHarness();
  const p = h.createNewSkill();
  await h.createNewSkill();
  assert.equal(h.creates.length, 1);
  assert.equal("mcps" in h.creates[0].value, false);
  assert.equal("visibility" in h.creates[0].value, false);
  h.creates[0].resolve(receipt());
  await p;
  assert.equal(h.visibility.length, 0);
  assert.equal(h.state.open, false);
  assert.equal(h.state.draft.name, "");
  assert.equal(h.state.loads, 1);
  assert.equal(h.state.busy, false);
});
test("latest same-turn draft is submitted and later same-value edits retire reset authority", async () => {
  const h = pageHarness();
  h.editNewSkill({ name: "new-name", description: "new text" });
  const p = h.createNewSkill();
  assert.equal(h.creates[0].value.name, "new-name");
  h.editNewSkill({ description: "new text" });
  h.creates[0].resolve(receipt("new-name"));
  await p;
  assert.equal(h.state.open, true);
  assert.equal(h.state.draft.description, "new text");
});
test("later edits and close/reopen are never cleared or closed by old acknowledgement", async () => {
  const h = pageHarness();
  const p = h.createNewSkill();
  h.setNewOpen(false);
  h.setNewOpen(true);
  h.editNewSkill({ instructions: "new body", name: "next-skill" });
  h.creates[0].resolve(receipt());
  await p;
  assert.equal(h.state.open, true);
  assert.equal(h.state.draft.name, "next-skill");
  assert.equal(h.state.draft.instructions, "new body");
});
test("unchanged submitted draft clears after reopen but newer dialog session remains open", async () => {
  const h = pageHarness();
  const p = h.createNewSkill();
  h.setNewOpen(false);
  h.setNewOpen(true);
  h.creates[0].resolve(receipt());
  await p;
  assert.equal(h.state.draft.name, "");
  assert.equal(h.state.open, true);
});
test("unknown creation preserves the dialog draft and cannot be unlocked by editing or reopening", async () => {
  const h = pageHarness();
  const p = h.createNewSkill();
  h.creates[0].reject(new ApiError(500, { retry: false }, "unknown"));
  await p;
  assert.equal(h.state.draft.name, "alpha");
  h.setNewOpen(false);
  h.setNewOpen(true);
  h.editNewSkill({ name: "different" });
  await h.createNewSkill();
  assert.equal(h.creates.length, 1);
  assert.equal(h.notices.length, 0);
});
test("Go secondary visibility failure never relabels confirmed primary creation as failed or repeats it", async () => {
  const h = pageHarness(false);
  const p = h.createNewSkill();
  h.creates[0].resolve(receipt());
  await flush();
  assert.equal(h.visibility.length, 1);
  await h.createNewSkill();
  assert.equal(h.creates.length, 1);
  h.visibility[0].reject(new Error("visibility refused"));
  await p;
  assert(h.notices.some(([kind, message]) => kind === "error" && message.includes("created, but some visibility")));
  assert.equal(h.state.loads, 1);
  assert.equal(h.model.getSnapshot().rootUncertain, null);
  assert.deepEqual(h.creates[0].value.mcps, ["demo"]);
});
test("unmounted callback neither applies optional visibility nor changes draft/dialog or announces success", async () => {
  const h = pageHarness(false);
  const p = h.createNewSkill();
  h.context.alive.current = false;
  h.creates[0].resolve(receipt());
  await p;
  assert.equal(h.visibility.length, 0);
  assert.equal(h.notices.length, 0);
  assert.equal(h.state.loads, 0);
  assert.equal(h.state.draft.name, "alpha");
});
test("closed dialog and locally invalid name cannot dispatch creation", async () => {
  const h = pageHarness();
  h.setNewOpen(false);
  await h.createNewSkill();
  assert.equal(h.creates.length, 0);
  h.setNewOpen(true);
  h.editNewSkill({ name: "-bad" });
  await h.createNewSkill();
  assert.equal(h.creates.length, 0);
  assert(h.notices.some(([kind]) => kind === "error"));
});
test("existing mutation callbacks check root suspension synchronously before API or optimistic changes", async () => {
  const context = vm.createContext({ workspace: { getSnapshot: () => ({ rootUncertain: { name: "alpha" } }) } });
  const methods =
    section(view, "  function rootWritesSuspended()", "  const [fileRetry") +
    section(view, "  async function deletePath(", "  async function runPendingDelete()") +
    section(view, "  async function toggleSkillMcp(", "  async function createNewSkill()");
  vm.runInContext(
    stripTypeScriptTypes(`${methods}\nglobalThis.actions=[deletePath,deleteSkill,toggleSkillMcp,toggleVisibility];`),
    context,
  );
  for (const action of context.actions) await action("alpha", "x", true);
});
test("actual JSX gates global writes, retains draft edits and hides Python MCP/visibility tabs", () => {
  assert.match(view, /!RESTRICTED_SKILLS && <TabsTrigger value="mcp"/);
  assert.match(view, /!RESTRICTED_SKILLS && <TabsTrigger value="visibility"/);
  assert.match(view, /!RESTRICTED_SKILLS && <TabsContent value="mcp"/);
  assert.match(view, /!RESTRICTED_SKILLS && <TabsContent value="visibility"/);
  assert.match(view, /All writes in this Skills workspace are suspended/);
  assert.match(view, /catalog refresh does not unlock writes/);
  assert.doesNotMatch(
    section(view, "  async function createNewSkill()", "  // ── Inline input JSX helper"),
    /setSelected/,
  );
  assert.match(view, /disabled=\{creatingSkill \|\| rootWriteBlocked/);
  assert.match(view, /if \(state.rootPending \|\| state.rootUncertain/);
});

test("confirmed primary creation is announced/refreshed before optional visibility settles", async () => {
  const h = pageHarness(false);
  const pending = h.createNewSkill();
  h.creates[0].resolve(receipt());
  await flush();
  assert.equal(h.visibility.length, 1);
  assert.equal(h.state.loads, 1);
  assert.equal(h.state.open, false);
  assert(h.notices.some(([kind]) => kind === "success"));
  h.setNewOpen(true);
  h.editNewSkill({ name: "next", description: "later draft" });
  h.visibility[0].reject(new Error("denied"));
  await pending;
  assert.equal(h.state.open, true);
  assert.equal(h.state.draft.name, "next");
  assert.equal(h.state.draft.description, "later draft");
});
test("model snapshots the requested identity and optional array without mutating caller data", async () => {
  const h = modelHarness();
  const value = { ...input(), mcps: ["one"] };
  const p = h.model.createSkill(value);
  value.name = "later";
  value.mcps.push("two");
  assert.equal(h.creates[0].value.name, "alpha");
  assert.deepEqual(h.creates[0].value.mcps, ["one"]);
  h.creates[0].value.name = "adapter-mutated";
  h.creates[0].resolve(receipt());
  assert.deepEqual(await p, receipt());
});

test("effect cleanup/setup retains unknown ownership and recovers local busy state after inactive completion", async () => {
  const effect = section(view, "  React.useEffect(() => {\n    alive.current = true;", "  const [visibility");
  const h = pageHarness();
  h.context.catalogRequest = { current: null };
  h.context.React = {
    useEffect: (setup) => {
      h.context.cleanup = setup();
    },
  };
  const pending = h.createNewSkill();
  vm.runInContext(stripTypeScriptTypes(effect), h.context);
  h.context.cleanup();
  h.creates[0].reject(new ApiError(500, { retry: false }, "unknown"));
  await pending;
  vm.runInContext(stripTypeScriptTypes(effect), h.context);
  assert.equal(h.state.busy, false);
  assert.equal(h.context.alive.current, true);
  assert.equal(h.state.draft.name, "alpha");
  await h.createNewSkill();
  assert.equal(h.creates.length, 1);
  assert(h.model.getSnapshot().rootUncertain);
});

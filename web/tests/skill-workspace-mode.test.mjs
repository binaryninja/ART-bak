import { ApiError } from "../src/lib/mutation-contract.mjs";
import { confirmedSkillWorkspaceProfile, createSkillWorkspace } from "../src/lib/skill-workspace.mjs";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { stripTypeScriptTypes } from "node:module";
import test from "node:test";
import vm from "node:vm";

const view = await readFile(new URL("../src/app/(main)/system/skills/page.tsx", import.meta.url), "utf8");
const source = await readFile(new URL("../src/lib/api.ts", import.meta.url), "utf8");
const mode = (writes_enabled = false) => ({ profile: "skills-workspace", writes_enabled });
const response = (body, status = 200) => ({ status, ok: status >= 200 && status < 300, json: async () => body });
function section(s, a, b) {
  const begin = s.indexOf(a),
    end = s.indexOf(b, begin + a.length);
  assert(begin >= 0 && end > begin, a);
  return s.slice(begin, end);
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
  const profiles = [],
    writes = [],
    creates = [],
    dirs = [];
  const api = {
    skillWorkspaceProfile: (signal) => {
      const d = deferred();
      profiles.push({ ...d, signal });
      return d.promise;
    },
    readSkillFile: async () => "old text",
    writeSkillFile: (...args) => {
      const d = deferred();
      writes.push({ ...d, args });
      return d.promise;
    },
    createSkill: (...args) => {
      const d = deferred();
      creates.push({ ...d, args });
      return d.promise;
    },
    createSkillDir: (...args) => {
      const d = deferred();
      dirs.push({ ...d, args });
      return d.promise;
    },
  };
  const model = createSkillWorkspace(api, { requireProfile: required });
  return {
    model,
    profiles,
    writes,
    creates,
    dirs,
    load: async () => {
      model.read("alpha", "SKILL.md");
      await flush();
      model.edit("alpha", "SKILL.md", "local draft");
    },
    confirm: async (value = mode(true)) => {
      model.readProfile();
      await flush();
      profiles.at(-1).resolve(value);
      await flush();
    },
  };
}
function apiHarness(result) {
  const calls = [];
  const context = vm.createContext({
    ApiError,
    confirmedSkillWorkspaceProfile,
    MOCK: false,
    getToken: () => "fixture-token",
    fetch: async (...args) => {
      calls.push(args);
      if (result instanceof Error) throw result;
      return result;
    },
  });
  const http = section(source, "export async function http<", "\n// sseUrl").replace("export async", "async");
  const method = section(source, "  skillWorkspaceProfile:", "  skills:");
  vm.runInContext(stripTypeScriptTypes(`${http}\nglobalThis.api={${method}};`), context);
  return { calls, api: context.api };
}

test("mode receipt is an exact configured-mode DTO, not a truthy or generic success grant", () => {
  for (const value of [mode(), mode(true)]) assert.deepEqual(confirmedSkillWorkspaceProfile(value), value);
  for (const value of [
    null,
    [],
    {},
    true,
    { ok: true },
    { profile: "go", writes_enabled: true },
    { profile: "skills-workspace", writes_enabled: "true" },
    { profile: "skills-workspace", writes_enabled: 1 },
    { ...mode(true), grant: true },
    { ...mode(true), unknown: true },
    { ...mode(true), retry: false },
  ]) {
    assert.throws(() => confirmedSkillWorkspaceProfile(value));
  }
});
test("actual API uses authenticated no-store GET with exact HTTP200 and propagates a read abort signal", async () => {
  const h = apiHarness(response(mode(true))),
    signal = new AbortController().signal;
  assert.equal((await h.api.skillWorkspaceProfile(signal)).writes_enabled, true);
  const [url, init] = h.calls[0];
  assert.equal(url, "/api/skills-workspace");
  assert.equal(init.method, undefined);
  assert.equal(init.cache, "no-store");
  assert.equal(init.signal, signal);
  assert.equal(init.headers.Authorization, "Bearer fixture-token");
  assert.equal(init.body, undefined);
  for (const status of [201, 202, 204])
    await assert.rejects(apiHarness(response(mode(true), status)).api.skillWorkspaceProfile());
});
test("401,403,unsupported404,503,malformed and lost mode reads cannot enable writes", async () => {
  const results = [
    response({}, 401),
    response({ error: "denied" }, 403),
    response({}, 404),
    response({}, 503),
    response({ ok: true }),
    {
      ...response(mode()),
      json: async () => {
        throw new Error("truncated");
      },
    },
    new Error("lost"),
  ];
  for (const result of results) {
    const h = apiHarness(result),
      model = createSkillWorkspace(h.api, { requireProfile: true });
    model.readProfile();
    await flush();
    assert.equal(model.getSnapshot().profile.status, "error");
    assert.equal(model.getSnapshot().writeModeEnabled, false);
    assert.equal(await model.createSkill({ name: "alpha", description: "example" }), null);
    assert.equal(h.calls.length, 1);
    assert.equal(model.getSnapshot().rootUncertain, null);
  }
});
test("required mode blocks create, upsert, mkdir and save immediately before the first response", async () => {
  const h = harness();
  await h.load();
  for (const beforeRead of [true, false]) {
    if (!beforeRead) h.model.readProfile();
    assert.equal(await h.model.save("alpha", "SKILL.md"), false);
    assert.equal(await h.model.create("alpha", "x", "file"), false);
    assert.equal(await h.model.create("alpha", "d", "dir"), false);
    assert.equal(await h.model.createSkill({ name: "beta", description: "example" }), null);
  }
  assert.equal(h.writes.length + h.creates.length + h.dirs.length, 0);
  assert.equal(h.model.getSnapshot().drafts.values().next().value.content, "local draft");
});
test("read-only confirmation leaves reads/draft edits usable but dispatches no writes", async () => {
  const h = harness();
  await h.confirm(mode(false));
  await h.load();
  assert.equal(h.model.getSnapshot().profile.status, "ready");
  assert.equal(h.model.getSnapshot().writeModeEnabled, false);
  assert.equal(await h.model.save("alpha", "SKILL.md"), false);
  assert.equal(await h.model.createSkill({ name: "beta", description: "example" }), null);
  assert.equal(h.model.getSnapshot().drafts.values().next().value.dirty, true);
  assert.equal(h.writes.length + h.creates.length, 0);
});
test("write-enabled confirmation admits existing requests but does not alter server refusal semantics", async () => {
  const h = harness();
  await h.confirm();
  await h.load();
  const save = h.model.save("alpha", "SKILL.md");
  h.writes[0].reject(new ApiError(403, {}, "read-only"));
  assert.equal(await save, false);
  assert.equal(h.model.getSnapshot().drafts.values().next().value.dirty, true);
  assert.equal(h.model.getSnapshot().errors.get("alpha").message, "read-only");
  const root = h.model.createSkill({ name: "beta", description: "example" });
  h.creates[0].reject(new ApiError(403, {}, "read-only"));
  assert.equal(await root, null);
  assert.equal(h.model.getSnapshot().rootError.message, "read-only");
});
test("every refresh closes the write gate synchronously and only latest mode completion owns it", async () => {
  const h = harness();
  await h.confirm();
  await h.load();
  const cleanupOld = h.model.readProfile();
  await flush();
  assert.equal(h.model.getSnapshot().writeModeEnabled, false);
  assert.equal(await h.model.save("alpha", "SKILL.md"), false);
  const old = h.profiles.at(-1);
  h.model.readProfile();
  await flush();
  const fresh = h.profiles.at(-1);
  assert.equal(old.signal.aborted, true);
  cleanupOld();
  assert.equal(fresh.signal.aborted, false);
  fresh.resolve(mode(false));
  await flush();
  old.resolve(mode(true));
  await flush();
  assert.equal(h.model.getSnapshot().profile.writesEnabled, false);
  assert.equal(h.model.getSnapshot().writeModeEnabled, false);
});
test("stale failure/finally cannot erase a later valid mode; latest failure removes stale write access", async () => {
  const h = harness();
  h.model.readProfile();
  await flush();
  const old = h.profiles[0];
  await h.confirm();
  old.reject(new Error("late"));
  await flush();
  assert.equal(h.model.getSnapshot().writeModeEnabled, true);
  h.model.readProfile();
  await flush();
  h.profiles.at(-1).reject(new Error("latest failure"));
  await flush();
  assert.equal(h.model.getSnapshot().writeModeEnabled, false);
  assert.equal(h.model.getSnapshot().profile.error, "latest failure");
});
test("effect cleanup/replay retires hanging read owners and suppresses late mode enablement", async () => {
  const h = harness();
  const cleanup = h.model.readProfile();
  await flush();
  const old = h.profiles[0];
  cleanup();
  assert.equal(old.signal.aborted, true);
  assert.equal(h.model.getSnapshot().writeModeEnabled, false);
  h.model.readProfile();
  await flush();
  assert.equal(h.profiles.length, 2);
  old.resolve(mode(true));
  await flush();
  assert.equal(h.model.getSnapshot().profile.status, "loading");
  h.profiles[1].resolve(mode(false));
  await flush();
  assert.equal(h.model.getSnapshot().writeModeEnabled, false);
});
test("cleanup before the initial microtask avoids dispatch and leaves mode unconfirmed", async () => {
  const h = harness();
  h.model.readProfile()();
  await flush();
  assert.equal(h.profiles.length, 0);
  assert.equal(h.model.getSnapshot().profile.status, "unchecked");
});
test("mode refresh cannot clear root or file mutation uncertainty", async () => {
  for (const kind of ["root", "file"]) {
    const h = harness();
    await h.confirm();
    await h.load();
    const op =
      kind === "root"
        ? h.model.createSkill({ name: "beta", description: "example" })
        : h.model.save("alpha", "SKILL.md");
    (kind === "root" ? h.creates : h.writes)[0].reject(new ApiError(500, { retry: false }, "unknown"));
    await op;
    await h.confirm(mode(false));
    await h.confirm(mode(true));
    assert.equal(await h.model.save("alpha", "SKILL.md"), false);
    assert.equal(await h.model.createSkill({ name: "gamma", description: "example" }), null);
    assert(kind === "root" ? h.model.getSnapshot().rootUncertain : h.model.getSnapshot().uncertain.has("alpha"));
  }
});
test("mode refresh does not cancel an in-flight write or discard its receipt/newer draft", async () => {
  const h = harness();
  await h.confirm();
  await h.load();
  const op = h.model.save("alpha", "SKILL.md");
  await h.confirm(mode(false));
  h.model.edit("alpha", "SKILL.md", "newer local text");
  h.writes[0].resolve({ ok: true });
  assert.equal(await op, true);
  const draft = h.model.getSnapshot().drafts.values().next().value;
  assert.equal(draft.content, "newer local text");
  assert.equal(draft.dirty, true);
  assert.equal(h.model.getSnapshot().writeModeEnabled, false);
});
test("default Go/mock model behavior is unchanged and does not request the Python host endpoint", async () => {
  for (const options of [undefined, { requireProfile: false }]) {
    let calls = 0;
    const model = createSkillWorkspace(
      {
        skillWorkspaceProfile: () => {
          throw new Error("unexpected");
        },
        createSkill: async ({ name }) => {
          calls++;
          return { name };
        },
      },
      options,
    );
    model.readProfile()();
    await flush();
    assert.equal(model.getSnapshot().profile, null);
    assert.equal(model.getSnapshot().writeModeEnabled, true);
    assert.equal((await model.createSkill({ name: "alpha", description: "example" })).name, "alpha");
    assert.equal(calls, 1);
  }
});
test("actual page requests host mode only for non-mock Python and shares immediate mode gates", () => {
  assert.match(view, /requireProfile: RESTRICTED_SKILLS && !MOCK/);
  assert.match(view, /const rootWriteBlocked = !workspaceState.writeModeEnabled/);
  assert.match(view, /return !state.writeModeEnabled \|\| !!state.rootPending/);
  assert.match(view, /if \(!state.writeModeEnabled \|\| state.rootPending/);
  assert.match(
    section(view, "  async function createNewSkill()", "  // ── Inline input JSX helper"),
    /!state.writeModeEnabled/,
  );
  assert.match(
    section(view, "  async function commitCreate(", "  function cancelCreate("),
    /!workspace.getSnapshot\(\).writeModeEnabled/,
  );
  assert.match(view, /MOCK \? "Fixture-only Skills demo; no real filesystem host is contacted/);
  assert.match(view, /Local drafts remain available; browsing still depends on host access/);
  assert.match(view, /Read-only Skills host/);
  assert.match(view, /Host mode unavailable/);
  assert.match(view, /Mode refresh cannot clear unconfirmed operations/);
});
test("actual page refresh/cleanup owns manual rechecks and immediate model gating without a render", async () => {
  const h = harness();
  let effectCleanup;
  const context = vm.createContext({
    workspace: h.model,
    React: {
      useRef: (current) => ({ current }),
      useCallback: (fn) => fn,
      useEffect: (fn) => {
        effectCleanup = fn();
      },
    },
  });
  const hooks = section(view, "  const profileCleanup =", "\n  // inline create");
  vm.runInContext(stripTypeScriptTypes(`${hooks}\nglobalThis.refresh=refreshProfile;`), context);
  await flush();
  h.profiles[0].resolve(mode(true));
  await flush();
  context.refresh();
  assert.equal(h.model.getSnapshot().writeModeEnabled, false);
  await flush();
  const request = h.profiles[1];
  effectCleanup();
  assert.equal(request.signal.aborted, true);
  request.resolve(mode(true));
  await flush();
  assert.equal(h.model.getSnapshot().writeModeEnabled, false);
});

test("a mode recheck does not consume an existing inline entry when its blur/Enter cannot write", () => {
  const owner = { skill: "alpha", inDir: "", kind: "file" };
  const context = vm.createContext({
    creatingRef: { current: owner },
    newEntryRef: { current: "keep-name" },
    workspace: { getSnapshot: () => ({ writeModeEnabled: false }) },
  });
  vm.runInContext(
    stripTypeScriptTypes(
      `${section(view, "  async function commitCreate(", "  function cancelCreate(")}\nglobalThis.commit=commitCreate;`,
    ),
    context,
  );
  return context.commit(owner).then(() => {
    assert.equal(context.creatingRef.current, owner);
    assert.equal(context.newEntryRef.current, "keep-name");
  });
});
test("missing optional profile adapter fails closed rather than inheriting Go write availability", async () => {
  const model = createSkillWorkspace({}, { requireProfile: true });
  model.readProfile();
  await flush();
  assert.equal(model.getSnapshot().profile.status, "error");
  assert.equal(model.getSnapshot().writeModeEnabled, false);
});

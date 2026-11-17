import { ApiError } from "../src/lib/mutation-contract.mjs";
import {
  confirmedSkillImport,
  confirmedSkillWorkspaceProfile,
  createSkillWorkspace,
  skillFileKey,
  validateSkillImportFile,
} from "../src/lib/skill-workspace.mjs";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { stripTypeScriptTypes } from "node:module";
import test from "node:test";
import vm from "node:vm";

const authSource = await readFile(new URL("../src/lib/auth.ts", import.meta.url), "utf8");
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
  return { resolve, reject, promise };
}
async function flush() {
  for (let n = 0; n < 15; n++) await Promise.resolve();
}
const archive = (name = "bundle.zip", body = "synthetic inert bytes") =>
  new File([body], name, { type: "application/zip" });
const receipt = (name = "manifest-name", files = 2) => ({ name, files });
const mode = () => ({
  profile: "skills-workspace",
  writes_enabled: true,
  deletes_enabled: true,
  imports_enabled: true,
});
function harness(required = true) {
  const imports = [],
    creates = [],
    writes = [],
    dirs = [],
    deletes = [],
    reads = [],
    modes = [];
  const api = Object.fromEntries(
    [
      ["importSkill", imports],
      ["createSkill", creates],
      ["writeSkillFile", writes],
      ["createSkillDir", dirs],
      ["deleteSkillPath", deletes],
      ["readSkillFile", reads],
      ["skillWorkspaceProfile", modes],
    ].map(([key, calls]) => [
      key,
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
    imports,
    creates,
    writes,
    dirs,
    deletes,
    reads,
    modes,
    confirm: async (value = mode()) => {
      model.readProfile();
      await flush();
      modes.at(-1).resolve(value);
      await flush();
    },
    load: async () => {
      model.read("alpha", "x");
      await flush();
      reads.at(-1).resolve("original");
      await flush();
    },
    draft: () => model.getSnapshot().drafts.get(skillFileKey("alpha", "x")),
  };
}
const response = (value = receipt(), status = 201) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => value,
});
function apiHarness(result = response(), mock = false) {
  const calls = [],
    removed = [];
  const context = vm.createContext({
    ApiError,
    confirmedSkillImport,
    validateSkillImportFile,
    File,
    FormData,
    MOCK: mock,
    getToken: () => "synthetic-token",
    window: { location: { href: "before" } },
    document: { cookie: "before" },
    localStorage: { removeItem: (key) => removed.push(key) },
    fetch: async (...args) => {
      calls.push(args);
      if (result instanceof Error) throw result;
      return result;
    },
  });
  vm.runInContext(stripTypeScriptTypes(authSource.replaceAll("export ", "")), context);
  vm.runInContext(
    stripTypeScriptTypes(`globalThis.api={${section(apiSource, "  importSkill:", "  // uploadSkill installs")}};`),
    context,
  );
  return { api: context.api, calls, context, removed };
}
test("profile accepts only known boolean optional capabilities and never infers imports from write/delete", () => {
  for (const value of [
    { profile: "skills-workspace", writes_enabled: false },
    { profile: "skills-workspace", writes_enabled: true },
    { profile: "skills-workspace", writes_enabled: true, deletes_enabled: true },
    { profile: "skills-workspace", writes_enabled: true, imports_enabled: true },
    mode(),
    { ...mode(), imports_enabled: false },
  ])
    assert.deepEqual(confirmedSkillWorkspaceProfile(value), value);
  for (const value of [
    { ...mode(), writes_enabled: false },
    { ...mode(), imports_enabled: "true" },
    { ...mode(), imports_enabled: 1 },
    { ...mode(), imports_enabled: null },
    { ...mode(), extra: true },
    { profile: "skills-workspace", imports_enabled: true },
  ])
    assert.throws(() => confirmedSkillWorkspaceProfile(value));
});
test("receipt is exact201 shape with manifest-derived name and regular-file count 1..128", () => {
  for (const count of [1, 128]) assert.deepEqual(confirmedSkillImport(receipt("技能", count)), receipt("技能", count));
  for (const value of [
    null,
    [],
    {},
    { ok: true },
    { name: "bad/name", files: 1 },
    { name: "Bad", files: 1 },
    ...[0, -1, 129, 1.5, Infinity, "1", null].map((files) => receipt("valid", files)),
    { ...receipt(), unknown: true },
    { ...receipt(), pending: false },
    { ...receipt(), retry: false },
  ])
    assert.throws(
      () => confirmedSkillImport(value),
      (e) => e instanceof ApiError && e.unknown && e.retry === false,
    );
});
test("preflight requires native nonempty bounded File and safe filename metadata, not ZIP content proof", () => {
  const file = archive("资料.ZIP");
  assert.equal(validateSkillImportFile(file), file);
  assert.equal(validateSkillImportFile(archive("limit.zip", new Uint8Array(1048576))).size, 1048576);
  for (const input of [
    null,
    undefined,
    {},
    { name: "fake.zip", size: 20 },
    new Blob(["x"]),
    archive("empty.zip", ""),
    archive("big.zip", new Uint8Array(1048577)),
    ...[
      "",
      "readme",
      "bundle.tar",
      "../bad.zip",
      "x/y.zip",
      "x\\y.zip",
      'bad".zip',
      "bad\n.zip",
      "bad\u007f.zip",
      `${"界".repeat(85)}.zip`,
    ].map((name) => archive(name)),
  ])
    assert.throws(() => validateSkillImportFile(input));
});
test("actual multipart API uses one file/no query/no JSON Content-Type and validates manifest name independent of filename", async () => {
  const h = apiHarness(),
    file = archive("different-filename.zip");
  assert.deepEqual(JSON.parse(JSON.stringify(await h.api.importSkill(file))), receipt());
  const [path, init] = h.calls[0];
  assert.equal(path, "/api/skills/upload");
  assert.equal(init.method, "POST");
  assert.equal(init.headers.Authorization, "Bearer synthetic-token");
  assert.equal(init.headers["Content-Type"], undefined);
  assert.deepEqual([...init.body.keys()], ["file"]);
  const sent = init.body.get("file");
  assert.equal(sent.name, file.name);
  assert.equal(await sent.text(), await file.text());
  assert.equal(h.calls.length, 1);
});
test("actual API rejects invalid File before dispatch and MOCK reports only a truthful fixture acknowledgement", async () => {
  const h = apiHarness();
  for (const value of [undefined, null, { name: "fake.zip", size: 1 }, archive("empty.zip", "")])
    await assert.rejects(h.api.importSkill(value));
  assert.equal(h.calls.length, 0);
  const fixture = apiHarness(null, true);
  assert.deepEqual(JSON.parse(JSON.stringify(await fixture.api.importSkill(archive()))), {
    name: "demo-import",
    files: 1,
  });
  assert.equal(fixture.calls.length, 0);
});
test("actual API marks network/truncated/malformed/unexpected successful responses unknown without retry", async () => {
  const values = [
    new Error("lost"),
    response({}),
    response(receipt(), 200),
    response(receipt(), 202),
    response({}, 204),
    {
      ...response(),
      json: async () => {
        throw new Error("truncated");
      },
    },
    {
      ...response({}, 403),
      json: async () => {
        throw new Error("bad JSON");
      },
    },
  ];
  for (const value of values) {
    const h = apiHarness(value);
    await assert.rejects(h.api.importSkill(archive()), (e) => e instanceof ApiError && e.unknown && e.retry === false);
    assert.equal(h.calls.length, 1);
  }
});
test("known error DTO semantics survive multipart API and401 follows existing sign-in behavior", async () => {
  for (const status of [400, 403, 409, 413, 500]) {
    const h = apiHarness(
      response({ error: "refused", ...(status === 500 ? { unknown: true, retry: false } : {}) }, status),
    );
    await assert.rejects(
      h.api.importSkill(archive()),
      (e) => e instanceof ApiError && e.status === status && e.message === "refused" && (status !== 500 || e.unknown),
    );
  }
  const h = apiHarness(response({}, 401));
  await assert.rejects(h.api.importSkill(archive()), (e) => e instanceof ApiError && e.status === 401 && !e.unknown);
  assert.deepEqual(h.removed, ["artex_token"]);
  assert.equal(h.context.window.location.href, "/login");
  assert.match(h.context.document.cookie, /max-age=0/);
});
test("legacy/unavailable/loading/invalid capability never admits Python import", async () => {
  const h = harness(),
    file = archive();
  assert.equal(await h.model.importArchive(file), null);
  for (const value of [
    { profile: "skills-workspace", writes_enabled: true },
    { profile: "skills-workspace", writes_enabled: true, deletes_enabled: true },
    { ...mode(), imports_enabled: false },
    { ...mode(), writes_enabled: false },
  ]) {
    await h.confirm(value);
    assert.equal(await h.model.importArchive(file), null);
  }
  h.model.readProfile();
  await flush();
  assert.equal(await h.model.importArchive(file), null);
  h.modes.at(-1).reject(new ApiError(404, {}, "unsupported"));
  await flush();
  assert.equal(await h.model.importArchive(file), null);
  assert.equal(h.imports.length, 0);
});
test("mode ownership/recheck retires permission without changing an admitted import outcome", async () => {
  const h = harness();
  await h.confirm();
  const file = archive(),
    pending = h.model.importArchive(file);
  h.model.readProfile();
  await flush();
  assert.equal(h.model.getSnapshot().importModeEnabled, false);
  assert.equal(await h.model.importArchive(archive("later.zip")), null);
  h.modes.at(-1).resolve({ ...mode(), imports_enabled: false });
  await flush();
  h.imports[0].resolve(receipt());
  assert.deepEqual(await pending, receipt());
  assert.equal(h.model.getSnapshot().importModeEnabled, false);
});
test("preflight refusal retains file/error without latching or dispatching", async () => {
  const h = harness();
  await h.confirm();
  const file = archive("empty.zip", "");
  assert.equal(await h.model.importArchive(file), null);
  assert.equal(h.imports.length, 0);
  assert.equal(h.model.getSnapshot().importError.file, file);
  assert.equal(h.model.getSnapshot().importUncertain, null);
});
test("immediate import fence blocks repeat and every tracked mutation family across skills", async () => {
  const h = harness();
  await h.confirm();
  await h.load();
  h.model.edit("alpha", "x", "keep dirty draft");
  const file = archive(),
    p = h.model.importArchive(file);
  assert.equal(h.imports[0].args[0], file);
  assert.equal(await h.model.importArchive(archive("other.zip")), null);
  assert.equal(await h.model.createSkill({ name: "beta", description: "new" }), null);
  assert.equal(await h.model.save("alpha", "x"), false);
  assert.equal(await h.model.create("beta", "new", "file"), false);
  assert.equal(await h.model.create("beta", "dir", "dir"), false);
  assert.equal(await h.model.remove("beta", "x"), false);
  assert.equal(h.imports.length, 1);
  assert.equal(h.writes.length + h.dirs.length + h.deletes.length + h.creates.length, 0);
  h.imports[0].resolve(receipt());
  assert.deepEqual(await p, receipt());
  assert.equal(h.draft().content, "keep dirty draft");
  assert.equal(h.draft().dirty, true);
});
test("each tracked prior mutation blocks import until settled", async () => {
  for (const kind of ["root", "save", "file", "dir", "delete"]) {
    const h = harness();
    await h.confirm();
    await h.load();
    if (kind === "save") h.model.edit("alpha", "x", "edited");
    const ops = {
      root: () => h.model.createSkill({ name: "beta", description: "new" }),
      save: () => h.model.save("alpha", "x"),
      file: () => h.model.create("alpha", "y", "file"),
      dir: () => h.model.create("alpha", "dir", "dir"),
      delete: () => h.model.remove("alpha", "x"),
    };
    const lists = { root: h.creates, save: h.writes, file: h.writes, dir: h.dirs, delete: h.deletes };
    const replies = {
      root: { name: "beta" },
      save: { ok: true },
      file: { ok: true },
      dir: { dir: "dir" },
      delete: { deleted: "x" },
    };
    const p = ops[kind]();
    assert.equal(await h.model.importArchive(archive()), null);
    assert.equal(h.imports.length, 0);
    lists[kind][0].resolve(replies[kind]);
    await p;
  }
});
test("known400/401/403/409/413 refusal preserves file/draft and allows only an explicit later attempt", async () => {
  for (const status of [400, 401, 403, 409, 413]) {
    const h = harness();
    await h.confirm();
    await h.load();
    const file = archive(),
      p = h.model.importArchive(file);
    h.model.edit("alpha", "x", "later draft");
    h.imports[0].reject(new ApiError(status, {}, "refused"));
    assert.equal(await p, null);
    assert.equal(h.model.getSnapshot().importUncertain, null);
    assert.equal(h.model.getSnapshot().importError.file, file);
    assert.equal(h.draft().content, "later draft");
    assert.equal(h.imports.length, 1);
    const second = h.model.importArchive(file);
    h.imports[1].resolve(receipt());
    assert.deepEqual(await second, receipt());
  }
});
test("every ambiguous post-dispatch outcome globally latches across mode refresh without invented installed name", async () => {
  for (const error of [
    new Error("unexpected adapter failure"),
    new ApiError(0, { unknown: true }, "lost"),
    new ApiError(500, {}, "failed"),
    new ApiError(503, {}, "unavailable"),
    new ApiError(409, { retry: false }, "unknown"),
    null,
  ]) {
    const h = harness();
    await h.confirm();
    await h.load();
    const file = archive("original.zip"),
      p = h.model.importArchive(file);
    if (error) h.imports[0].reject(error);
    else h.imports[0].resolve({ name: "bad", files: 0 });
    assert.equal(await p, null);
    const unknown = h.model.getSnapshot().importUncertain;
    assert.equal(unknown.archiveName, "original.zip");
    assert.equal("name" in unknown, false);
    await h.confirm();
    assert.equal(h.model.getSnapshot().importUncertain, unknown);
    h.model.edit("alpha", "x", "retained");
    assert.equal(await h.model.importArchive(archive("renamed.zip")), null);
    assert.equal(await h.model.createSkill({ name: "beta", description: "new" }), null);
    assert.equal(await h.model.save("alpha", "x"), false);
    assert.equal(await h.model.create("beta", "x", "file"), false);
    assert.equal(await h.model.create("beta", "d", "dir"), false);
    assert.equal(await h.model.remove("beta", "x"), false);
    assert.equal(h.imports.length, 1);
    assert.equal(h.draft().content, "retained");
  }
});
function pageHarness() {
  const h = harness(),
    file = archive();
  const owner = { open: true };
  const state = { file, open: true, busy: false, loads: 0, notices: [] };
  const context = vm.createContext({
    workspace: h.model,
    RESTRICTED_SKILLS: true,
    MOCK: false,
    importDialog: { current: owner },
    importSession: owner,
    importFileRef: { current: file },
    importRequest: { current: null },
    importAlive: { current: true },
    setImportOpenState: (x) => {
      state.open = x;
    },
    setImportFile: (x) => {
      state.file = x;
    },
    setImporting: (x) => {
      state.busy = x;
    },
    load: () => {
      state.loads++;
    },
    toast: {
      success: (x) => {
        state.notices.push(x);
      },
    },
  });
  const code =
    section(view, "  function rootWritesSuspended()", "  const [fileRetry") +
    section(view, "  function setImportOpen(", "  // skill detail — MCP edit state");
  vm.runInContext(
    stripTypeScriptTypes(`${code}\nglobalThis.actions={setImportOpen,pickImportFile,importArchive};`),
    context,
  );
  const pick = (next) => {
    const event = { target: { files: [next], value: "selected" } };
    context.actions.pickImportFile(event, context.importDialog.current);
    assert.equal(event.target.value, "");
  };
  return { ...h, context, state, ...context.actions, pick };
}
test("actual Sheet uses immediate latest File and same-render owner; unchanged ack clears only original selection/session", async () => {
  const h = pageHarness();
  await h.confirm();
  const current = archive("latest.zip");
  h.pick(current);
  const p = h.importArchive();
  await h.importArchive();
  assert.equal(h.imports.length, 1);
  assert.equal(h.imports[0].args[0], current);
  h.imports[0].resolve(receipt());
  await p;
  assert.equal(h.state.file, null);
  assert.equal(h.state.open, false);
  assert.equal(h.state.loads, 1);
  assert.equal(h.state.busy, false);
});
test("later File and closed/reopened session survive old acknowledged completion without selection/navigation changes", async () => {
  for (const edit of [true, false]) {
    const h = pageHarness();
    await h.confirm();
    const old = h.state.file,
      p = h.importArchive();
    h.setImportOpen(false);
    h.setImportOpen(true);
    const later = archive("later.zip");
    if (edit) h.pick(later);
    h.imports[0].resolve(receipt());
    await p;
    assert.equal(h.state.file, edit ? later : old);
    assert.equal(h.state.open, true);
    assert.equal(h.state.loads, 1);
  }
  assert.doesNotMatch(
    section(view, "  async function importArchive()", "  // skill detail — MCP edit state"),
    /setSelected|setNewDraft|overwrite/,
  );
});
test("known failure/unknown preserve selected File and matching dialog without auto-overwrite or automatic retry", async () => {
  for (const status of [409, 500]) {
    const h = pageHarness();
    await h.confirm();
    const file = h.state.file,
      p = h.importArchive();
    h.imports[0].reject(new ApiError(status, {}, "already exists"));
    await p;
    assert.equal(h.state.file, file);
    assert.equal(h.state.open, true);
    assert.equal(h.state.loads, 0);
    assert.equal(h.imports.length, 1);
    assert.equal(h.state.busy, false);
  }
});
test("old picker/dialog callbacks cannot take ownership of a reopened session", async () => {
  const h = pageHarness();
  await h.confirm();
  const old = h.context.importSession;
  h.setImportOpen(false);
  h.setImportOpen(true);
  const event = { target: { files: [archive("stale.zip")], value: "chosen" } };
  h.pickImportFile(event, old);
  assert.equal(h.state.file.name, "bundle.zip");
  await h.importArchive();
  assert.equal(h.imports.length, 0); // old rendered callback
});
test("commit-phase unmount retirement suppresses import completion UI effects", async () => {
  const h = pageHarness();
  await h.confirm();
  const p = h.importArchive();
  let cleanup;
  h.context.React = {
    useLayoutEffect: (fn) => {
      cleanup = fn();
    },
  };
  vm.runInContext(
    section(view, "  React.useLayoutEffect(() => {\n    importAlive", "  function setImportOpen("),
    h.context,
  );
  cleanup();
  h.imports[0].resolve(receipt());
  await p;
  assert.equal(h.state.loads, 0);
  assert.equal(h.state.notices.length, 0);
  assert(h.state.file);
  assert.equal(h.context.importRequest.current, null);
});
test("actual import modal names original uncertainty and attributes errors to exact selected File", () => {
  const modal = section(view, "      {/* Python-only create-only archive import", "      {/* ── New skill dialog");
  assert.match(modal, /workspaceState.importError\?\.file === importFile/);
  assert.match(modal, /workspaceState.importUncertain.archiveName/);
  assert.match(modal, /role="alert"/);
  assert.match(modal, /!workspaceState.importModeEnabled/);
  assert.match(modal, /Fixture-only acknowledgement; no archive is installed or inspected/);
  assert.match(modal, /derives the installed name from SKILL.md, not this filename/);
  assert.match(view, /Import of archive .*importUncertain.archiveName/);
  assert.match(view, /workspaceState.importError && !workspaceState.importUncertain && <p role="alert"/);
  assert.match(view, /Could not import archive .*importError.archiveName/);
});

test("existing Go overwrite-capable API and upload callbacks remain byte-identical", () => {
  const digest = (value) => createHash("sha256").update(value).digest("hex");
  assert.equal(
    digest(section(apiSource, "  // uploadSkill installs", "  deleteSkill:")),
    "d70359a63d3d127570ae7044dbda573d24715763a9ab8ae57b66015889976660",
  );
  assert.equal(
    digest(section(view, "  async function uploadZip(", "  React.useEffect(() => {\n    if (creating)")),
    "f44b055b5892a854775b373541b18565d3fff26dcfebfa2d7d3d9a56afedcd33",
  );
  assert.match(view, /!RESTRICTED_SKILLS && <input\s+ref=\{uploadRef\}/);
  assert.match(view, /RESTRICTED_SKILLS \? setImportOpen\(true\) : uploadRef.current\?\.click\(\)/);
});
test("new File chosen in the same open Sheet survives an earlier success", async () => {
  const h = pageHarness();
  await h.confirm();
  const p = h.importArchive();
  const next = archive("newer.zip");
  h.pick(next);
  h.imports[0].resolve(receipt());
  await p;
  assert.equal(h.state.file, next);
  assert.equal(h.state.open, true);
  assert.equal(h.state.loads, 1);
});
test("existing root/file/delete uncertainty prevents import even after capability refresh", async () => {
  for (const kind of ["root", "file", "delete"]) {
    const h = harness();
    await h.confirm();
    await h.load();
    const ops = {
      root: () => h.model.createSkill({ name: "beta", description: "new" }),
      file: () => h.model.create("alpha", "y", "file"),
      delete: () => h.model.remove("alpha", "x"),
    };
    const lists = { root: h.creates, file: h.writes, delete: h.deletes };
    const p = ops[kind]();
    lists[kind][0].reject(new ApiError(500, { unknown: true }, "unknown"));
    await p;
    await h.confirm();
    assert.equal(await h.model.importArchive(archive()), null);
    assert.equal(h.imports.length, 0);
  }
});

test("non-NFC201 import identities are rejected by actual API and latch model uncertainty", async () => {
  for (const name of ["Å", "K"]) {
    assert.notEqual(name.normalize("NFC"), name);
    assert.throws(
      () => confirmedSkillImport(receipt(name, 1)),
      (e) => e instanceof ApiError && e.unknown,
    );
    const h = apiHarness(response(receipt(name, 1)));
    const model = createSkillWorkspace(
      { ...h.api, skillWorkspaceProfile: async () => mode() },
      { requireProfile: true },
    );
    model.readProfile();
    await flush();
    assert.equal(await model.importArchive(archive("source.zip")), null);
    assert.equal(model.getSnapshot().importUncertain.archiveName, "source.zip");
    assert.equal(await model.importArchive(archive("different.zip")), null);
    assert.equal(h.calls.length, 1);
  }
  assert.deepEqual(confirmedSkillImport(receipt("Å", 1)), receipt("Å", 1));
  assert.match(view, /MOCK \? "ZIP import also uses only fixture acknowledgements, without host discovery\."/);
});

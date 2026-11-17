import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { stripTypeScriptTypes } from "node:module";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("../src/app/(main)/system/mcp/page.tsx", import.meta.url), "utf8");
const prelude = source.slice(source.indexOf("type Transport ="), source.indexOf("export default function MCPPage"));
const body = source
  .slice(source.indexOf("export default function MCPPage"), source.indexOf("  function renderForm()"))
  .replace("export default ", "");
const plain = (x) => JSON.parse(JSON.stringify(x));
const server = (id = 1, extra = {}) => ({
  id,
  name: `Server ${id}`,
  transport: "http",
  url: `https://fixture-${id}.test/mcp`,
  args: [],
  env: {},
  enabled: true,
  ...extra,
});
const tool = (name) => ({ name, description: `${name} description` });
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((a, b) => {
    resolve = a;
    reject = b;
  });
  return { promise, resolve, reject };
};
const flush = async () => {
  for (let i = 0; i < 12; i++) await Promise.resolve();
};
function harness() {
  const slots = [],
    effects = [],
    requests = [],
    notices = [],
    writes = [];
  let cursor = 0,
    dirty = false,
    current;
  const depsEqual = (a, b) => a && b && a.length === b.length && a.every((x, i) => Object.is(x, b[i]));
  const React = {
    useState(initial) {
      const i = cursor++;
      if (!slots[i]) slots[i] = { value: typeof initial === "function" ? initial() : initial };
      return [
        slots[i].value,
        (next) => {
          const value = typeof next === "function" ? next(slots[i].value) : next;
          writes.push(i);
          if (!Object.is(value, slots[i].value)) {
            slots[i].value = value;
            dirty = true;
          }
        },
      ];
    },
    useRef(value) {
      const i = cursor++;
      slots[i] ??= { current: value };
      return slots[i];
    },
    useMemo(fn, deps) {
      const i = cursor++;
      if (!depsEqual(slots[i]?.deps, deps)) slots[i] = { deps, value: fn() };
      return slots[i].value;
    },
    useCallback(fn, deps) {
      return React.useMemo(() => fn, deps);
    },
    useEffect(setup, deps) {
      const i = cursor++;
      if (!depsEqual(slots[i]?.deps, deps))
        effects.push(() => {
          slots[i]?.cleanup?.();
          slots[i] = { deps, setup, cleanup: setup(), effect: true };
        });
    },
  };
  React.useLayoutEffect = React.useEffect;
  const methods = [
    "agents",
    "mcpServers",
    "resourceVisibility",
    "mcpTools",
    "saveMcpServer",
    "refreshMcpServer",
    "deleteMcpServer",
    "toggleVisibility",
  ];
  const context = vm.createContext({
    React,
    api: Object.fromEntries(
      methods.map((method) => [
        method,
        (...args) => {
          const d = deferred();
          requests.push({ method, args, ...d });
          return d.promise;
        },
      ]),
    ),
    toast: Object.fromEntries(["success", "error"].map((kind) => [kind, (message) => notices.push({ kind, message })])),
  });
  vm.runInContext(
    stripTypeScriptTypes(
      `${prelude}\n${body}\nreturn {servers,agents,visibility,open,editing,form,saving,tools,toolsLoading,refreshing,setF,setOpen,openAdd,openEdit,load,loadTools,saveForm,refreshTools,removeServer,toggleEnabled,toggleVisibility};}\nglobalThis.view=MCPPage;`,
    ),
    context,
  );
  function render() {
    let passes = 0;
    do {
      dirty = false;
      cursor = 0;
      current = context.view();
      while (effects.length) effects.shift()();
      assert(++passes < 20);
    } while (dirty);
    return current;
  }
  render();
  return {
    requests,
    notices,
    writes,
    context,
    render,
    get current() {
      return current;
    },
    find(method) {
      return requests.filter((r) => r.method === method);
    },
    async settle(req, value, error) {
      if (error) req.reject(error);
      else req.resolve(value);
      await flush();
      render();
    },
    async ready() {
      await this.settle(this.find("agents")[0], [{ id: "a", key: "a", name: "Agent" }]);
      await this.settle(this.find("mcpServers")[0], [server(1), server(2)]);
      for (const r of this.find("resourceVisibility")) r.resolve([]);
      await flush();
      render();
      return this;
    },
    cleanup() {
      for (const slot of slots) if (slot?.effect) slot.cleanup?.();
    },
    replay() {
      for (const slot of slots) if (slot?.effect) slot.cleanup = slot.setup();
      render();
    },
  };
}
async function ready() {
  return harness().ready();
}

test("old tool success/failure cannot replace B tools or release B loading", async () => {
  for (const fails of [false, true]) {
    const h = await ready();
    h.current.openEdit(server(1));
    h.render();
    const old = h.find("mcpTools")[0];
    h.current.openEdit(server(2));
    h.render();
    const fresh = h.find("mcpTools")[1];
    await h.settle(old, [tool("old")], fails ? new Error("old read") : undefined);
    assert.equal(h.current.editing.id, 2);
    assert.deepEqual(plain(h.current.tools), []);
    assert.equal(h.current.toolsLoading, true);
    await h.settle(fresh, [tool("fresh")]);
    assert.equal(h.current.tools[0].name, "fresh");
    assert.equal(h.current.toolsLoading, false);
  }
});
test("older cached tool read cannot overwrite explicit refresh", async () => {
  const h = await ready();
  h.current.openEdit(server(1));
  h.render();
  const old = h.find("mcpTools")[0];
  const op = h.current.refreshTools();
  h.render();
  await h.settle(h.find("refreshMcpServer")[0], [tool("discovered")]);
  await op;
  await h.settle(old, [tool("cached")]);
  assert.equal(h.current.tools[0].name, "discovered");
});
test("old create cannot close or clear busy on a newly opened create", async () => {
  const h = await ready();
  h.current.openAdd();
  h.current.setF({ name: "First", command: "synthetic" });
  h.render();
  const old = h.current.saveForm();
  h.current.setOpen(false);
  h.current.openAdd();
  h.current.setF({ name: "Second", command: "synthetic" });
  h.render();
  const fresh = h.current.saveForm();
  h.render();
  await h.settle(h.find("saveMcpServer")[0], { id: 3 });
  await old;
  assert.equal(h.current.open, true);
  assert.equal(h.current.saving, true);
  assert.equal(h.current.form.name, "Second");
  await h.settle(h.find("saveMcpServer")[1], { id: 4 });
  await fresh;
  assert.equal(h.current.open, false);
});
test("a delete for A cannot close the currently selected B drawer", async () => {
  const h = await ready();
  const op = h.current.removeServer(server(1));
  h.current.openEdit(server(2));
  h.render();
  await h.settle(h.find("deleteMcpServer")[0], { deleted: 1 });
  await op;
  assert.equal(h.current.open, true);
  assert.equal(h.current.editing.id, 2);
});
test("same-turn edits and duplicate save use one latest captured payload", async () => {
  const h = await ready();
  h.current.openEdit(server(1));
  h.render();
  const save = h.current.saveForm;
  h.current.setF({ name: "Latest", url: "https://latest.test/mcp" });
  const op = save();
  void save();
  assert.equal(h.find("saveMcpServer").length, 1);
  assert.equal(h.find("saveMcpServer")[0].args[0].name, "Latest");
  await h.settle(h.find("saveMcpServer")[0], { id: 1 });
  await op;
});
test("successful enabled toggle cannot be undone by later save from an open draft", async () => {
  const h = await ready();
  h.current.openEdit(server(1));
  h.render();
  h.current.setF({ name: "Edited" });
  h.render();
  const toggle = h.current.toggleEnabled(server(1));
  await h.settle(h.find("saveMcpServer")[0], { id: 1 });
  await toggle;
  const save = h.current.saveForm();
  assert.equal(h.find("saveMcpServer")[1].args[0].enabled, false);
  assert.equal(h.find("saveMcpServer")[1].args[0].name, "Edited");
  await h.settle(h.find("saveMcpServer")[1], { id: 1 });
  await save;
});
test("out-of-order catalogs and visibility reads cannot replace the latest snapshot", async () => {
  const h = await ready();
  h.current.load();
  h.current.load();
  const reads = h.find("mcpServers");
  await h.settle(reads[2], [server(2, { name: "Newest" })]);
  const latestVisibility = h.find("resourceVisibility").at(-1);
  await h.settle(latestVisibility, ["a"]);
  await h.settle(reads[1], [server(1, { name: "Stale" })]);
  assert.deepEqual(plain(h.current.servers.map((s) => s.name)), ["Newest"]);
  assert.deepEqual(plain(h.current.visibility[2]), ["a"]);
});
test("save and remove for the same record cannot overlap before busy UI commits", async () => {
  const h = await ready();
  h.current.openEdit(server(1));
  h.render();
  const op = h.current.saveForm();
  void h.current.removeServer(server(1));
  void h.current.toggleEnabled(server(1));
  void h.current.refreshTools();
  assert.equal(h.find("saveMcpServer").length, 1);
  assert.equal(h.find("deleteMcpServer").length, 0);
  assert.equal(h.find("refreshMcpServer").length, 0);
  await h.settle(h.find("saveMcpServer")[0], { id: 1 });
  await op;
});
test("unmount retires all pending editor and metadata callbacks", async () => {
  const h = await ready();
  h.current.openEdit(server(1));
  h.render();
  const save = h.current.saveForm();
  h.current.load();
  h.cleanup();
  const before = h.writes.length;
  for (const r of h.requests) {
    if (r.method === "saveMcpServer") r.resolve({ id: 1 });
    if (r.method === "mcpTools") r.resolve([tool("late")]);
    if (r.method === "mcpServers") r.resolve([server(9)]);
    if (r.method === "agents") r.resolve([]);
    if (r.method === "resourceVisibility") r.resolve([]);
  }
  await save;
  await flush();
  assert.equal(h.writes.length, before);
  assert.equal(h.notices.length, 0);
});

test("create draft freezes during save, closes on acknowledgement, and stale handlers cannot create twice", async () => {
  const h = await ready();
  h.current.openAdd();
  h.current.setF({ name: "Created", command: "synthetic" });
  h.render();
  const actions = h.current;
  const op = actions.saveForm();
  actions.setF({ name: "Ignored", command: "changed" });
  h.render();
  assert.equal(h.current.form.name, "Created");
  assert.equal(h.current.form.command, "synthetic");
  await h.settle(h.find("saveMcpServer")[0], { id: 3 });
  await op;
  actions.setF({ name: "Old handler" });
  await actions.saveForm();
  assert.equal(h.find("saveMcpServer").length, 1);
  assert.equal(h.current.open, false);
});
test("existing save retains later draft edits and next save updates the same record", async () => {
  const h = await ready();
  h.current.openEdit(server(1));
  h.render();
  const op = h.current.saveForm();
  h.current.setF({ name: "Later", env: "Synthetic=only-fixture" });
  h.render();
  await h.settle(h.find("saveMcpServer")[0], { id: 1 });
  await op;
  assert.equal(h.current.open, true);
  assert.equal(h.current.form.name, "Later");
  assert.match(h.notices.at(-1).message, /newer edits remain unsaved/);
  const next = h.current.saveForm();
  assert.equal(h.find("saveMcpServer")[1].args[0].id, 1);
  assert.equal(h.find("saveMcpServer")[1].args[0].name, "Later");
  await h.settle(h.find("saveMcpServer")[1], { id: 1 });
  await next;
});
test("known unavailable save failure retains edits and unlocks only the current operation", async () => {
  const h = await ready();
  h.current.openEdit(server(1));
  h.render();
  const op = h.current.saveForm();
  h.current.setF({ name: "Keep" });
  h.render();
  await h.settle(h.find("saveMcpServer")[0], undefined, new Error("MCP catalog capability unavailable"));
  await op;
  assert.equal(h.current.form.name, "Keep");
  assert.equal(h.current.saving, false);
  assert.equal(h.current.open, true);
  assert.equal(h.find("saveMcpServer").length, 1);
  assert.equal(h.notices.at(-1).message, "Save failed: MCP catalog capability unavailable");
  const retry = h.current.saveForm();
  assert.equal(h.find("saveMcpServer").length, 2);
  await h.settle(h.find("saveMcpServer")[1], { id: 1 });
  await retry;
});
test("old save failure is silent in a replacement drawer and does not release its save", async () => {
  const h = await ready();
  h.current.openEdit(server(1));
  h.render();
  const old = h.current.saveForm();
  h.current.openEdit(server(2));
  h.render();
  const fresh = h.current.saveForm();
  h.render();
  await h.settle(h.find("saveMcpServer")[0], undefined, new Error("old failure"));
  await old;
  assert.equal(h.current.editing.id, 2);
  assert.equal(h.current.saving, true);
  assert.equal(h.notices.length, 0);
  await h.settle(h.find("saveMcpServer")[1], { id: 2 });
  await fresh;
  assert.equal(h.current.saving, false);
});
test("closing and reopening the same ID retires cached reads even when the IDs repeat", async () => {
  const h = await ready();
  h.current.openEdit(server(1));
  h.render();
  const old = h.find("mcpTools")[0];
  h.current.setOpen(false);
  h.current.openEdit(server(1));
  h.render();
  const fresh = h.find("mcpTools")[1];
  await h.settle(old, [tool("old")]);
  assert.equal(h.current.toolsLoading, true);
  assert.deepEqual(plain(h.current.tools), []);
  await h.settle(fresh, [tool("fresh")]);
  assert.equal(h.current.tools[0].name, "fresh");
});
test("late refresh changes only its catalog row, not the new drawer or busy indicator", async () => {
  for (const fails of [false, true]) {
    const h = await ready();
    h.current.openEdit(server(1));
    h.render();
    const old = h.current.refreshTools();
    h.current.openEdit(server(2));
    h.render();
    const fresh = h.current.refreshTools();
    h.render();
    await h.settle(h.find("refreshMcpServer")[0], [tool("A")], fails ? new Error("old refresh") : undefined);
    await old;
    assert.equal(h.current.editing.id, 2);
    assert.equal(h.current.refreshing, true);
    assert.deepEqual(plain(h.current.tools), []);
    assert.equal(h.notices.length, 0);
    if (!fails) assert.deepEqual(plain(h.current.servers.find((s) => s.id === 1).tools), ["A"]);
    await h.settle(h.find("refreshMcpServer")[1], [tool("B")]);
    await fresh;
    assert.equal(h.current.tools[0].name, "B");
    assert.equal(h.current.refreshing, false);
  }
});
test("refresh unavailability releases its server gate and leaves a retryable draft", async () => {
  const h = await ready();
  h.current.openEdit(server(1));
  h.render();
  const op = h.current.refreshTools();
  await h.settle(h.find("refreshMcpServer")[0], undefined, new Error("Not found"));
  await op;
  assert.equal(h.current.refreshing, false);
  assert.equal(h.current.open, true);
  assert.equal(h.find("refreshMcpServer").length, 1);
  const retry = h.current.refreshTools();
  assert.equal(h.find("refreshMcpServer").length, 2);
  await h.settle(h.find("refreshMcpServer")[1], []);
  await retry;
});
test("duplicate delete/toggle/visibility callbacks coalesce while other server operations continue", async () => {
  for (const action of ["removeServer", "toggleEnabled", "toggleVisibility"]) {
    const h = await ready();
    const invoke = () =>
      action === "toggleVisibility" ? h.current[action](1, "a", "Agent") : h.current[action](server(1));
    const op = invoke();
    void invoke();
    void h.current.toggleEnabled(server(1));
    const method =
      action === "removeServer" ? "deleteMcpServer" : action === "toggleEnabled" ? "saveMcpServer" : "toggleVisibility";
    assert.equal(h.find(method).length, 1);
    const other = h.current.toggleEnabled(server(2));
    assert.equal(h.find("saveMcpServer").at(-1).args[0].id, 2);
    await h.settle(
      h.find(method)[0],
      action === "removeServer" ? { deleted: 1 } : action === "toggleEnabled" ? { id: 1 } : { ok: true },
    );
    await op;
    await h.settle(h.find("saveMcpServer").at(-1), { id: 2 });
    await other;
  }
});
test("visibility keeps its existing payload, confirmed toggle state and latest read ownership", async () => {
  const h = await ready();
  h.current.load();
  await h.settle(h.find("mcpServers").at(-1), [server(1), server(2)]);
  const old = h
    .find("resourceVisibility")
    .filter((r) => r.args[1] === 1)
    .at(-1);
  const first = h.current.toggleVisibility(1, "a", "Agent");
  assert.deepEqual(plain(h.find("toggleVisibility")[0].args), ["a", "mcp", 1, true]);
  await h.settle(h.find("toggleVisibility")[0], { ok: true });
  await first;
  await h.settle(old, []);
  assert.deepEqual(plain(h.current.visibility[1]), ["a"]);
  const second = h.current.toggleVisibility(1, "a", "Agent");
  assert.deepEqual(plain(h.find("toggleVisibility")[1].args), ["a", "mcp", 1, false]);
  await h.settle(h.find("toggleVisibility")[1], { ok: true });
  await second;
  assert.deepEqual(plain(h.current.visibility[1]), []);
});
test("failed mutation does not cancel pending catalog reads or fabricate metadata changes", async () => {
  const h = await ready();
  h.current.load();
  const pending = h.find("mcpServers").at(-1);
  const op = h.current.toggleEnabled(server(1));
  await h.settle(h.find("saveMcpServer")[0], undefined, new Error("unavailable"));
  await op;
  await h.settle(pending, [server(1, { name: "Fresh read" }), server(2)]);
  assert.equal(h.current.servers[0].name, "Fresh read");
  assert.equal(h.current.servers[0].enabled, true);
});
test("catalog refresh preserves current drafts and stale passed row data cannot overwrite new config", async () => {
  const h = await ready();
  h.current.openEdit(server(1));
  h.current.setF({ name: "Draft" });
  h.render();
  h.current.load();
  await h.settle(h.find("mcpServers").at(-1), [
    server(1, { name: "Server metadata", url: "https://new.test/mcp" }),
    server(2),
  ]);
  assert.equal(h.current.form.name, "Draft");
  const toggle = h.current.toggleEnabled(server(1));
  const payload = h.find("saveMcpServer")[0].args[0];
  assert.equal(payload.url, "https://new.test/mcp");
  assert.equal(payload.name, "Server metadata");
  await h.settle(h.find("saveMcpServer")[0], { id: 1 });
  await toggle;
  assert.equal(h.current.form.name, "Draft");
});
test("acknowledged deletion retires same-record handlers and prevents stale catalog resurrection", async () => {
  const h = await ready();
  h.current.openEdit(server(1));
  h.render();
  h.current.load();
  const oldRead = h.find("mcpServers").at(-1);
  const op = h.current.removeServer(server(1));
  void h.current.saveForm();
  h.current.openEdit(server(1));
  assert.equal(h.find("saveMcpServer").length, 0);
  await h.settle(h.find("deleteMcpServer")[0], { deleted: 1 });
  await op;
  await h.settle(oldRead, [server(1), server(2)]);
  assert.equal(h.current.open, false);
  assert.deepEqual(plain(h.current.servers.map((s) => s.id)), [2]);
  h.current.openEdit(server(1));
  await h.current.toggleEnabled(server(1));
  assert.equal(h.find("saveMcpServer").length, 0);
});
test("add mode resets tools and loading when an edit read is still pending", async () => {
  const h = await ready();
  h.current.openEdit(server(1));
  h.render();
  const read = h.find("mcpTools")[0];
  h.current.openAdd();
  h.render();
  assert.equal(h.current.toolsLoading, false);
  assert.equal(h.current.editing, null);
  await h.settle(read, [tool("old")]);
  assert.deepEqual(plain(h.current.tools), []);
});
test("stdio/http/sse saves preserve existing transport and environment payload rules", async () => {
  for (const transport of ["stdio", "http", "sse"]) {
    const h = await ready();
    h.current.openAdd();
    h.current.setF({
      name: " Fixture ",
      transport,
      command: " synthetic ",
      args: " one  two ",
      url: " https://fixture.test/mcp ",
      env: " KEY = VALUE\nHeader = synthetic=value\ninvalid",
      insecure: true,
    });
    h.render();
    const op = h.current.saveForm();
    const payload = h.find("saveMcpServer")[0].args[0];
    assert.equal(payload.name, "Fixture");
    assert.equal(payload.enabled, true);
    assert.equal("id" in payload, false);
    assert.deepEqual(plain(payload.env), { KEY: "VALUE", Header: "synthetic=value" });
    if (transport === "stdio") {
      assert.equal(payload.command, "synthetic");
      assert.deepEqual(plain(payload.args), ["one", "two"]);
      assert.equal(payload.insecure, false);
      assert.equal("url" in payload, false);
    } else {
      assert.equal(payload.url, "https://fixture.test/mcp");
      assert.equal(payload.command, "");
      assert.deepEqual(plain(payload.args), []);
      assert.equal(payload.insecure, true);
    }
    await h.settle(h.find("saveMcpServer")[0], { id: 3 });
    await op;
  }
});
test("validation creates no operation and corrected same-turn edits can submit", async () => {
  const h = await ready();
  h.current.openAdd();
  await h.current.saveForm();
  h.current.setF({ name: "Name" });
  await h.current.saveForm();
  h.current.setF({ transport: "http" });
  await h.current.saveForm();
  assert.equal(h.find("saveMcpServer").length, 0);
  h.current.setF({ url: "https://fixture.test/mcp" });
  const op = h.current.saveForm();
  assert.equal(h.find("saveMcpServer").length, 1);
  await h.settle(h.find("saveMcpServer")[0], { id: 3 });
  await op;
});
test("inactive actions cannot dispatch reads/writes or edit retained draft state", async () => {
  const h = await ready();
  h.current.openEdit(server(1));
  h.render();
  h.cleanup();
  const before = h.requests.length,
    writes = h.writes.length;
  h.current.load();
  h.current.openAdd();
  h.current.openEdit(server(2));
  h.current.setOpen(false);
  h.current.setF({ name: "No" });
  await h.current.saveForm();
  await h.current.refreshTools();
  await h.current.removeServer(server(1));
  await h.current.toggleEnabled(server(1));
  await h.current.toggleVisibility(1, "a", "Agent");
  await h.current.loadTools(1);
  assert.equal(h.requests.length, before);
  assert.equal(h.writes.length, writes);
});
test("cleanup/setup retires prior catalog and mutation lifetimes without releasing a new drawer busy state", async () => {
  const h = await ready();
  h.current.openEdit(server(1));
  h.render();
  const old = h.current.saveForm();
  h.cleanup();
  h.replay();
  h.current.openEdit(server(2));
  h.render();
  const fresh = h.current.saveForm();
  h.render();
  await h.settle(h.find("saveMcpServer")[0], { id: 1 });
  await old;
  assert.equal(h.current.saving, true);
  assert.equal(h.current.editing.id, 2);
  assert.equal(h.notices.length, 0);
  await h.settle(h.find("saveMcpServer")[1], { id: 2 });
  await fresh;
});
test("JSX wires controlled close, create-only fieldset lock and shared record busy gates", () => {
  assert.match(source, /<Sheet open=\{open\} onOpenChange=\{setOpen\}/);
  assert.match(source, /<fieldset disabled=\{!editing && saving\}/);
  assert.equal((source.match(/disabled=\{busyIds.has\(s.id\)\}/g) ?? []).length, 3);
  assert.match(source, /disabled=\{saving \|\| busyIds.has\(editing.id\)\}/);
  assert.match(source, /disabled=\{refreshing \|\| \(editing !== null && busyIds.has\(editing.id\)\)\}/);
});

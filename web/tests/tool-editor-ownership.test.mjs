import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { stripTypeScriptTypes } from "node:module";
import test from "node:test";
import vm from "node:vm";

// Execute exact built-in editor declarations with inert metadata APIs and hook
// doubles. This does not render React, invoke tools, or provide browser coverage.
const source = await readFile(new URL("../src/app/(main)/system/tools/page.tsx", import.meta.url), "utf8");
function section(a, b, offset = 0) {
  const start = source.indexOf(a, offset),
    end = source.indexOf(b, start + a.length);
  assert(start >= 0 && end > start, a);
  return source.slice(start, end);
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((a, b) => {
    resolve = a;
    reject = b;
  });
  return { promise, resolve, reject };
}
const plain = (value) => JSON.parse(JSON.stringify(value));
const fixture = (key) => ({
  key,
  description: "Original metadata",
  agents: ["reader"],
  enabled: true,
  schema: {
    type: "object",
    required: ["count"],
    properties: { count: { type: "integer", description: "Count", default: 2 } },
  },
});
function harness(key = "fixture_a", parent = { selected: key, closes: 0, reloads: 0 }) {
  const tool = fixture(key),
    requests = [],
    notices = [],
    states = [],
    writes = [],
    effects = [];
  const context = vm.createContext({
    structuredClone,
    tool,
    parent,
    React: {
      useState: (value) => {
        const i = states.length;
        states.push(typeof value === "function" ? value() : value);
        return [
          states[i],
          (next) => {
            states[i] = typeof next === "function" ? next(states[i]) : next;
            writes.push(i);
          },
        ];
      },
      useRef: (current) => ({ current }),
      useCallback: (callback) => callback,
      useLayoutEffect: (setup) => {
        effects.push({ setup, cleanup: setup() });
      },
    },
    api: Object.fromEntries(
      ["saveTool", "resetTool"].map((method) => [
        method,
        (...args) => {
          const d = deferred();
          requests.push({ ...d, method, args });
          return d.promise;
        },
      ]),
    ),
    toast: { success: (value) => notices.push(["success", value]), error: (value) => notices.push(["error", value]) },
    onSaved: () => {
      parent.reloads++;
    },
    onClose: () => {
      parent.closes++;
      parent.selected = null;
    },
  });
  const helpers = section("function toRows(", "// ToolEditor");
  const body = section("  type EditorDraft =", "\n  return (\n", source.indexOf("// ToolEditor"));
  vm.runInContext(
    stripTypeScriptTypes(
      `${helpers}\n${body}\nglobalThis.actions={save,reset,setDescription,setEnabled,setRow,toggleAgent,editDraft};globalThis.refs={draftRef,operationRef,alive};`,
    ),
    context,
  );
  return {
    ...context.actions,
    refs: context.refs,
    context,
    requests,
    notices,
    states,
    writes,
    tool,
    parent,
    cleanup: () =>
      effects.forEach((e) => {
        e.cleanup();
      }),
    replay: () =>
      effects.forEach((e) => {
        e.cleanup = e.setup();
      }),
  };
}

test("same-turn repeated Save submits exactly one captured ordinary metadata payload", async () => {
  const h = harness();
  const first = h.save();
  await h.save();
  assert.equal(h.requests.length, 1);
  assert.equal(h.states[1], true);
  assert.equal(h.requests[0].method, "saveTool");
  assert.deepEqual(plain(h.requests[0].args), [
    "fixture_a",
    { description: "Original metadata", schema: h.tool.schema, agents: ["reader"], enabled: true },
  ]);
  h.requests[0].resolve({ ok: true });
  await first;
  assert.equal(h.parent.reloads, 1);
  assert.equal(h.parent.closes, 1);
  assert.equal(h.states[1], false);
});
test("Save and Restore defaults share one immediate owner in both orders", async () => {
  for (const [first, second] of [
    ["save", "reset"],
    ["reset", "save"],
    ["reset", "reset"],
  ]) {
    const h = harness();
    const op = h[first]();
    await h[second]();
    assert.equal(h.requests.length, 1);
    assert.equal(h.states[1], true);
    h.requests[0].resolve({ ok: true });
    await op;
    assert.equal(h.parent.closes, 1);
    assert.equal(h.refs.operationRef.current, null);
    assert.equal(h.states[1], false);
  }
});
test("a previously captured Save closure submits synchronous latest metadata edits", async () => {
  const h = harness(),
    save = h.save;
  h.setDescription("Latest description");
  h.setEnabled(false);
  h.setRow(0, { defaultStr: "3", description: "Latest row" });
  h.toggleAgent("editor");
  h.toggleAgent("reader");
  const op = save(),
    payload = h.requests[0].args[1];
  assert.equal(payload.description, "Latest description");
  assert.equal(payload.enabled, false);
  assert.deepEqual(plain(payload.agents), ["editor"]);
  assert.equal(payload.schema.properties.count.default, 3);
  assert.equal(payload.schema.properties.count.description, "Latest row");
  h.requests[0].resolve({ ok: true });
  await op;
});
test("later edits are retained, cannot change the submitted payload, and prevent completion closing the drawer", async () => {
  const h = harness();
  const op = h.save(),
    payload = h.requests[0].args[1];
  h.setDescription("Later draft");
  h.setEnabled(false);
  h.setRow(0, { defaultStr: "9" });
  h.toggleAgent("editor");
  h.requests[0].resolve({ ok: true });
  await op;
  assert.equal(h.parent.closes, 0);
  assert.equal(h.parent.selected, "fixture_a");
  assert.equal(h.parent.reloads, 1);
  assert.equal(h.refs.draftRef.current.description, "Later draft");
  assert.equal(h.refs.draftRef.current.enabled, false);
  assert.equal(h.refs.draftRef.current.rows[0].defaultStr, "9");
  assert.equal(payload.description, "Original metadata");
  assert.equal(payload.schema.properties.count.default, 2);
  assert.deepEqual(plain(payload.agents), ["reader"]);
  assert.match(h.notices[0][1], /newer edits remain unsaved/);
  const next = h.save();
  assert.equal(h.requests.length, 2);
  assert.equal(h.requests[1].args[1].description, "Later draft");
  h.requests[1].resolve({ ok: true });
  await next;
  assert.equal(h.parent.closes, 1);
});
test("same-value edits also retire close authority rather than relying on value equality", async () => {
  const h = harness();
  const op = h.save();
  h.setDescription("Original metadata");
  h.requests[0].resolve({ ok: true });
  await op;
  assert.equal(h.parent.closes, 0);
  assert.match(h.notices[0][1], /newer edits remain unsaved/);
});
test("save completion for unmounted A cannot close or reload newly selected B", async () => {
  const parent = { selected: "fixture_a", closes: 0, reloads: 0 },
    old = harness("fixture_a", parent);
  const op = old.save();
  old.cleanup();
  parent.selected = "fixture_b";
  const active = harness("fixture_b", parent);
  const before = old.writes.length;
  old.requests[0].resolve({ ok: true });
  await op;
  assert.equal(parent.selected, "fixture_b");
  assert.equal(parent.closes, 0);
  assert.equal(parent.reloads, 0);
  assert.equal(old.notices.length, 0);
  assert.equal(old.writes.length, before);
  assert.equal(active.refs.alive.current, true);
});
test("old save completion cannot close a fresh editor instance reopened for the same key", async () => {
  const parent = { selected: "fixture_a", closes: 0, reloads: 0 },
    old = harness("fixture_a", parent);
  const op = old.save();
  old.cleanup();
  const fresh = harness("fixture_a", parent);
  fresh.setDescription("Reopened draft");
  old.requests[0].resolve({ ok: true });
  await op;
  assert.equal(parent.closes, 0);
  assert.equal(fresh.refs.draftRef.current.description, "Reopened draft");
  assert.equal(old.notices.length, 0);
});
test("known errors keep drafts open, release the owned busy state and never trigger automatic retry", async () => {
  const h = harness();
  h.setDescription("Keep this");
  const op = h.save();
  h.requests[0].reject(new Error("Denied"));
  await op;
  assert.equal(h.requests.length, 1);
  assert.equal(h.parent.closes, 0);
  assert.equal(h.parent.reloads, 0);
  assert.equal(h.refs.draftRef.current.description, "Keep this");
  assert.equal(h.states[1], false);
  assert.deepEqual(h.notices, [["error", "Save failed: Denied"]]);
});
test("unmounted failure emits no stale toast/state change and cannot affect the current drawer", async () => {
  for (const action of ["save", "reset"]) {
    const h = harness();
    const op = h[action]();
    h.cleanup();
    const before = h.writes.length;
    h.requests[0].reject(new Error("late failure"));
    await op;
    assert.equal(h.notices.length, 0);
    assert.equal(h.writes.length, before);
    assert.equal(h.parent.closes, 0);
    assert.equal(h.refs.operationRef.current, null);
  }
});
test("existing Restore defaults keeps later edits and retains Python unavailable failure semantics", async () => {
  const h = harness();
  const op = h.reset();
  h.setDescription("New local metadata");
  h.requests[0].resolve({ ok: true });
  await op;
  assert.equal(h.requests[0].method, "resetTool");
  assert.deepEqual(plain(h.requests[0].args), ["fixture_a"]);
  assert.equal(h.parent.closes, 0);
  assert.equal(h.refs.draftRef.current.description, "New local metadata");
  assert.match(h.notices[0][1], /newer edits remain unsaved/);
  const denied = h.reset();
  h.requests[1].reject(new Error("Tool capability unavailable"));
  await denied;
  assert.equal(h.parent.closes, 0);
  assert.equal(h.notices.at(-1)[1], "Restore failed: Tool capability unavailable");
});
test("cleanup/setup preserves pending ownership and recovers busy state after an inactive completion", async () => {
  const h = harness();
  const op = h.save();
  h.cleanup();
  h.replay();
  await h.save();
  assert.equal(h.requests.length, 1);
  assert.equal(h.states[1], true);
  h.cleanup();
  h.requests[0].resolve({ ok: true });
  await op;
  assert.equal(h.refs.operationRef.current, null);
  h.replay();
  assert.equal(h.states[1], false);
  assert.equal(h.parent.closes, 0);
  assert.equal(h.notices.length, 0);
});
test("actions from an inactive editor cannot submit or change its draft", async () => {
  const h = harness();
  h.cleanup();
  const draft = h.refs.draftRef.current;
  h.setDescription("should not apply");
  await h.save();
  await h.reset();
  assert.equal(h.requests.length, 0);
  assert.equal(h.refs.draftRef.current, draft);
});
test("a refresh callback editing the form cannot cause close after its newer draft", async () => {
  const h = harness();
  h.context.onSaved = () => {
    h.setDescription("Callback edit");
  };
  const op = h.save();
  h.requests[0].resolve({ ok: true });
  await op;
  assert.equal(h.parent.closes, 0);
  assert.equal(h.refs.draftRef.current.description, "Callback edit");
});
test("source keeps keyed editor instances, existing action payloads and both rendered busy gates", () => {
  const component = section("// ToolEditor", "// ToolGridCard");
  assert.match(source, /<ToolEditor\s+key=\{selected.key\}/);
  assert.match(component, /React.useLayoutEffect\(\(\) =>/);
  assert.match(component, /onClick=\{save\} disabled=\{saving\}/);
  assert.match(component, /onClick=\{reset\} disabled=\{saving\}/);
  assert.doesNotMatch(component, /setTimeout|AbortController|testCustomTool|createCustomTool/);
});

async function flush() {
  for (let i = 0; i < 12; i++) await Promise.resolve();
}
function actualParentReload(h) {
  const reads = [];
  const state = { tools: [h.tool], error: null };
  h.context.api.tools = () => {
    const d = deferred();
    reads.push(d);
    return d.promise;
  };
  h.context.setTools = (items) => {
    state.tools = items;
    if (!items.some((item) => item.key === h.tool.key)) {
      h.parent.selected = null;
      h.cleanup();
    }
  };
  h.context.setCatalogError = (error) => {
    state.error = error;
  };
  const reload = section(
    "  const reload = React.useCallback(",
    "  React.useEffect(",
    source.indexOf("export default function ToolsPage()"),
  );
  vm.runInContext(stripTypeScriptTypes(`${reload}\nglobalThis.onSaved=reload;globalThis.reload=reload;`), h.context);
  return { reads, state, reload: h.context.reload };
}
test("actual parent catalog refresh failure after acknowledged Save or Restore retains newer editor draft", async () => {
  for (const action of ["save", "reset"]) {
    const h = harness(),
      parent = actualParentReload(h);
    const op = h[action]();
    h.setDescription("Newer local draft");
    h.requests[0].resolve({ ok: true });
    await op;
    assert.equal(parent.reads.length, 1);
    parent.reads[0].reject(new Error("Catalog temporarily unavailable"));
    await flush();
    assert.equal(h.parent.selected, "fixture_a");
    assert.equal(h.parent.closes, 0);
    assert.equal(h.refs.alive.current, true);
    assert.equal(h.refs.draftRef.current.description, "Newer local draft");
    assert.equal(parent.state.tools[0], h.tool);
    assert.equal(parent.state.error, "Catalog temporarily unavailable");
    assert.equal(h.notices.filter(([kind]) => kind === "error").length, 0);
    parent.reload();
    parent.reads[1].resolve([{ ...h.tool, description: "Server metadata" }]);
    await flush();
    assert.equal(parent.state.error, null);
    assert.equal(h.refs.draftRef.current.description, "Newer local draft");
  }
});
test("parent failure is a visible retryable read error, not an empty successful catalog", async () => {
  const h = harness(),
    parent = actualParentReload(h);
  parent.reload();
  parent.reads[0].reject(new Error("Denied"));
  await flush();
  assert.equal(parent.state.error, "Denied");
  assert.equal(parent.state.tools.length, 1);
  parent.reload();
  parent.reads[1].reject(null);
  await flush();
  assert.equal(parent.state.error, "Tool catalog unavailable");
  assert.equal(parent.state.tools.length, 1);
  const page = source.slice(
    source.indexOf("export default function ToolsPage()"),
    source.indexOf("// ---- Custom tool editor ----"),
  );
  assert.match(page, /catalogError &&/);
  assert.match(page, /Tool catalog refresh failed/);
  assert.match(page, /onClick=\{reload\}>Retry catalog/);
});

test("actual parent close callback preserves a different queued selection before old editor cleanup", () => {
  const callback = source.match(
    /onClose=\{(\(\) => setSelectedKey\(\(current\) => current === selected\.key \? null : current\))\}/,
  )?.[1];
  assert(callback);
  let key = "fixture_b";
  const context = vm.createContext({
    selected: { key: "fixture_a" },
    setSelectedKey: (updater) => {
      key = updater(key);
    },
  });
  vm.runInContext(`globalThis.close=${callback};`, context);
  context.close();
  assert.equal(key, "fixture_b");
  key = "fixture_a";
  context.close();
  assert.equal(key, null);
});

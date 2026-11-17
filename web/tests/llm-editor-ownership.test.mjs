import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { stripTypeScriptTypes } from "node:module";
import test from "node:test";
import vm from "node:vm";

// Execute the actual drawer and parent declarations with deferred inert APIs.
// Hook doubles cover ownership/control flow, not mounted React or browser timing.
const source = await readFile(new URL("../src/app/(main)/system/llm/page.tsx", import.meta.url), "utf8");
function section(start, end, offset = 0) {
  const a = source.indexOf(start, offset),
    b = source.indexOf(end, a + start.length);
  assert(a >= 0 && b > a, `Source boundary: ${start}`);
  return source.slice(a, b);
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
const plain = (value) => JSON.parse(JSON.stringify(value));
const zero = () => ({
  connect: { attempts: 0, interval_ms: 0 },
  empty: { attempts: 0, interval_ms: 0 },
  stream: { attempts: 0, interval_ms: 0 },
});
const fixture = (id = "7") => ({
  id,
  name: `Profile ${id}`,
  model: `model-${id}`,
  format: "openai",
  base_url: "https://provider.invalid/v1",
  proxy: "",
  rate_per_second: 2,
  rate_per_minute: 30,
  context_window_k: 128,
  api_key_hint: "masked",
  is_default: false,
  retry: zero(),
});
function hooks() {
  const slots = [],
    writes = [],
    effects = [];
  let cursor = 0;
  return {
    slots,
    writes,
    start: () => {
      cursor = 0;
    },
    React: {
      useState(initial) {
        const i = cursor++;
        if (!(i in slots)) slots[i] = typeof initial === "function" ? initial() : initial;
        return [
          slots[i],
          (value) => {
            slots[i] = typeof value === "function" ? value(slots[i]) : value;
            writes.push(i);
          },
        ];
      },
      useRef(initial) {
        const i = cursor++;
        if (!(i in slots)) slots[i] = { current: initial };
        return slots[i];
      },
      useCallback: (fn) => fn,
      useLayoutEffect(setup) {
        const i = cursor++;
        if (i in slots) return;
        const effect = { setup, cleanup: null };
        slots[i] = effect;
        effects.push(effect);
      },
    },
    commit: () => {
      for (const e of effects) if (!e.cleanup) e.cleanup = e.setup();
    },
    cleanup: () => {
      for (const e of effects) {
        e.cleanup?.();
        e.cleanup = null;
      }
    },
  };
}
function parent() {
  const h = hooks(),
    context = vm.createContext({ React: h.React, structuredClone });
  vm.runInContext(
    stripTypeScriptTypes(
      `${section("  type Editor =", "\n  const loadPool")}\nglobalThis.actions = { openEditor, changeEditorOpen, editorRef };`,
    ),
    context,
  );
  return context.actions;
}
function harness(p = fixture(), existingParent) {
  const owner = existingParent ?? parent();
  if (!existingParent) owner.openEditor(p);
  const selected = owner.editorRef.current;
  const h = hooks(),
    requests = [],
    notices = [],
    reloads = [];
  const context = vm.createContext({
    React: h.React,
    structuredClone,
    ZERO_OVERRIDE: zero(),
    api: Object.fromEntries(
      ["fetchLLMModels", "testLLM", "saveLLMProfile"].map((method) => [
        method,
        (...args) => {
          const request = { method, args, ...deferred() };
          requests.push(request);
          return request.promise;
        },
      ]),
    ),
    toast: {
      success: (...args) => notices.push(["success", ...args]),
      error: (...args) => notices.push(["error", ...args]),
    },
  });
  const drawer = section(
    "  // The parent keys each open/switch",
    "\n  return (",
    source.indexOf("function ProfileSheet("),
  );
  const constants = section('const NONE = "none";', "const THINKING_TYPES:");
  vm.runInContext(
    stripTypeScriptTypes(`${constants}\n${section("function initialProfileDraft(", "function ProfileSheet(")}
    globalThis.render = function(profile, open, isCurrent, onOpenChange, onSaved) {
      ${drawer}
      return { loadModels, testConnection, save, editDraft, draftRef, requests, alive, profileId,
        draft, storedId, isNew, testing, saving, saveUnverified, models, loadingModels, modelsOpen };
    };`),
    context,
  );
  const props = {
    profile: p,
    isCurrent: () => owner.editorRef.current.open && owner.editorRef.current.session === selected.session,
    onOpenChange: (open) => owner.changeEditorOpen(selected.session, open),
    onSaved: (id) => reloads.push(id),
  };
  let actions;
  function render(profile = props.profile) {
    h.start();
    actions = context.render(profile, owner.editorRef.current.open, props.isCurrent, props.onOpenChange, props.onSaved);
    h.commit();
    return actions;
  }
  render();
  return {
    owner,
    selected,
    requests,
    notices,
    reloads,
    writes: h.writes,
    render,
    props,
    get actions() {
      return actions;
    },
    get state() {
      return render();
    },
    close: () => props.onOpenChange(false),
    cleanup: h.cleanup,
    replay: h.commit,
  };
}
const actions = ["loadModels", "testConnection", "save"];
const busy = { loadModels: "loadingModels", testConnection: "testing", save: "saving" };
const success = {
  loadModels: { ok: true, models: ["remote-a"] },
  testConnection: { ok: true, reply: "synthetic reply", latency_ms: 12 },
  save: { id: 7 },
};

test("parent gives every open a fresh keyed session and ignores retired close callbacks", () => {
  assert.match(source, /<ProfileSheet\s+key=\{editor.session\}/);
  assert.match(source, /isCurrent=\{\(\) => editorRef.current.open && editorRef.current.session === editor.session\}/);
  assert.match(source, /onOpenChange=\{\(open\) => changeEditorOpen\(editor.session, open\)\}/);
  const h = harness(),
    first = h.selected.session;
  h.close();
  assert.equal(h.owner.editorRef.current.open, false);
  h.owner.openEditor(fixture());
  assert.equal(h.owner.editorRef.current.session, first + 1);
  h.close();
  assert.equal(h.owner.editorRef.current.open, true);
  assert.equal(h.props.isCurrent(), false);
});

test("background profile-object refresh never replaces any local draft or starts a fresh session", () => {
  const h = harness();
  const edit = {
    name: "Local",
    format: "anthropic",
    model: "local-model",
    apiKey: "synthetic-key",
    baseUrl: "https://next.invalid",
    proxy: "http://proxy.invalid",
    rps: "9",
    rpm: "90",
    cw: "512",
    thinkingType: "enabled",
    effort: "high",
    priority: "30",
    poolExclude: true,
    streaming: false,
    maxTokens: "99",
    maxTokensField: "max_completion_tokens",
    sessionHeaderKey: "x-fixture",
    retry: zero(),
  };
  edit.retry.connect.attempts = 6;
  h.actions.editDraft(edit);
  const draft = h.actions.draftRef.current;
  h.render({ ...fixture(), name: "Reloaded server name", model: "server-model", retry: zero() });
  assert.equal(h.actions.draftRef.current, draft);
  assert.deepEqual(plain(h.actions.draft), edit);
  assert.equal(h.owner.editorRef.current.session, h.selected.session);
});

for (const action of actions) {
  test(`${action}: same-turn repeat dispatches only once; unchanged success is published`, async () => {
    const h = harness(),
      pending = h.actions[action]();
    await h.actions[action]();
    assert.equal(h.requests.length, 1);
    assert.equal(h.state[busy[action]], true);
    h.requests[0].resolve(success[action]);
    await pending;
    assert.equal(h.notices.length, 1);
    assert.equal(h.notices[0][0], "success");
    if (action === "save") {
      assert.deepEqual(h.reloads, ["7"]);
      assert.equal(h.owner.editorRef.current.open, false);
    } else assert.equal(h.state[busy[action]], false);
  });
  for (const transition of ["close", "switch-before-commit", "reopen-same", "unmount"]) {
    for (const failure of [false, true]) {
      test(`${action}: ${transition} suppresses stale ${failure ? "failure" : "success"} and busy writes`, async () => {
        const h = harness(),
          pending = h.actions[action]();
        if (transition === "unmount") h.cleanup();
        else h.close();
        let fresh, freshPending;
        if (transition === "switch-before-commit" || transition === "reopen-same") {
          const p = fixture(transition === "reopen-same" ? "7" : "8");
          h.owner.openEditor(p);
          // Old drawer has not run cleanup yet: parent authority must already be gone.
          fresh = harness(p, h.owner);
          freshPending = fresh.actions[action]();
        }
        const before = h.writes.length;
        if (failure) h.requests[0].reject(new Error("synthetic late failure"));
        else h.requests[0].resolve(success[action]);
        await pending;
        assert.equal(h.writes.length, before);
        assert.deepEqual(h.notices, []);
        assert.deepEqual(h.reloads, []);
        await h.actions[action]();
        assert.equal(h.requests.length, 1, "retired callbacks cannot dispatch");
        if (fresh) {
          assert.equal(fresh.state[busy[action]], true);
          assert.equal(h.owner.editorRef.current.open, true);
          fresh.requests[0].resolve({ ...success[action], id: Number(fresh.selected.profile.id) });
          await freshPending;
        }
      });
    }
  }
  test(`${action}: current failure keeps the draft, releases its own busy state and permits explicit retry`, async () => {
    const h = harness(),
      pending = h.actions[action]();
    h.requests[0].reject(new Error("synthetic offline"));
    await pending;
    assert.equal(h.state[busy[action]], false);
    assert.equal(h.owner.editorRef.current.open, true);
    assert.equal(h.notices[0][0], "error");
    assert.equal(h.requests.length, 1);
    const again = h.actions[action]();
    assert.equal(h.requests.length, 2);
    h.requests[1].resolve(success[action]);
    await again;
  });
  test(`${action}: cleanup/setup retains pending ownership and reconciles an inactive completion`, async () => {
    for (const settleDuringCleanup of [false, true]) {
      const h = harness(),
        pending = h.actions[action]();
      h.cleanup();
      if (settleDuringCleanup) {
        h.requests[0].resolve(success[action]);
        await pending;
      }
      h.replay();
      assert.equal(h.state[busy[action]], !settleDuringCleanup);
      if (!settleDuringCleanup) {
        await h.actions[action]();
        assert.equal(h.requests.length, 1);
        h.requests[0].resolve(success[action]);
        await pending;
      } else assert.deepEqual(h.notices, []);
    }
  });
}

test("each endpoint/key edit retires model results and old finally cannot clear a newer request", async () => {
  for (const patch of [
    { format: "anthropic" },
    { baseUrl: "https://changed.invalid" },
    { proxy: "http://changed.invalid" },
    { apiKey: "synthetic-change" },
  ]) {
    for (const fail of [false, true]) {
      const h = harness(),
        old = h.actions.loadModels();
      h.actions.editDraft(patch);
      assert.equal(h.state.loadingModels, false);
      const current = h.actions.loadModels();
      assert.equal(h.requests.length, 2);
      if (fail) h.requests[0].reject(new Error("old list failed"));
      else h.requests[0].resolve(success.loadModels);
      await old;
      assert.equal(h.state.loadingModels, true);
      assert.deepEqual(plain(h.state.models), []);
      assert.equal(h.notices.length, 0);
      h.requests[1].resolve({ ok: true, models: ["new-model"] });
      await current;
      assert.equal(h.state.loadingModels, false);
      assert.deepEqual(plain(h.state.models), ["new-model"]);
      assert.equal(h.state.modelsOpen, true);
      h.actions.editDraft(patch);
      assert.deepEqual(plain(h.state.models), []);
      assert.equal(h.state.modelsOpen, false);
    }
  }
});

test("old list resolved after the new list cannot replace it", async () => {
  const h = harness(),
    old = h.actions.loadModels();
  h.actions.editDraft({ baseUrl: "https://changed.invalid" });
  const current = h.actions.loadModels();
  h.requests[1].resolve({ ok: true, models: ["new"] });
  await current;
  h.requests[0].resolve({ ok: true, models: ["old"] });
  await old;
  assert.deepEqual(plain(h.state.models), ["new"]);
  assert.equal(h.notices.length, 1);
});

test("connection test snapshots latest synchronous edits and suppresses feedback after any newer edit", async () => {
  for (const fail of [false, true]) {
    const h = harness(),
      captured = h.actions.testConnection;
    h.actions.editDraft({
      format: "openai-responses",
      model: "edited",
      thinkingType: "enabled",
      effort: "xhigh",
      streaming: false,
      sessionHeaderKey: " x-test ",
    });
    const pending = captured();
    assert.deepEqual(plain(h.requests[0].args), [
      "openai-responses",
      "edited",
      "https://provider.invalid/v1",
      "",
      "",
      "enabled",
      "xhigh",
      7,
      false,
      "x-test",
    ]);
    h.actions.editDraft({ model: "edited" }); // even a same-value edit retires feedback authority
    if (fail) h.requests[0].reject(new Error("late test error"));
    else h.requests[0].resolve(success.testConnection);
    await pending;
    assert.equal(h.state.testing, false);
    assert.deepEqual(h.notices, []);
  }
});

test("save submits latest immutable draft and keeps later edits open across its catalog refresh", async () => {
  const h = harness(),
    captured = h.actions.save;
  const retry = zero();
  retry.empty.attempts = 4;
  h.actions.editDraft({
    name: " Latest ",
    model: " latest-model ",
    maxTokens: "-3",
    maxTokensField: "max_completion_tokens",
    retry,
  });
  retry.empty.attempts = 99;
  const pending = captured(),
    sent = h.requests[0].args[0];
  assert.equal(sent.id, 7);
  assert.equal(sent.name, "Latest");
  assert.equal(sent.model, "latest-model");
  assert.equal(sent.api_key, "");
  assert.equal(sent.max_tokens, 0);
  assert.equal(sent.max_tokens_field, "max_completion_tokens");
  assert.equal(sent.retry.empty.attempts, 4);
  h.actions.editDraft({ name: "Newer draft", retry: zero() });
  h.requests[0].resolve(success.save);
  await pending;
  h.render({ ...fixture(), name: "Latest" });
  assert.equal(h.state.draft.name, "Newer draft");
  assert.equal(sent.name, "Latest");
  assert.equal(sent.retry.empty.attempts, 4);
  assert.equal(h.owner.editorRef.current.open, true);
  assert.equal(h.state.saving, false);
  assert.deepEqual(h.reloads, ["7"]);
  assert.match(h.notices[0][1], /newer edits remain unsaved/);
});

test("create with later edits adopts acknowledged ID for next Save/test/models without duplicating the profile", async () => {
  const h = harness(null);
  h.actions.editDraft({ name: "Created", model: "synthetic-model", maxTokensField: "max_completion_tokens" });
  const first = h.actions.save();
  assert.equal("id" in h.requests[0].args[0], false);
  assert.equal(h.requests[0].args[0].max_tokens_field, "");
  h.actions.editDraft({ name: "Newer" });
  h.requests[0].resolve({ id: 41 });
  await first;
  assert.equal(h.state.isNew, false);
  const testPending = h.actions.testConnection(),
    modelsPending = h.actions.loadModels();
  assert.equal(h.requests[1].args[7], 41);
  assert.equal(h.requests[2].args[4], 41);
  h.requests[1].resolve(success.testConnection);
  h.requests[2].resolve(success.loadModels);
  await Promise.all([testPending, modelsPending]);
  const second = h.actions.save();
  assert.equal(h.requests[3].args[0].id, 41);
  assert.equal(h.requests[3].args[0].name, "Newer");
  h.requests[3].resolve({ id: 41 });
  await second;
  assert.equal(h.owner.editorRef.current.open, false);
});

test("save validation is local and current failed model/test DTOs preserve existing feedback", async () => {
  const h = harness(null);
  await h.actions.save();
  assert.equal(h.requests.length, 0);
  assert.deepEqual(h.notices, [["error", "Enter a name and model"]]);
  const models = h.actions.loadModels();
  h.requests[0].resolve({ ok: true, models: [] });
  await models;
  assert.match(h.notices[1][1], /No models returned/);
  const connection = h.actions.testConnection();
  h.requests[1].resolve({ ok: false, error: "fixture refused" });
  await connection;
  assert.equal(h.notices[2][1], "Connection failed: fixture refused");
});

for (const creating of [false, true]) {
  test(`${creating ? "create" : "update"} acknowledgement retires previous key-context probes without clearing newer owners`, async () => {
    for (const fails of [false, true]) {
      const h = harness(creating ? null : fixture());
      h.actions.editDraft({ name: "Submitted", model: "synthetic-model", apiKey: "synthetic-saved-key" });
      const saved = h.actions.save();
      h.actions.editDraft({ apiKey: "" });
      const oldModels = h.actions.loadModels(),
        oldTest = h.actions.testConnection();
      assert.equal(h.requests[1].args[4], creating ? undefined : 7);
      assert.equal(h.requests[2].args[7], creating ? undefined : 7);
      const id = creating ? 41 : 7;
      h.requests[0].resolve({ id });
      await saved;
      assert.equal(h.state.loadingModels, false);
      assert.equal(h.state.testing, false);
      const models = h.actions.loadModels(),
        connection = h.actions.testConnection();
      assert.equal(h.requests[3].args[4], id);
      assert.equal(h.requests[4].args[7], id);
      const notices = h.notices.length;
      if (fails) {
        h.requests[1].reject(new Error("prior stored-key failure"));
        h.requests[2].reject(new Error("prior stored-key failure"));
      } else {
        h.requests[1].resolve({ ok: false, error: "No API key provided" });
        h.requests[2].resolve({ ok: true, reply: "prior fallback key" });
      }
      await Promise.all([oldModels, oldTest]);
      assert.equal(h.notices.length, notices);
      assert.equal(h.state.loadingModels, true);
      assert.equal(h.state.testing, true);
      assert.deepEqual(plain(h.state.models), []);
      h.requests[3].resolve({ ok: true, models: ["current-context"] });
      h.requests[4].resolve({ ok: true, reply: "current-context" });
      await Promise.all([models, connection]);
      assert.deepEqual(plain(h.state.models), ["current-context"]);
      assert.equal(h.state.loadingModels, false);
      assert.equal(h.state.testing, false);
    }
  });

  test(`${creating ? "create" : "update"} acknowledgement clears completed choices from the prior stored-key context`, async () => {
    const h = harness(creating ? null : fixture());
    h.actions.editDraft({ name: "Submitted", model: "synthetic-model", apiKey: "synthetic-saved-key" });
    const saved = h.actions.save();
    h.actions.editDraft({ apiKey: "" });
    const models = h.actions.loadModels();
    h.requests[1].resolve({ ok: true, models: ["prior-context"] });
    await models;
    assert.equal(h.state.modelsOpen, true);
    h.requests[0].resolve({ id: creating ? 41 : 7 });
    await saved;
    assert.deepEqual(plain(h.state.models), []);
    assert.equal(h.state.modelsOpen, false);
  });

  test(`${creating ? "create" : "update"} invalid receipts preserve target/draft and require reconciliation without another dispatch`, async () => {
    const receipts = [
      undefined,
      null,
      {},
      [],
      "7",
      7,
      { id: undefined },
      { id: null },
      { id: "7" },
      { id: true },
      { id: 0 },
      { id: -1 },
      { id: 1.5 },
      { id: NaN },
      { id: Infinity },
      { id: Number.MAX_SAFE_INTEGER + 1 },
    ];
    if (!creating) receipts.push({ id: 8 });
    for (const receipt of receipts) {
      const h = harness(creating ? null : fixture());
      h.actions.editDraft({ name: "Submitted", model: "synthetic-model" });
      const saved = h.actions.save();
      h.actions.editDraft({ name: "Newer draft" });
      const models = h.actions.loadModels(),
        connection = h.actions.testConnection();
      h.requests[0].resolve(receipt);
      await saved;
      assert.equal(h.state.storedId, creating ? undefined : 7);
      assert.equal(h.actions.profileId.current, creating ? undefined : 7);
      assert.equal(h.state.isNew, creating);
      assert.equal(h.state.draft.name, "Newer draft");
      assert.equal(h.state.saveUnverified, true);
      assert.equal(h.state.saving, false);
      assert.equal(h.state.testing, false);
      assert.equal(h.state.loadingModels, false);
      assert.equal(h.owner.editorRef.current.open, true);
      assert.deepEqual(h.reloads, []);
      assert.deepEqual(h.notices, [
        ["error", "Save outcome could not be verified. Reload this page before saving again."],
      ]);
      await h.actions.save();
      await h.actions.loadModels();
      await h.actions.testConnection();
      assert.equal(h.requests.length, 3);
      h.requests[1].resolve({ ok: true, models: ["unverified-context"] });
      h.requests[2].resolve({ ok: true, reply: "unverified-context" });
      await Promise.all([models, connection]);
      assert.equal(h.notices.length, 1);
      assert.deepEqual(plain(h.state.models), []);
    }
  });
}

test("positive safe numeric create IDs and matching update IDs preserve successful receipt semantics", async () => {
  for (const id of [1, Number.MAX_SAFE_INTEGER]) {
    const h = harness(null);
    h.actions.editDraft({ name: "Submitted", model: "synthetic-model" });
    const pending = h.actions.save();
    h.actions.editDraft({ name: "Newer draft" });
    h.requests[0].resolve({ id });
    await pending;
    assert.equal(h.state.storedId, id);
    assert.equal(h.state.saveUnverified, false);
    assert.deepEqual(h.reloads, [String(id)]);
    const update = h.actions.save();
    assert.equal(h.requests[1].args[0].id, id);
    h.requests[1].resolve({ id });
    await update;
    assert.equal(h.owner.editorRef.current.open, false);
  }
});

test("retired malformed receipts cannot lock a new session or publish reconciliation feedback", async () => {
  const h = harness(),
    saved = h.actions.save();
  h.close();
  h.owner.openEditor(fixture());
  const fresh = harness(fixture(), h.owner);
  h.requests[0].resolve({});
  await saved;
  assert.equal(fresh.state.saveUnverified, false);
  assert.equal(h.notices.length, 0);
  const pending = fresh.actions.save();
  assert.equal(fresh.requests.length, 1);
  fresh.requests[0].resolve({ id: 7 });
  await pending;
});

test("unverified-save UI keeps the draft visible and disables all request actions", () => {
  assert.match(source, /role="alert"[\s\S]*Copy any unsaved edits, then reload this page before saving again/);
  assert.match(source, /disabled=\{loadingModels \|\| saveUnverified\}/);
  assert.match(source, /disabled=\{testing \|\| saveUnverified\}/);
  assert.match(source, /disabled=\{saving \|\| saveUnverified\}/);
});

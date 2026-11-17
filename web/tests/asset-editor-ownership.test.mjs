import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { stripTypeScriptTypes } from "node:module";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("../src/app/(main)/function/assets/page.tsx", import.meta.url), "utf8");
const parser = stripTypeScriptTypes(
  (await readFile(new URL("../src/lib/company-scope.ts", import.meta.url), "utf8"))
    .replace(/^import type .*;\n/m, "").replace(/^export /gm, ""),
);
const helpers = source.slice(source.indexOf("function showScopeWarnings("), source.indexOf("// Add companies"));
const fixture = (id = 7, kind = "keyword", raw = "example.test") => ({
  id, name: `Company ${id}`, asset_count: 3,
  scope: [{ id: id * 10, company_id: id, kind, raw }],
});
const plain = value => JSON.parse(JSON.stringify(value));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };

// Execute the actual component declarations before JSX with persistent hook
// doubles and deferred metadata transports. This checks control flow and hook
// dependencies, not mounted React/Radix behavior or browser effect scheduling.
function harness(component = "EditScopeDialog", company = fixture()) {
  const slots = [], effects = [], requests = [], notices = [], writes = [];
  let cursor = 0, dirty = false, current, reloads = 0;
  const sameDeps = (a, b) => a && b && a.length === b.length && a.every((value, i) => Object.is(value, b[i]));
  const hooks = {
    useState(initial) {
      const index = cursor++;
      if (!slots[index]) slots[index] = { value: typeof initial === "function" ? initial() : initial };
      return [slots[index].value, next => {
        const value = typeof next === "function" ? next(slots[index].value) : next;
        writes.push(index);
        if (!Object.is(slots[index].value, value)) { slots[index].value = value; dirty = true; }
      }];
    },
    useRef(initial) {
      const index = cursor++;
      slots[index] ??= { current: initial };
      return slots[index];
    },
    useMemo(create, deps) {
      const index = cursor++;
      if (!sameDeps(slots[index]?.deps, deps)) slots[index] = { deps, value: create() };
      return slots[index].value;
    },
    useCallback(callback, deps) { return hooks.useMemo(() => callback, deps); },
    useEffect(setup, deps) {
      const index = cursor++;
      if (!sameDeps(slots[index]?.deps, deps)) effects.push(() => {
        slots[index]?.cleanup?.();
        slots[index] = { deps, setup, cleanup: setup(), effect: true };
      });
    },
  };
  hooks.useLayoutEffect = hooks.useEffect;
  const context = vm.createContext({
    URL, React: hooks,
    props: { company, onSaved: () => { reloads++; } },
    api: Object.fromEntries(["createCompany", "updateCompanyScope", "addCompanyScope"].map(method => [method, (...args) => {
      const pending = deferred(); requests.push({ method, args, ...pending }); return pending.promise;
    }])),
    toast: Object.fromEntries(["success", "error", "warning"].map(kind => [kind, (message, options) => notices.push({ kind, message, options })])),
  });
  const start = source.indexOf(`function ${component}(`);
  assert(start >= 0, component);
  const end = source.indexOf("\n  return (", start);
  assert(end > start, component);
  const fields = component === "CompanyDialog" ? "name,setName," : "reason,setReason,";
  vm.runInContext(`${parser}\n${stripTypeScriptTypes(`${helpers}\n${source.slice(start, end)}
    return {open,scopeText,busy,setOpen,setScopeText,submit,${fields}};
  }\nglobalThis.renderComponent = () => ${component}(props);`)}`, context);
  function render(nextCompany) {
    if (nextCompany) context.props.company = nextCompany;
    let passes = 0;
    do {
      dirty = false; cursor = 0;
      current = context.renderComponent();
      while (effects.length) effects.shift()();
      assert(++passes < 20, "hook render did not settle");
    } while (dirty);
    return current;
  }
  render();
  const h = {
    get current() { return current; }, get reloads() { return reloads; }, context, requests, notices, writes, render,
    open() { current.setOpen(true); render(); },
    close() { current.setOpen(false); render(); },
    edit(patch, rerender = true) {
      for (const [key, value] of Object.entries(patch)) current[`set${key[0].toUpperCase()}${key.slice(1)}`](value);
      if (rerender) render();
    },
    submit() { return current.submit(); },
    cleanup() { for (const slot of slots) if (slot?.effect) slot.cleanup?.(); },
    replay() { for (const slot of slots) if (slot?.effect) slot.cleanup = slot.setup(); render(); },
  };
  return h;
}
function ready(component = "EditScopeDialog") {
  const h = harness(component); h.open();
  if (component === "CompanyDialog") h.edit({ name: " New company " });
  if (component === "AppendScopeDialog") h.edit({ scopeText: "new.test", reason: "append evidence" });
  return h;
}
const components = ["CompanyDialog", "EditScopeDialog", "AppendScopeDialog"];
const saved = { id: 11, created: true, added: 1, invalid: 0, scope_added: 1, scope_invalid: 0 };

test("background company refresh preserves text, reason and the opened rule kind", async () => {
  const h = ready();
  h.edit({ scopeText: "example.test\nnew.test", reason: "Local evidence" });
  h.render(fixture(7, "domain", "server.test"));
  assert.equal(h.current.scopeText, "example.test\nnew.test");
  assert.equal(h.current.reason, "Local evidence");
  const op = h.submit();
  assert.deepEqual(plain(h.requests[0].args), [7, [
    { kind: "keyword", value: "example.test" }, { kind: "domain", value: "new.test" },
  ], "Local evidence"]);
  h.requests[0].resolve(saved); await op;
});

for (const component of components) {
  test(`${component}: same-turn repeated submit has one captured mutation`, async () => {
    const h = ready(component), op = h.submit();
    void h.submit();
    assert.equal(h.requests.length, 1);
    h.requests[0].resolve(saved); await op; h.render();
    assert.equal(h.current.open, false);
    assert.equal(h.current.busy, false);
    assert.equal(h.reloads, 1);
  });

  test(`${component}: close/reopen retires old success, warning and busy ownership`, async () => {
    const h = ready(component), old = h.submit();
    h.close(); h.open();
    h.edit({ scopeText: "reopened.test", ...(component === "CompanyDialog" ? { name: "Reopened company" } : { reason: "new evidence" }) });
    const fresh = h.submit(); h.render();
    assert.equal(h.current.busy, true);
    h.requests[0].resolve({ ...saved, warnings: ["stale warning"], invalid: 1, scope_invalid: 1 });
    await old; h.render();
    assert.equal(h.current.open, true);
    assert.equal(h.current.busy, true);
    assert.equal(h.current.scopeText, "reopened.test");
    assert.equal(h.notices.length, 0);
    assert.equal(h.reloads, 0);
    h.requests[1].resolve(saved); await fresh; h.render();
    assert.equal(h.current.open, false);
    assert.equal(h.current.busy, false);
    assert.equal(h.reloads, 1);
  });

  test(`${component}: pending edits respect the create or update contract`, async () => {
    const h = ready(component), op = h.submit();
    const before = plain(h.requests[0].args);
    h.edit({ scopeText: "later.test" });
    h.requests[0].resolve(saved); await op; h.render();
    assert.equal(h.current.open, component !== "CompanyDialog");
    assert.equal(h.current.busy, false);
    assert.equal(h.current.scopeText, component === "CompanyDialog" ? "" : "later.test");
    assert.deepEqual(plain(h.requests[0].args), before);
    assert.equal(h.reloads, 1);
    if (component === "CompanyDialog") assert.doesNotMatch(h.notices[0].message, /newer edits remain unsaved/);
    else assert.match(h.notices[0].message, /newer edits remain unsaved/);
  });

  test(`${component}: unmount retires pending failures and successes`, async () => {
    for (const fails of [false, true]) {
      const h = ready(component), op = h.submit(); h.cleanup();
      const before = h.writes.length;
      if (fails) h.requests[0].reject(new Error("late failure")); else h.requests[0].resolve(saved);
      await op;
      assert.equal(h.notices.length, 0);
      assert.equal(h.reloads, 0);
      assert.equal(h.writes.length, before);
    }
  });

  test(`${component}: captured submit uses edits made before a React render`, async () => {
    const h = ready(component), submit = h.current.submit;
    h.edit({ scopeText: "latest.test", ...(component === "CompanyDialog" ? { name: " Latest company " } : { reason: "latest evidence" }) }, false);
    const op = submit();
    assert.deepEqual(plain(h.requests[0].args), component === "CompanyDialog"
      ? ["Latest company", [{ kind: "domain", value: "latest.test" }]]
      : [7, [{ kind: "domain", value: "latest.test" }], "latest evidence"]);
    h.requests[0].resolve(saved); await op;
  });

  test(`${component}: current unavailable error preserves the draft without retry`, async () => {
    const h = ready(component);
    h.edit({ scopeText: "keep.test" });
    const op = h.submit();
    h.requests[0].reject(new Error("Company scope mutation or cleanup is unavailable"));
    await op; h.render();
    assert.equal(h.current.open, true);
    assert.equal(h.current.busy, false);
    assert.equal(h.current.scopeText, "keep.test");
    assert.equal(h.requests.length, 1);
    assert.equal(h.reloads, 0);
    assert.equal(h.notices[0].message, "Save failed: Company scope mutation or cleanup is unavailable");
  });

  test(`${component}: old failure is silent after close/reopen`, async () => {
    const h = ready(component), op = h.submit();
    h.close(); h.open(); h.edit({ scopeText: "new draft.test" });
    const before = h.writes.length;
    h.requests[0].reject(new Error("old unavailable")); await op; h.render();
    assert.equal(h.current.open, true);
    assert.equal(h.current.busy, false);
    assert.equal(h.current.scopeText, "new draft.test");
    assert.equal(h.writes.length, before);
    assert.equal(h.notices.length, 0);
  });

  test(`${component}: pending same-value and callback edits respect draft mutability`, async () => {
    for (const callbackEdit of [false, true]) {
      const h = ready(component);
      if (callbackEdit) {
        h.context.props.onSaved = () => h.edit({ scopeText: "callback.test" });
        h.render();
      }
      const op = h.submit();
      if (!callbackEdit) h.edit({ scopeText: h.current.scopeText });
      h.requests[0].resolve(saved); await op;
      h.render();
      assert.equal(h.current.open, component !== "CompanyDialog");
      assert.equal(h.current.busy, false);
      if (component === "CompanyDialog") assert.equal(h.current.scopeText, "");
      else if (callbackEdit) assert.equal(h.current.scopeText, "callback.test");
      else assert.match(h.notices[0].message, /newer edits remain unsaved/);
    }
  });

  test(`${component}: effect replay preserves an active request and recovers after inactive completion`, async () => {
    const h = ready(component), op = h.submit();
    h.cleanup(); h.replay();
    void h.submit();
    assert.equal(h.requests.length, 1);
    assert.equal(h.current.busy, true);
    h.cleanup(); h.requests[0].resolve(saved); await op;
    h.replay();
    assert.equal(h.current.busy, false);
    assert.equal(h.notices.length, 0);
    assert.equal(h.reloads, 0);
    if (component === "CompanyDialog") {
      assert.equal(h.current.open, false);
      await h.submit();
      assert.equal(h.requests.length, 1);
    } else {
      const retry = h.submit();
      assert.equal(h.requests.length, 2);
      h.requests[1].resolve(saved); await retry;
    }
  });

  test(`${component}: closed or inactive actions cannot submit or change a draft`, async () => {
    const h = ready(component), submit = h.current.submit, setScopeText = h.current.setScopeText;
    h.close(); const before = h.writes.length;
    setScopeText("ignored.test"); await submit();
    assert.equal(h.requests.length, 0);
    assert.equal(h.writes.length, before);
    h.open(); h.cleanup(); const unmounted = h.writes.length;
    h.current.setOpen(true); h.edit({ scopeText: "inactive.test" }, false); await h.submit();
    assert.equal(h.requests.length, 0);
    assert.equal(h.writes.length, unmounted);
  });
}

for (const component of ["EditScopeDialog", "AppendScopeDialog"]) {
  test(`${component}: company identity changes retire old draft and mutation ownership`, async () => {
    for (const fails of [false, true]) {
      const h = ready(component), oldActions = h.current, old = h.submit();
      assert.equal(h.requests[0].args[0], 7);
      h.render(fixture(8, "domain", "company-b.test"));
      assert.equal(h.current.open, false);
      oldActions.setOpen(true); oldActions.setScopeText("wrong company"); await oldActions.submit();
      assert.equal(h.requests.length, 1);
      h.open();
      assert.equal(h.current.scopeText, component === "EditScopeDialog" ? "company-b.test" : "");
      h.edit({ scopeText: "latest-b.test", reason: "B evidence" });
      const fresh = h.submit(); h.render();
      assert.equal(h.requests[1].args[0], 8);
      if (fails) h.requests[0].reject(new Error("A failure")); else h.requests[0].resolve(saved);
      await old; h.render();
      assert.equal(h.current.open, true);
      assert.equal(h.current.busy, true);
      assert.equal(h.current.scopeText, "latest-b.test");
      assert.equal(h.notices.length, 0);
      h.requests[1].resolve(saved); await fresh; h.render();
      assert.equal(h.current.open, false);
      assert.equal(h.reloads, 1);
    }
  });

  test(`${component}: reopening uses latest company data and keeps backend warning semantics`, async () => {
    const h = ready(component);
    h.edit({ reason: "discarded evidence", scopeText: "discarded.test" }); h.close();
    h.render(fixture(7, "keyword", "new-server.test")); h.open();
    assert.equal(h.current.reason, "");
    assert.equal(h.current.scopeText, component === "EditScopeDialog" ? "new-server.test" : "");
    if (component === "AppendScopeDialog") h.edit({ scopeText: "append.test" });
    const op = h.submit();
    assert.equal(h.requests[0].method, component === "EditScopeDialog" ? "updateCompanyScope" : "addCompanyScope");
    h.requests[0].resolve({ ...saved, invalid: 2, warnings: ["Existing malformed IP"] }); await op; h.render();
    assert.equal(h.current.open, false);
    assert.deepEqual(plain(h.notices), [
      { kind: "warning", message: "Saved; 2 invalid rows" },
      { kind: "warning", message: "Existing malformed IP", options: { duration: 15000 } },
    ]);
  });
}

test("create/replace/append keep empty-scope and validation contracts", async () => {
  for (const component of components) {
    const h = ready(component);
    h.edit({ scopeText: "192.0.2.1/99" }); await h.submit(); h.render();
    assert.equal(h.requests.length, 0);
    assert.equal(h.current.busy, false);
    assert.equal(h.current.open, true);
    h.edit({ scopeText: "" }); const op = h.submit();
    if (component === "AppendScopeDialog") {
      await op; h.render();
      assert.equal(h.requests.length, 0);
      assert.equal(h.current.busy, false);
      assert.equal(h.notices.at(-1).message, "Enter scope entries to append");
    } else {
      assert.deepEqual(plain(h.requests[0].args[1]), []);
      h.requests[0].resolve(saved); await op;
    }
  }
  const h = harness("CompanyDialog"); h.open(); await h.submit(); h.render();
  assert.equal(h.requests.length, 0);
  assert.equal(h.current.busy, false);
  assert.equal(h.notices.at(-1).message, "Enter a company name");
});

test("company name conflict keeps the editable name and does not create again", async () => {
  const h = ready("CompanyDialog"), op = h.submit();
  h.requests[0].reject(new Error("/companies: 409")); await op; h.render();
  assert.equal(h.current.name, " New company ");
  assert.equal(h.current.open, true);
  assert.equal(h.current.busy, false);
  assert.equal(h.requests.length, 1);
  assert.equal(h.notices[0].message, "Company already exists. Choose another name.");
});

test("company create freezes same-turn edits and cannot submit again after acknowledgement", async () => {
  const h = ready("CompanyDialog"), actions = h.current;
  const op = actions.submit();
  actions.setName("A different company");
  actions.setScopeText("newer.test");
  void actions.submit();
  h.render();
  assert.equal(h.current.busy, true);
  assert.equal(h.current.name, " New company ");
  assert.equal(h.current.scopeText, "");
  assert.equal(h.requests.length, 1);
  assert.deepEqual(plain(h.requests[0].args), ["New company", []]);
  h.requests[0].resolve({ id: 41, created: true, scope_added: 0 }); await op;
  actions.setScopeText("after-success.test");
  await actions.submit();
  h.render();
  assert.equal(h.current.open, false);
  assert.equal(h.requests.length, 1);
  assert.equal(h.reloads, 1);
  assert.doesNotMatch(h.notices[0].message, /newer edits remain unsaved/);
});

test("company create releases its draft lock after validation or a known failure", async () => {
  const h = harness("CompanyDialog"); h.open();
  await h.submit();
  h.edit({ name: "Corrected", scopeText: "192.0.2.1/99" });
  await h.submit();
  assert.equal(h.requests.length, 0);
  h.edit({ scopeText: "" });
  const op = h.submit();
  h.edit({ name: "Blocked during save", scopeText: "blocked.test" });
  h.requests[0].reject(new Error("/companies: 409")); await op; h.render();
  assert.equal(h.current.open, true);
  assert.equal(h.current.busy, false);
  assert.equal(h.current.name, "Corrected");
  assert.equal(h.current.scopeText, "");
  h.edit({ name: "Available company", scopeText: "allowed.test" });
  const retry = h.submit();
  assert.deepEqual(plain(h.requests[1].args), ["Available company", [{ kind: "domain", value: "allowed.test" }]]);
  h.requests[1].resolve(saved); await retry; h.render();
  assert.equal(h.current.open, false);
});

test("company close/reopen starts an editable create draft before any render or old settlement", async () => {
  const h = ready("CompanyDialog"), actions = h.current;
  const old = actions.submit();
  actions.setOpen(false); actions.setOpen(true);
  actions.setName("Reopened"); actions.setScopeText("reopened.test");
  const fresh = actions.submit();
  actions.setName("Blocked newer edit"); h.render();
  assert.equal(h.current.name, "Reopened");
  assert.equal(h.current.busy, true);
  h.requests[0].reject(new Error("old request failed")); await old; h.render();
  assert.equal(h.current.busy, true);
  assert.equal(h.notices.length, 0);
  assert.deepEqual(plain(h.requests[1].args), ["Reopened", [{ kind: "domain", value: "reopened.test" }]]);
  h.requests[1].resolve(saved); await fresh; h.render();
  assert.equal(h.current.open, false);
  assert.equal(h.reloads, 1);
});

test("company create visibly disables both inputs with a native fieldset only in the create form", () => {
  const start = source.indexOf("function CompanyDialog(");
  const create = source.slice(start, source.indexOf("// Edit/replace asset scope dialog", start));
  assert.match(create, /<fieldset disabled=\{busy\}[^>]*>[\s\S]*id="cn-name"[\s\S]*id="cn-scope"[\s\S]*<\/fieldset>/);
  const update = source.slice(source.indexOf("function EditScopeDialog("));
  assert.doesNotMatch(update, /<fieldset disabled=\{busy\}|freezeWhileSaving/);
});

function metadataHarness() {
  const reads = [], state = { companies: [fixture()], counts: { ip: 3 } };
  let cleanup;
  const context = vm.createContext({
    metaRequestRef: { current: 0 },
    setCompanies: value => { state.companies = value; },
    setCounts: value => { state.counts = value; },
    React: { useCallback: fn => fn, useEffect: setup => { cleanup = setup(); } },
    api: Object.fromEntries(["companies", "assetCounts"].map(method => [method, () => {
      const pending = deferred(); reads.push({ method, ...pending }); return pending.promise;
    }])),
  });
  const start = source.indexOf("  const loadMeta = React.useCallback(");
  const loader = source.slice(start, source.indexOf("\n\n  // Manual refresh", start));
  const effectStart = source.indexOf("  React.useEffect(() => {\n    loadMeta();");
  const effect = source.slice(effectStart, source.indexOf("\n\n", effectStart));
  vm.runInContext(stripTypeScriptTypes(`${loader}\n${effect}\nglobalThis.reload = loadMeta;`), context);
  return { state, reads, reload: context.reload, cleanup: () => cleanup?.() };
}

test("newest company/count read wins in either completion order", async () => {
  for (const newestFirst of [false, true]) {
    const h = metadataHarness(); h.reload();
    const old = async () => { h.reads[0].resolve([fixture(7)]); h.reads[1].resolve({ ip: 30 }); await flush(); };
    const latest = async () => { h.reads[2].resolve([fixture(8)]); h.reads[3].resolve({ ip: 8 }); await flush(); };
    if (newestFirst) { await latest(); await old(); } else { await old(); await latest(); }
    assert.equal(h.state.companies[0].id, 8);
    assert.deepEqual(h.state.counts, { ip: 8 });
  }
});

test("current metadata failure keeps the last good snapshot and retires older in-flight reads", async () => {
  const h = metadataHarness(), before = h.state.companies;
  h.reload();
  h.reads[2].reject(new Error("company read unavailable")); h.reads[3].reject(new Error("counts unavailable"));
  await flush();
  h.reads[0].resolve([fixture(99)]); h.reads[1].resolve({ ip: 99 }); await flush();
  assert.equal(h.state.companies, before);
  assert.deepEqual(h.state.counts, { ip: 3 });
  h.reload(); h.reads[4].resolve([]); h.reads[5].resolve({ ip: 0 }); await flush();
  assert.deepEqual(h.state.companies, []);
  assert.deepEqual(h.state.counts, { ip: 0 });
});

test("metadata completion after page cleanup cannot publish stale companies or counts", async () => {
  const h = metadataHarness(), before = h.state.companies;
  h.cleanup();
  h.reads[0].resolve([fixture(99)]); h.reads[1].resolve({ ip: 99 }); await flush();
  assert.equal(h.state.companies, before);
  assert.deepEqual(h.state.counts, { ip: 3 });
});

test("rendered dialogs retain identity keys, editable draft setters, close gates and list ownership", () => {
  assert.match(source, /companies\.map\(\(c\) => \(\s*<TableRow key=\{c.id\}>/);
  assert.match(source, /<EditScopeDialog company=\{c\} onSaved=\{refresh\}/);
  assert.match(source, /<AppendScopeDialog company=\{c\} onSaved=\{refresh\}/);
  assert.equal((source.match(/onOpenChange=\{setOpen\}/g) ?? []).length, 3);
  assert.equal((source.match(/onValueChange=\{setScopeText\}/g) ?? []).length, 3);
  assert.equal((source.match(/<Button onClick=\{submit\} disabled=\{busy/g) ?? []).length, 3);
  assert.equal((source.match(/if \(assetsRequestRef.current !== request\) return;/g) ?? []).length, 2);
  assert.match(source, /if \(assetsRequestRef.current === request\) assetsRequestRef.current\+\+;/);
});

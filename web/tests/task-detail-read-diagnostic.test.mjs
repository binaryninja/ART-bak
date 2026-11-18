import ts from "typescript";

import { ApiError, mutationMustWait } from "../src/lib/mutation-contract.mjs";
import { taskDetailHref } from "../src/lib/task-detail-route.mjs";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../src/app/(main)/function/tasks/detail/page.tsx", import.meta.url), "utf8");
const code = ts.transpileModule(`${source}\nexport { TaskDetailInner as Probe };`, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
};
function words(node) {
  if (Array.isArray(node)) return node.map(words).join("");
  if (typeof node === "string" || typeof node === "number") return String(node);
  return node && typeof node === "object" ? words(node.props?.children) : "";
}
const task = (id = "17", extra = {}) => ({
  id,
  name: `Synthetic ${id}`,
  description: "Synthetic task",
  goal: "Inert read check",
  status: "running",
  paused: false,
  ...extra,
});

// Entire production component with inert child JSX, router, timers and deferred
// reads. Only the four read methods may be invoked; mutation APIs are forbidden.
function harness(initialID = "17") {
  let id = initialID,
    cursor = 0,
    dirty = false,
    mounted = true,
    nodes = [];
  const slots = [],
    effects = [],
    reads = [],
    stats = [],
    calls = [],
    writes = [],
    navigations = [],
    timers = new Map();
  const router = { push: () => assert.fail("No push navigation effect"), replace: (href) => navigations.push(href) };
  const same = (a, b) => a && b && a.length === b.length && a.every((x, i) => Object.is(x, b[i]));
  const hooks = {
    useState(initial) {
      const index = cursor++;
      if (!slots[index]) slots[index] = { value: typeof initial === "function" ? initial() : initial };
      return [
        slots[index].value,
        (next) => {
          slots[index].value = typeof next === "function" ? next(slots[index].value) : next;
          writes.push(index);
          dirty = true;
        },
      ];
    },
    useRef(initial) {
      const index = cursor++;
      if (!slots[index]) slots[index] = { current: initial };
      return slots[index];
    },
    useCallback(fn, deps) {
      const index = cursor++;
      if (!slots[index] || !same(slots[index].deps, deps)) slots[index] = { fn, deps };
      return slots[index].fn;
    },
    useEffect(fn, deps) {
      const index = cursor++;
      if (!slots[index] || !same(slots[index].deps, deps)) {
        const prior = slots[index];
        slots[index] = { deps };
        effects.push(() => {
          prior?.cleanup?.();
          slots[index].cleanup = fn();
        });
      }
    },
  };
  const methods = {
    llmProfiles() {
      calls.push("llmProfiles");
      return Promise.resolve([]);
    },
    interceptTask(value) {
      calls.push("interceptTask");
      assert.equal(value, id);
      return Promise.resolve([]);
    },
    task(value) {
      calls.push("task");
      const d = deferred();
      reads.push({ id: value, ...d });
      return d.promise;
    },
    stats(value) {
      calls.push("stats");
      const d = deferred();
      stats.push({ id: value, ...d });
      return d.promise;
    },
  };
  const api = new Proxy(methods, {
    get(target, key) {
      assert.ok(key in target, `Mutation or unexpected API forbidden: ${String(key)}`);
      return target[key];
    },
  });
  const context = vm.createContext({
    exports: {},
    Error,
    Set,
    Map,
    setInterval(fn, ms) {
      assert.equal(ms, 5000);
      const key = Symbol();
      timers.set(key, fn);
      return key;
    },
    clearInterval(key) {
      timers.delete(key);
    },
    require(name) {
      if (name === "react") return hooks;
      if (name === "react/jsx-runtime")
        return {
          jsx: (type, props) => ({ type, props }),
          jsxs: (type, props) => ({ type, props }),
          Fragment: "Fragment",
        };
      if (name === "next/link") return { default: "Link" };
      if (name === "next/navigation")
        return {
          useRouter: () => router,
          useSearchParams: () => ({
            get: (key) => {
              assert.equal(key, "id");
              return id;
            },
          }),
        };
      if (name === "@/lib/api") return { api };
      if (name === "@/lib/mutation-contract.mjs") return { ApiError, mutationMustWait };
      if (name === "@/lib/task-detail-route.mjs") return { taskDetailHref };
      if (name === "sonner") return { toast: new Proxy({}, { get: () => () => assert.fail("No mutation toast") }) };
      assert.ok(name.startsWith("@/components/") || name.startsWith("./_tabs/") || name === "lucide-react", name);
      return new Proxy({}, { get: (_target, key) => String(key) });
    },
  });
  vm.runInContext(code, context);
  function render() {
    assert.ok(mounted);
    do {
      dirty = false;
      cursor = 0;
      nodes = [];
      const tree = context.exports.Probe();
      function visit(node) {
        if (Array.isArray(node)) return node.forEach(visit);
        if (!node || typeof node !== "object") return;
        nodes.push(node);
        visit(node.props?.children);
      }
      visit(tree);
      for (const effect of effects.splice(0)) effect();
    } while (dirty);
    return nodes;
  }
  async function settle() {
    for (let n = 0; n < 8; n++) await Promise.resolve();
    if (mounted) render();
  }
  render();
  return {
    reads,
    stats,
    calls,
    writes,
    navigations,
    render,
    settle,
    words: () =>
      nodes
        .map((node) =>
          node.type === "AlertTitle" || node.type === "AlertDescription" || node.type === "p" ? words(node) : "",
        )
        .join(" "),
    nodes: () => nodes,
    retry: () => nodes.find((node) => node.type === "Button" && /Retry/.test(words(node))),
    navigate(next) {
      id = next;
      render();
    },
    poll() {
      for (const fn of timers.values()) fn();
      render();
    },
    unmount() {
      for (const slot of slots) slot?.cleanup?.();
      mounted = false;
    },
  };
}

test("initial pending read is loading, not missing, and exposes no mutation or retry", () => {
  const h = harness();
  assert.match(h.words(), /Loading/);
  assert.doesNotMatch(h.words(), /not found|deleted|archived|Could not/);
  assert.equal(h.retry(), undefined);
  assert.equal(h.reads.length, 1);
});

test("transport, denied, unavailable and malformed-response failures are not missing tasks", async () => {
  for (const error of [
    new ApiError(0, {}, "Connection unavailable"),
    new ApiError(403, {}, "Read denied"),
    new ApiError(503, {}, "Task read capability unavailable"),
    new SyntaxError("Invalid JSON"),
    new Error("not found"),
    { status: 404 },
    null,
  ]) {
    const h = harness();
    h.reads[0].reject(error);
    await h.settle();
    assert.match(h.words(), /Could not load task/);
    assert.doesNotMatch(h.words(), /HTTP 404|deleted|archived|does not exist/);
    assert.equal(h.retry().props.disabled, false);
    assert.equal(h.retry().props.type, "button");
  }
});

test("an actual HTTP404 read response is distinguished without asserting deletion or archival", async () => {
  const h = harness();
  h.reads[0].reject(new ApiError(404, { error: "Task not found" }, "fallback"));
  await h.settle();
  assert.match(h.words(), /Task 17 could not be found \(HTTP 404\)/);
  assert.doesNotMatch(h.words(), /deleted|archived|does not exist/);
  assert.equal(h.retry().props.disabled, false);
});

test("retry performs only coalesced task/stats reads and success removes the diagnostic", async () => {
  const h = harness();
  h.reads[0].reject(new ApiError(503, {}, "Unavailable"));
  await h.settle();
  const retry = h.retry().props.onClick;
  retry();
  retry();
  h.render();
  assert.equal(h.reads.length, 2);
  assert.equal(h.stats.length, 2);
  assert.equal(h.retry().props.disabled, true);
  assert.match(words(h.retry()), /Retrying read/);
  assert.match(h.words(), /Unavailable/);
  h.reads[1].resolve(task());
  h.stats[1].resolve(null);
  await h.settle();
  assert.equal(h.retry(), undefined);
  assert.doesNotMatch(h.words(), /Could not load|Unavailable/);
  assert.ok(h.nodes().some((node) => node.type === "SessionsTab" && node.props.taskId === "17"));
  assert.ok(h.calls.every((name) => ["task", "stats", "llmProfiles", "interceptTask"].includes(name)));
});

test("optional stats failure does not turn a successful task read into an error", async () => {
  const h = harness();
  h.reads[0].resolve(task());
  h.stats[0].reject(new Error("stats unavailable"));
  await h.settle();
  assert.ok(h.nodes().some((node) => node.type === "Tabs"));
  assert.equal(h.retry(), undefined);
});

test("a recon task redirects to its exact report without mounting interactive sessions", async () => {
  const h = harness("4");
  h.reads[0].resolve(task("4", { runtime_kind: "servicenow-recon", status: "failed" }));
  h.stats[0].resolve(null);
  await h.settle();
  assert.deepEqual(h.navigations, ["/function/recon?id=4"]);
  assert.equal(h.nodes().some(node => node.type === "SessionsTab" || node.type === "Tabs"), false);
  assert.ok(h.nodes().some(node => node.type === "Link" && node.props.href === "/function/recon?id=4"));
});

test("a registered assessment stays on the common task route and mounts its supported view", async () => {
  const h = harness("13");
  h.reads[0].resolve(task("13", { runtime_kind: "assessment", status: "done" }));
  h.stats[0].resolve(null);
  await h.settle();
  assert.deepEqual(h.navigations, []);
  assert.ok(h.nodes().some(node => node.type === "AssessmentDetail" && node.props.task.id === "13"));
  assert.equal(h.nodes().some(node => node.type === "SessionsTab" || node.type === "Tabs"), false);
});

test("poll failure retains the last confirmed current task and existing control display", async () => {
  const h = harness();
  h.reads[0].resolve(task());
  h.stats[0].resolve(null);
  await h.settle();
  h.poll();
  h.reads[1].reject(new ApiError(503, {}, "temporary"));
  await h.settle();
  assert.ok(h.nodes().some((node) => node.type === "Tabs"));
  assert.equal(h.retry(), undefined);
  assert.ok(h.nodes().some((node) => node.type === "h1" && words(node) === "Synthetic 17"));
});

test("query task changes show loading instead of stale data or the previous task's failure", async () => {
  for (const failed of [false, true]) {
    const h = harness();
    if (failed) h.reads[0].reject(new ApiError(404, {}, "old missing"));
    else {
      h.reads[0].resolve(task());
      h.stats[0].resolve(null);
    }
    await h.settle();
    h.navigate("18");
    assert.match(h.words(), /Loading/);
    assert.doesNotMatch(h.words(), /old missing|HTTP 404/);
    assert.equal(
      h.nodes().some((node) => node.type === "Tabs"),
      false,
    );
    h.reads[1].reject(new ApiError(503, {}, "new unavailable"));
    await h.settle();
    assert.match(h.words(), /new unavailable/);
    assert.doesNotMatch(h.words(), /HTTP 404/);
  }
});

test("retired success or error cannot overwrite another task's read diagnostic", async () => {
  for (const fails of [false, true]) {
    const h = harness();
    h.navigate("18");
    h.reads[1].reject(new ApiError(503, {}, "current unavailable"));
    await h.settle();
    if (fails) h.reads[0].reject(new ApiError(404, {}, "retired missing"));
    else {
      h.reads[0].resolve(task());
      h.stats[0].resolve(null);
    }
    await h.settle();
    assert.match(h.words(), /current unavailable/);
    assert.doesNotMatch(h.words(), /retired|HTTP 404/);
  }
});

test("A to B to A keeps request identity and rejects old same-ID errors", async () => {
  const h = harness();
  h.navigate("18");
  h.navigate("17");
  h.reads[2].reject(new ApiError(503, {}, "latest 17"));
  await h.settle();
  h.reads[0].reject(new ApiError(404, {}, "old 17"));
  h.reads[1].reject(new Error("old 18"));
  await h.settle();
  assert.match(h.words(), /latest 17/);
  assert.doesNotMatch(h.words(), /old|HTTP 404/);
});

test("unmounted reads do not write feedback or task state", async () => {
  for (const fails of [false, true]) {
    const h = harness();
    await h.settle(); // Let unrelated, unchanged profile reads settle before retiring the task read.
    h.unmount();
    const before = h.writes.length;
    if (fails) h.reads[0].reject(new ApiError(503, {}, "retired"));
    else {
      h.reads[0].resolve(task());
      h.stats[0].resolve(null);
    }
    await h.settle();
    assert.equal(h.writes.length, before);
  }
});

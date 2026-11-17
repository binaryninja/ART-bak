import { ApiError } from "../src/lib/mutation-contract.mjs";
import { createTaskListReader } from "../src/lib/task-list-reader.mjs";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { stripTypeScriptTypes } from "node:module";
import test from "node:test";
import vm from "node:vm";

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
const flush = async () => {
  for (let i = 0; i < 8; i++) await Promise.resolve();
};
function harness() {
  const reads = [],
    events = [],
    timers = new Map();
  let timerId = 0;
  const reader = createTaskListReader(
    {
      read(signal) {
        const d = deferred();
        reads.push({ ...d, signal });
        return d.promise;
      },
      onStart: () => events.push(["start"]),
      onData: (data) => events.push(["data", data]),
      onError: (error) => events.push(["error", error.message]),
      onSettled: () => events.push(["settled"]),
    },
    {
      setTimeout(fn, ms) {
        assert.equal(ms, 15000);
        const id = ++timerId;
        timers.set(id, fn);
        return id;
      },
      clearTimeout(id) {
        timers.delete(id);
      },
    },
  );
  return { reader, reads, events, timers };
}

test("reader is inert until its view effect activates it", async () => {
  const h = harness();
  assert.equal(h.reader.run(), false);
  await flush();
  assert.deepEqual(h.events, []);
  assert.equal(h.reads.length, 0);
  assert.equal(h.timers.size, 0);
});
test("post-control refresh retires old polling response, including signal-ignoring transport", async () => {
  const h = harness();
  h.reader.activate();
  h.reader.run();
  await flush();
  h.reader.run();
  await flush();
  assert(h.reads[0].signal.aborted);
  h.reads[1].resolve("paused");
  await flush();
  h.reads[0].resolve("running");
  await flush();
  assert.deepEqual(h.events, [["start"], ["start"], ["data", "paused"], ["settled"]]);
  assert.equal(h.timers.size, 0);
});
test("old read cannot publish even before its replacement completes", async () => {
  const h = harness();
  h.reader.activate();
  h.reader.run();
  await flush();
  h.reader.run();
  await flush();
  h.reads[0].resolve("old");
  await flush();
  assert.deepEqual(h.events, [["start"], ["start"]]);
  h.reads[1].resolve("new");
  await flush();
  assert.deepEqual(h.events.at(-2), ["data", "new"]);
});
test("stale rejection cannot clear replacement loading or display an obsolete error", async () => {
  const h = harness();
  h.reader.activate();
  h.reader.run();
  await flush();
  h.reader.run();
  await flush();
  h.reads[0].reject(new Error("stale error"));
  await flush();
  assert.deepEqual(h.events, [["start"], ["start"]]);
  h.reads[1].reject(new Error("current error"));
  await flush();
  assert.deepEqual(h.events.slice(-2), [["error", "current error"], ["settled"]]);
});
test("poll coalesces a slow read without replacing it or resetting its timeout", async () => {
  const h = harness();
  h.reader.activate();
  h.reader.run();
  await flush();
  const before = [...h.timers.keys()];
  for (let i = 0; i < 5; i++) assert.equal(h.reader.run(false), false);
  assert.deepEqual([...h.timers.keys()], before);
  assert.equal(h.reads.length, 1);
  h.reads[0].resolve("slow data");
  await flush();
  assert.deepEqual(h.events.at(-2), ["data", "slow data"]);
  assert.equal(h.reader.run(false), true);
  await flush();
  assert.equal(h.reads.length, 2);
  h.reader.deactivate();
});
test("hanging read expires, releases ownership and permits a later poll", async () => {
  const h = harness();
  h.reader.activate();
  h.reader.run();
  await flush();
  [...h.timers.values()][0]();
  assert(h.reads[0].signal.aborted);
  assert.deepEqual(h.events.slice(-2), [["error", "Read timed out. Try again."], ["settled"]]);
  assert.equal(h.reader.run(false), true);
  await flush();
  h.reads[0].resolve("late expired");
  await flush();
  assert.equal(h.events.filter(([kind]) => kind === "data").length, 0);
  h.reads[1].resolve("retry");
  await flush();
  assert.deepEqual(h.events.at(-2), ["data", "retry"]);
});
test("known failure permits explicit read retry without any mutation retry", async () => {
  const h = harness();
  h.reader.activate();
  h.reader.run();
  await flush();
  h.reads[0].reject(new Error("read denied"));
  await flush();
  h.reader.run();
  await flush();
  h.reads[1].resolve([]);
  await flush();
  assert.deepEqual(h.events.at(-2), ["data", []]);
});
test("effect cleanup retires its owner and removes the deadline", async () => {
  const h = harness();
  h.reader.activate();
  h.reader.run();
  await flush();
  h.reader.deactivate();
  assert(h.reads[0].signal.aborted);
  assert.equal(h.timers.size, 0);
  assert.equal(h.reader.run(), false);
  h.reads[0].resolve("unmounted");
  await flush();
  assert.deepEqual(h.events, [["start"]]);
});
test("cleanup/setup replay starts fresh even when old request never resolves", async () => {
  const h = harness();
  h.reader.activate();
  h.reader.run();
  await flush();
  h.reader.deactivate();
  h.reader.activate();
  assert.equal(h.reader.run(false), true);
  await flush();
  assert.equal(h.reads.length, 2);
  h.reads[1].resolve("replay");
  await flush();
  h.reads[0].reject(new Error("retired"));
  await flush();
  assert.deepEqual(h.events, [["start"], ["start"], ["data", "replay"], ["settled"]]);
});
test("explicit retirement protects locally acknowledged category edits", async () => {
  const h = harness();
  h.reader.activate();
  h.reader.run();
  await flush();
  h.reader.retire();
  h.reads[0].resolve("old category");
  await flush();
  assert.deepEqual(h.events, [["start"]]);
  h.reader.run();
  await flush();
  h.reads[1].resolve("new category");
  await flush();
  assert.deepEqual(h.events.at(-2), ["data", "new category"]);
});
test("same-turn superseding refresh never dispatches the retired request", async () => {
  const h = harness();
  h.reader.activate();
  h.reader.run();
  h.reader.run();
  await flush();
  assert.equal(h.reads.length, 1);
  h.reads[0].resolve("only owner");
  await flush();
  assert.deepEqual(h.events, [["start"], ["start"], ["data", "only owner"], ["settled"]]);
});
test("independent task and category readers cannot retire one another", async () => {
  const tasks = harness(),
    cats = harness();
  tasks.reader.activate();
  cats.reader.activate();
  tasks.reader.run();
  cats.reader.run();
  await flush();
  tasks.reader.run();
  await flush();
  assert.equal(cats.reads[0].signal.aborted, false);
  cats.reads[0].resolve("categories");
  await flush();
  assert.deepEqual(cats.events.at(-2), ["data", "categories"]);
  tasks.reader.deactivate();
});
test("synchronous read and invalid payload handler failures release the reader", async () => {
  const events = [];
  const outcomes = [
    () => {
      throw new Error("sync");
    },
    () => Promise.resolve(null),
  ];
  const reader = createTaskListReader({
    read: () => {
      const next = outcomes.shift();
      assert(next);
      return next();
    },
    onStart() {
      /* No view state needed in this callback-error probe. */
    },
    onData() {
      throw new Error("invalid payload");
    },
    onError: (e) => events.push(e.message),
    onSettled: () => events.push("settled"),
  });
  reader.activate();
  reader.run();
  await flush();
  reader.run();
  await flush();
  assert.deepEqual(events, ["sync", "settled", "invalid payload", "settled"]);
  reader.deactivate();
});

const root = new URL("../../", import.meta.url);
const apiSource = await readFile(new URL("web/src/lib/api.ts", root), "utf8");
const page = await readFile(new URL("web/src/app/(main)/function/tasks/page.tsx", root), "utf8");
function section(source, start, end) {
  const from = source.indexOf(start),
    to = source.indexOf(end, from + start.length);
  assert(from >= 0 && to > from, start);
  return source.slice(from, to);
}
test("actual GET methods forward optional AbortSignal without changing task/category DTOs", async () => {
  const calls = [],
    controller = new AbortController();
  const context = vm.createContext({
    ApiError,
    MOCK: false,
    getToken: () => null,
    arr: (v) => v ?? [],
    fetch: async (url, options) => {
      calls.push({ url, options });
      return {
        status: 200,
        ok: true,
        json: async () =>
          url.endsWith("/tasks") ? { tasks: [{ id: "17" }], active: "17" } : { categories: [{ id: 4 }] },
      };
    },
  });
  const http = section(apiSource, "export async function http<", "\n// sseUrl").replace("export async", "async");
  const tasks = section(apiSource, "  tasks:", "  task:");
  const categories = section(apiSource, "  taskCategories:", "  createTaskCategory:");
  vm.runInContext(stripTypeScriptTypes(`${http}\nglobalThis.api={${tasks}${categories}};`), context);
  assert.equal((await context.api.tasks(controller.signal)).active, "17");
  assert.equal((await context.api.taskCategories(controller.signal))[0].id, 4);
  assert.deepEqual(
    calls.map((c) => c.url),
    ["/api/tasks", "/api/task-categories"],
  );
  for (const call of calls) {
    assert.equal(call.options.signal, controller.signal);
    assert.equal(call.options.method, undefined);
    assert.equal(call.options.body, undefined);
  }
  await context.api.tasks();
  assert.equal(calls[2].options.signal, undefined);
});
test("real page wires both readers, coalescing poll, replay cleanup and category-move retirement", () => {
  assert.match(page, /read: \(signal\) => api\.tasks\(signal\)/);
  assert.match(page, /read: \(signal\) => api\.taskCategories\(signal\)/);
  assert.match(page, /setInterval\(\(\) => taskReader\.run\(false\), POLL_MS\)/);
  assert.match(page, /taskReader\.activate\(\);[\s\S]*categoryReader\.activate\(\);/);
  assert.match(page, /taskReader\.deactivate\(\);\s*categoryReader\.deactivate\(\);/);
  const move = section(page, "  const applyTaskCategoryMove", "\n  React.useEffect");
  assert(move.indexOf("taskReader.retire()") < move.indexOf("setTasks("));
  assert.match(move, /loadCategories\(\);\s*load\(\);/);
});
test("initial unavailable read cannot render successful empty state and failures expose explicit retries", () => {
  assert.match(page, /taskListPlaceholder\(tasksLoaded, tasksLoading, tasks.length, filtered.length\)/);
  assert.match(page, /Loading tasks…/);
  assert.match(page, /Task list unavailable\. Retry the read above\./);
  assert.match(page, /Retry tasks/);
  assert.match(page, /Retry categories/);
  assert.match(page, /Showing last loaded results/);
});

function pageReaders() {
  const requests = [],
    state = {
      tasks: [],
      loaded: false,
      loading: true,
      error: null,
      categories: [],
      categoriesLoaded: false,
      categoryError: null,
    };
  const loadingUpdates = [];
  let cleanup;
  const context = vm.createContext({
    createTaskListReader,
    POLL_MS: 10000,
    lastRef: { current: "" },
    setTasks: (value) => {
      state.tasks = typeof value === "function" ? value(state.tasks) : value;
    },
    setTasksLoaded: (value) => {
      state.loaded = value;
    },
    setTasksLoading: (value) => {
      loadingUpdates.push(value);
      state.loading = value;
    },
    setTasksError: (value) => {
      state.error = value;
    },
    setCategories: (value) => {
      state.categories = value;
    },
    setCategoriesLoaded: (value) => {
      state.categoriesLoaded = value;
    },
    setCategoriesError: (value) => {
      state.categoryError = value;
    },
    React: {
      useState: (init) => [init()],
      useCallback: (fn) => fn,
      useEffect: (effect) => {
        cleanup = effect();
      },
    },
    setInterval: () => 1,
    clearInterval() {
      /* This harness never schedules a real interval. */
    },
    api: Object.fromEntries(
      ["tasks", "taskCategories"].map((kind) => [
        kind,
        (signal) => {
          const d = deferred();
          requests.push({ kind, signal, ...d });
          return d.promise;
        },
      ]),
    ),
  });
  vm.runInContext(
    stripTypeScriptTypes(
      section(page, "  const [taskReader]", "\n  const refreshCategoriesAndTasks") +
        "\nglobalThis.actions={load,loadCategories,poll:()=>taskReader.run(false)};",
    ),
    context,
  );
  return { state, requests, loadingUpdates, actions: context.actions, cleanup: () => cleanup() };
}
test("actual page callbacks preserve the last confirmed rows on error and distinguish first-read failure", async () => {
  const h = pageReaders();
  await flush();
  assert.equal(h.state.loading, true);
  assert.equal(h.state.loaded, false);
  h.requests[0].reject(new Error("not available"));
  h.requests[1].resolve([]);
  await flush();
  assert.equal(h.state.loading, false);
  assert.equal(h.state.loaded, false);
  assert(h.state.error);
  h.actions.load();
  await flush();
  h.requests[2].resolve({ tasks: [{ id: "17", status: "paused" }], active: "17" });
  await flush();
  assert.equal(h.state.loaded, true);
  assert.equal(h.state.loading, false);
  assert.equal(h.state.tasks[0].active, true);
  assert.equal(h.state.error, null);
  h.actions.load();
  await flush();
  h.requests[3].reject(new Error("later failure"));
  await flush();
  assert.equal(h.state.tasks[0].status, "paused");
  assert.equal(h.state.loaded, true);
  assert(h.state.error);
  h.cleanup();
});
test("actual page callbacks discard pre-refresh task/category results and cleanup ignores late responses", async () => {
  const h = pageReaders();
  await flush();
  h.actions.load();
  h.actions.loadCategories();
  await flush();
  h.requests[2].resolve({ tasks: [{ id: "17", status: "paused" }], active: "" });
  h.requests[3].resolve([{ id: 4 }]);
  await flush();
  h.requests[0].resolve({ tasks: [{ id: "17", status: "running" }], active: "" });
  h.requests[1].resolve([{ id: 3 }]);
  await flush();
  assert.equal(h.state.tasks[0].status, "paused");
  assert.equal(h.state.categories[0].id, 4);
  h.actions.load();
  await flush();
  h.cleanup();
  h.requests[4].resolve({ tasks: [], active: "" });
  await flush();
  assert.equal(h.state.tasks.length, 1);
});

test("actual placeholder never calls failed initial reads an empty success", () => {
  const context = vm.createContext({});
  vm.runInContext(
    stripTypeScriptTypes(
      section(page, "function taskListPlaceholder(", "export default function TasksPage()") +
        "\nglobalThis.message=taskListPlaceholder;",
    ),
    context,
  );
  assert.equal(context.message(false, true, 0, 0), "Loading tasks…");
  assert.equal(context.message(false, false, 0, 0), "Task list unavailable. Retry the read above.");
  assert.match(context.message(true, false, 0, 0), /^No tasks yet/);
  assert.equal(context.message(true, false, 1, 0), "No matching tasks.");
  assert.equal(context.message(true, true, 1, 1), "");
});

test("actual background poll keeps errors until success and does not toggle foreground loading", async () => {
  const h = pageReaders();
  await flush();
  h.requests[0].resolve({ tasks: [{ id: "17" }], active: "" });
  h.requests[1].resolve([]);
  await flush();
  h.actions.poll();
  await flush();
  assert.deepEqual(h.loadingUpdates, [true, false]);
  h.requests[2].reject(new Error("offline"));
  await flush();
  assert(h.state.error);
  h.actions.poll();
  await flush();
  assert(h.state.error);
  assert.equal(h.state.loading, false);
  h.requests[3].resolve({ tasks: [{ id: "17" }], active: "" });
  await flush();
  assert.equal(h.state.error, null);
  assert.equal(h.loadingUpdates.filter(Boolean).length, 1);
  h.cleanup();
});

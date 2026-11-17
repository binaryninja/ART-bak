import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { createLogHistory, LOG_ROW_LIMIT, logRowKey, mergeLogRows, oldestLogId } from "../src/lib/log-history-state.mjs";

const line = (seq, db_id, text = `line ${seq || db_id}`) => ({
  seq, ...(db_id ? { db_id } : {}), ts: "2026-11-17T12:00:00Z", level: "info", tag: "test", text,
});
const page = (items, has_more = false) => ({ items, has_more });
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };

function harness(limit) {
  const requests = [], states = [], timers = new Map();
  let id = 0;
  const history = createLogHistory({
    limit,
    read(path, signal) {
      return new Promise((resolve, reject) => requests.push({ path, signal, resolve, reject }));
    },
    onChange: (state) => states.push(state),
  }, {
    setTimeout(fn, ms) { assert.equal(ms, 15000); timers.set(++id, fn); return id; },
    clearTimeout(key) { timers.delete(key); },
  });
  return { history, requests, states, timers, get state() { return states.at(-1); } };
}

test("history identities and cursors use db_id while seq remains the wire value zero", () => {
  const merged = mergeLogRows([line(900, 20)], [line(0, 18), line(0, 19)], true);
  assert.deepEqual(merged.map((l) => [l.seq, l.db_id]), [[0, 18], [0, 19], [900, 20]]);
  assert.equal(oldestLogId(merged), 18);
  assert.equal(new Set(merged.map(logRowKey)).size, 3);
});

test("repeated identical messages with different sequence numbers are distinct", () => {
  const merged = mergeLogRows([line(1, undefined, "same")], [line(2, undefined, "same")]);
  assert.equal(merged.length, 2);
  assert.notEqual(logRowKey(merged[0]), logRowKey(merged[1]));
});

test("replayed snapshot promotes live identity with db_id and stale frames cannot erase it", () => {
  const live = line(10), persisted = line(10, 100);
  const merged = mergeLogRows([live], [persisted]);
  assert.deepEqual(merged, [persisted]);
  assert.deepEqual(mergeLogRows(merged, [live]), [persisted]);
});

test("history and SSE replay deduplicate in either completion order and retain live seq", () => {
  const history = line(0, 100, "stored"), live = line(10, 100, "live");
  assert.deepEqual(mergeLogRows([history], [live]), [live]);
  assert.deepEqual(mergeLogRows([live], [history], true), [live]);
});

test("a replay carrying both identities collapses an earlier unmatched history/live pair", () => {
  const live = line(10), history = line(0, 100, "database timestamp may differ");
  assert.deepEqual(mergeLogRows([history, live], [line(10, 100)]), [line(10, 100)]);
});

test("reused process counters do not hide new rows or merge distinct persisted IDs", () => {
  const a = line(1, 100, "same"), b = line(1, 200, "same");
  assert.deepEqual(mergeLogRows([a], [b]), [a, b]);
  assert.equal(mergeLogRows([line(1)], [{ ...line(1), ts: "2026-11-18T12:00:00Z" }]).length, 2);
  assert.equal(mergeLogRows([line(1)], [line(1, undefined, "new process")]).length, 2);
});

test("history cannot infer a live join from matching message text and timestamps", () => {
  const live = line(10, undefined, "same"), history = line(0, 100, "same");
  assert.deepEqual(mergeLogRows([live], [history], true), [history, live]);
});

test("persisted rows are ordered by database identity across late replay and history", () => {
  const newer = [line(0, 30), line(0, 40)];
  const replay = [line(80, 10), line(90, 20), line(100, 30)];
  const a = replay.reduce((rows, next) => mergeLogRows(rows, [next]), newer);
  const b = mergeLogRows(replay, newer, true);
  assert.deepEqual(a, b);
  assert.deepEqual(a.map((l) => l.db_id), [10, 20, 30, 40]);
});

test("Go restored history before startup logs is not reordered by larger ring sequences", () => {
  const restored = line(500, 100), startup = line(1), recent = line(501);
  assert.deepEqual(mergeLogRows([restored, startup], [recent]), [restored, startup, recent]);
});

test("default retention stays at 5000 for history, replay, and new live rows", () => {
  const rows = Array.from({ length: LOG_ROW_LIMIT + 3 }, (_, i) => line(i + 1, i + 1));
  const retained = mergeLogRows([], rows);
  assert.equal(retained.length, 5000);
  assert.equal(retained[0].db_id, 4);
  const after = mergeLogRows(retained, [line(6000, 6000)]);
  assert.equal(after.length, 5000);
  assert.equal(after[0].db_id, 5);
});

test("controller is inert before activation and ignores malformed SSE frames", () => {
  const h = harness();
  assert.equal(h.history.loadOlder(), false);
  assert.equal(h.history.append(line(1)), false);
  h.history.activate();
  for (const invalid of [null, [], {}, line(0, 1), { ...line(1), level: "debug" },
    { ...line(1), seq: 1.5 }, { ...line(1), seq: Number.MAX_SAFE_INTEGER + 1 },
    { ...line(1), db_id: "3" }, { ...line(1), tag: null }, { ...line(1), text: {} }]) {
    assert.equal(h.history.append(invalid), false);
  }
  assert.deepEqual(h.state.lines, []);
});

test("history can be requested before a persisted replay row arrives", async () => {
  const h = harness();
  h.history.activate();
  h.history.append(line(900));
  assert.equal(h.history.loadOlder(), true);
  assert.equal(h.history.loadOlder(), false); // synchronous double-click coalesces
  await flush();
  assert.equal(h.requests[0].path, "/logs/history?limit=200");
  h.requests[0].resolve(page([line(0, 100)], true));
  await flush();
  assert.deepEqual(h.state.lines.map((l) => l.seq), [0, 900]);
  h.history.loadOlder();
  await flush();
  assert.equal(h.requests[1].path, "/logs/history?before=100&limit=200");
  h.history.deactivate();
});

test("live events arriving during an older read are retained at settlement", async () => {
  const h = harness();
  h.history.activate(); h.history.append(line(90, 100)); h.history.loadOlder();
  await flush();
  h.history.append(line(91, 101));
  h.requests[0].resolve(page([line(0, 98), line(0, 99)]));
  await flush();
  assert.deepEqual(h.state.lines.map((l) => l.db_id), [98, 99, 100, 101]);
  assert.equal(h.state.loadingHistory, false);
  assert.equal(h.timers.size, 0);
});

test("an exhausted history response stays exhausted when live IDs change", async () => {
  const h = harness();
  h.history.activate(); h.history.append(line(90, 100)); h.history.loadOlder();
  await flush(); h.requests[0].resolve(page([])); await flush();
  h.history.append(line(91, 101));
  assert.equal(h.state.hasMore, false);
  assert.equal(h.history.loadOlder(), false);
});

test("Clear retires in-flight history, resets its cursor, and cannot be undone by late completion", async () => {
  const h = harness();
  h.history.activate(); h.history.append(line(20, 100)); h.history.loadOlder();
  await flush(); h.history.clear();
  assert(h.requests[0].signal.aborted);
  assert.equal(h.state.loadingHistory, false);
  assert.deepEqual(h.state.lines, []);
  h.history.loadOlder(); await flush();
  assert.equal(h.requests[1].path, "/logs/history?limit=200");
  h.requests[0].resolve(page([line(0, 90)])); await flush();
  assert.deepEqual(h.state.lines, []);
  assert.equal(h.state.loadingHistory, true);
  h.requests[1].resolve(page([line(0, 200)])); await flush();
  assert.deepEqual(h.state.lines.map((l) => l.db_id), [200]);
});

test("a retired rejection cannot display an error or clear a replacement busy state", async () => {
  const h = harness();
  h.history.activate(); h.history.loadOlder(); await flush();
  h.history.clear(); h.history.loadOlder(); await flush();
  h.requests[0].reject(new Error("obsolete")); await flush();
  assert.equal(h.state.error, null); assert.equal(h.state.loadingHistory, true);
  h.requests[1].resolve(page([])); await flush();
  assert.equal(h.state.loadingHistory, false);
});

test("cleanup and setup reject old completion even when transport ignores cancellation", async () => {
  const h = harness();
  h.history.activate(); h.history.loadOlder(); await flush();
  h.history.deactivate();
  const count = h.states.length;
  h.requests[0].resolve(page([line(0, 1)])); await flush();
  assert.equal(h.states.length, count);
  assert.equal(h.history.append(line(2)), false);
  h.history.activate();
  assert.equal(h.state.loadingHistory, false);
  assert.equal(h.history.loadOlder(), true);
  h.history.deactivate(); await flush();
  assert.equal(h.requests.length, 1); // retired before the transport started
  assert.equal(h.timers.size, 0);
});

test("deadline releases a hanging read, permits retry, and rejects late data", async () => {
  const h = harness();
  h.history.activate(); h.history.loadOlder(); await flush();
  [...h.timers.values()][0]();
  assert(h.requests[0].signal.aborted);
  assert.equal(h.state.loadingHistory, false);
  assert.match(h.state.error, /Try again/);
  h.history.loadOlder(); await flush();
  h.requests[0].resolve(page([line(0, 1)])); await flush();
  assert.deepEqual(h.state.lines, []); assert.equal(h.state.loadingHistory, true);
  h.requests[1].resolve(page([line(0, 2)])); await flush();
  assert.equal(h.state.error, null);
  assert.deepEqual(h.state.lines.map((l) => l.db_id), [2]);
});

test("network and JSON failures preserve displayed rows and permit explicit retry", async () => {
  for (const error of [new TypeError("network"), new SyntaxError("JSON")]) {
    const h = harness();
    h.history.activate(); h.history.append(line(1, 20)); h.history.loadOlder(); await flush();
    h.requests[0].reject(error); await flush();
    assert.deepEqual(h.state.lines, [line(1, 20)]);
    assert.match(h.state.error, /Could not load/);
    assert.equal(h.history.loadOlder(), true);
    h.history.deactivate();
  }
});

test("malformed or non-progressing history responses cannot mutate rows or exhaustion", async () => {
  for (const data of [null, {}, page(null), page([], true), page([line(1, 5)]),
    page([line(0)]), page([line(0, 20)]), page([line(0, 21)]),
    { items: [], has_more: "false" }, page([{ ...line(0, 5), text: null }])]) {
    const h = harness();
    h.history.activate(); h.history.append(line(1, 20)); h.history.loadOlder(); await flush();
    h.requests[0].resolve(data); await flush();
    assert.deepEqual(h.state.lines, [line(1, 20)]);
    assert.equal(h.state.hasMore, true);
    assert.match(h.state.error, /Could not load/);
    assert.equal(h.state.loadingHistory, false);
  }
});

test("bounded window requests only available room and stops at capacity", async () => {
  const h = harness(3);
  h.history.activate(); h.history.append(line(10, 10)); h.history.loadOlder(); await flush();
  assert.equal(h.requests[0].path, "/logs/history?before=10&limit=2");
  h.requests[0].resolve(page([line(0, 8), line(0, 9)], true)); await flush();
  assert.deepEqual(h.state.lines.map((l) => l.db_id), [8, 9, 10]);
  assert.equal(h.history.loadOlder(), false);
  h.history.append(line(11, 11));
  assert.deepEqual(h.state.lines.map((l) => l.db_id), [9, 10, 11]);
  assert.equal(h.history.loadOlder(), false);
});

test("live growth during a page read keeps the window bounded without advancing a discarded cursor", async () => {
  const h = harness(3);
  h.history.activate(); h.history.append(line(10, 10)); h.history.loadOlder(); await flush();
  h.history.append(line(11, 11)); h.history.append(line(12, 12));
  h.requests[0].resolve(page([line(0, 8), line(0, 9)], true)); await flush();
  assert.deepEqual(h.state.lines.map((l) => l.db_id), [10, 11, 12]);
  assert.equal(oldestLogId(h.state.lines), 10);
  assert.equal(h.history.loadOlder(), false);
});

test("page wires authenticated history reads, lifecycle ownership, stable row keys, and bounded paging", async () => {
  const source = await readFile(new URL("../src/app/(main)/system/logs/page.tsx", import.meta.url), "utf8");
  assert.match(source, /read: \(path, signal\) => http\(path, \{ signal \}\)/);
  assert.match(source, /history\.deactivate\(\)/);
  assert.match(source, /onClick=\{\(\) => history\.clear\(\)\}/);
  assert.match(source, /key=\{logRowKey\(l\)\}/);
  assert.match(source, /hasMore && lines\.length < LOG_ROW_LIMIT/);
  assert.match(source, /role="alert"/);
  assert.doesNotMatch(source, /fetch\(|minSeq|slice\(-3000\)|setHasMore/);
});

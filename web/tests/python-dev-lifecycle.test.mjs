// Fake lifecycle callbacks only. No Next import, sockets or real app shutdown.
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { setImmediate as tick } from "node:timers/promises";
import test from "node:test";
import { createDevelopmentShutdown } from "../dev/python-dev-server.mjs";

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function fixture(overrides = {}) {
  const calls = [], reports = [], signals = new EventEmitter(), appClosed = deferred();
  let serverDone;
  const bridge = { close() { calls.push("bridge"); } };
  const server = { close(done) { calls.push("server"); serverDone = done; }, closeAllConnections() { calls.push("connections"); } };
  const app = { close() { calls.push("app"); return appClosed.promise; } };
  const shutdown = createDevelopmentShutdown({ bridge, server, app, signals, report: (message) => reports.push(message), ...overrides });
  return { ...shutdown, calls, reports, signals, appClosed, serverDone(error) { serverDone(error); } };
}

test("all shutdown callers share completion until both server and app close", async () => {
  const f = fixture(), first = f.stop();
  assert.equal(f.stop(), first);
  assert.equal(f.signals.listenerCount("SIGINT"), 0); assert.equal(f.signals.listenerCount("SIGTERM"), 0);
  let completed = false; first.then(() => { completed = true; });
  await tick(); assert.deepEqual(f.calls, ["bridge", "server", "connections", "app"]);
  f.appClosed.resolve(); await tick(); assert.equal(completed, false);
  f.serverDone(); await first; assert.equal(completed, true); assert.equal(f.stop(), first);
  assert.deepEqual(f.calls, ["bridge", "server", "connections", "app"]);
});

test("server completion alone does not report app cleanup finished", async () => {
  const f = fixture(), pending = f.stop(); let done = false; pending.then(() => { done = true; });
  await tick(); f.serverDone(); await tick(); assert.equal(done, false);
  f.appClosed.resolve(); await pending; assert.equal(done, true);
});

test("cleanup errors retain one sanitized rejection and still attempt remaining closes", async () => {
  const calls = [];
  const f = fixture({
    bridge: { close() { calls.push("bridge"); throw new Error("private bridge details"); } },
    server: { close(done) { calls.push("server"); done(new Error("private server details")); }, closeAllConnections() { calls.push("connections"); } },
    app: { close() { calls.push("app"); return Promise.reject(new Error("private Next details")); } },
  });
  const pending = f.stop();
  await assert.rejects(pending, (error) => error.message === "Development frontend cleanup failed" && error.cause === undefined);
  assert.equal(f.stop(), pending); await assert.rejects(f.stop(), /Development frontend cleanup failed/);
  assert.deepEqual(calls, ["bridge", "server", "connections", "app"]); assert.deepEqual(f.reports, []);
});

test("signals remove handlers and catch failures without logging private errors", async () => {
  const f = fixture();
  assert.equal(f.signals.listenerCount("SIGINT"), 1); assert.equal(f.signals.listenerCount("SIGTERM"), 1);
  f.signals.emit("SIGINT");
  assert.equal(f.signals.listenerCount("SIGINT"), 0); assert.equal(f.signals.listenerCount("SIGTERM"), 0);
  f.signals.emit("SIGTERM");
  await tick(); f.serverDone(); f.appClosed.reject(new Error("private Next cleanup stack/token"));
  // Let the failure settle without any programmatic caller attaching a catch.
  // node:test reports an unhandled rejection if the signal path forgets one.
  await tick();
  assert.deepEqual(f.reports, ["Development frontend cleanup failed."]);
  await assert.rejects(f.stop(), /^Error: Development frontend cleanup failed$/);
  assert.deepEqual(f.calls, ["bridge", "server", "connections", "app"]);
});

test("reporter failures are contained and not-running server close is harmless", async () => {
  const signals = new EventEmitter();
  const shutdown = createDevelopmentShutdown({
    signals,
    bridge: { close() {} },
    server: { close(done) { done(Object.assign(new Error("not started"), { code: "ERR_SERVER_NOT_RUNNING" })); }, closeAllConnections() {} },
    app: { close() { return Promise.reject(new Error("private error")); } },
    report() { throw new Error("private reporter failure"); },
  });
  signals.emit("SIGTERM"); await assert.rejects(shutdown.stop(), /Development frontend cleanup failed/); await tick();
  const healthy = createDevelopmentShutdown({
    signals,
    bridge: { close() {} },
    server: { close(done) { done({ code: "ERR_SERVER_NOT_RUNNING" }); }, closeAllConnections() {} },
    app: { close() {} },
  });
  await healthy.stop();
});

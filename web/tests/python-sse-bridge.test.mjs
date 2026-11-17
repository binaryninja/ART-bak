import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { PassThrough, Writable } from "node:stream";
import { setTimeout as delay, setImmediate as tick } from "node:timers/promises";
import test from "node:test";
import { bridgeConfig, createSseBridge, frontendRequestStatus, isSsePath } from "../dev/python-sse-bridge.mjs";
import { parsePorts } from "../dev/python-dev-server.mjs";

const config = { frontendPort: 3000, backendPort: 8787 };
function req(url = "/api/logs/stream?since=0&token=query-token", headers = {}, method = "GET", rawHeaders) {
  return Object.assign(new EventEmitter(), { url, method, headers: { host: "127.0.0.1:3000", ...headers }, rawHeaders: rawHeaders ?? Object.entries({ host: "127.0.0.1:3000", ...headers }).flat() });
}
class Response extends Writable {
  constructor({ blocked = false } = {}) { super({ highWaterMark: 1 }); this.chunks = []; this.headersSent = false; this.blocked = blocked; }
  _write(chunk, _encoding, done) { this.chunks.push(Buffer.from(chunk)); if (this.blocked) this.release = done; else done(); }
  writeHead(status, headers) { this.statusCode = status; this.headers = headers; this.headersSent = true; }
  flushHeaders() { this.flushed = true; }
  body() { return Buffer.concat(this.chunks).toString(); }
}
function transport() {
  const calls = [];
  return { calls, request(options, callback) {
    const client = Object.assign(new EventEmitter(), { ended: false, destroyed: false, end() { this.ended = true; }, destroy() { this.destroyed = true; } });
    calls.push({ options, callback, client });
    return client;
  }, respond(index = 0, code = 200, headers = {}) {
    const stream = Object.assign(new PassThrough(), { statusCode: code, headers: { "content-type": "text/event-stream", ...headers } });
    calls[index].callback(stream); return stream;
  } };
}
function setup(t, options = {}) {
  const upstream = transport();
  const bridge = createSseBridge({ ...config, ...options }, upstream.request);
  t.after(() => bridge.close());
  return { bridge, upstream };
}

test("configuration requires explicit distinct bounded ports and limits", () => {
  assert.equal(bridgeConfig(config).handshakeMs, 30000);
  assert.deepEqual(parsePorts(["--frontend-port", "3000", "--backend-port", "8787"]), bridgeConfig(config));
  for (const changes of [{ frontendPort: 0 }, { backendPort: "8787" }, { frontendPort: 8787 }, { backendPort: 65536 }, { maxStreams: 0 }, { maxStreams: 17 }, { maxBytes: Infinity }, { idleMs: 0 }, { lifetimeMs: 300001 }, { upstream: "http://evil.invalid" }]) assert.throws(() => bridgeConfig({ ...config, ...changes }));
  for (const args of [[], ["--frontend-port", "03000", "--backend-port", "8787"], ["--frontend-port", "3000", "--backend-port", "8787", "--host", "0.0.0.0"]]) assert.throws(() => parsePorts(args));
});

test("only exact source SSE paths are intercepted, without URL normalization", (t) => {
  const { bridge, upstream } = setup(t);
  for (const path of ["/api/logs/stream", "/api/exploration/activity/stream?task=1", "/api/side-questions/a_b.c:d-1/events"]) assert.equal(isSsePath(path), true);
  for (const path of ["/api/update/stream", "/api/tasks", "/api/logs/stream/", "/api/%6cogs/stream", "/api/a/../logs/stream", "//api/logs/stream", "http://evil.invalid/api/logs/stream", "/api/side-questions/a%2fb/events"]) {
    assert.equal(isSsePath(path), false);
    assert.equal(bridge.handle(req(path), new Response()), false);
  }
  assert.equal(upstream.calls.length, 0);
});

test("raw Host, Origin and framing gates reject aliases, foreign/duplicate headers and unsafe targets", () => {
  assert.equal(frontendRequestStatus(req(), 3000), 0);
  assert.equal(frontendRequestStatus(req(undefined, { origin: "http://127.0.0.1:3000", "x-forwarded-host": "evil.invalid" }), 3000), 0);
  for (const headers of [{ host: "localhost:3000" }, { host: "evil.invalid" }, { origin: "null" }, { origin: "http://127.0.0.1:3001" }, { origin: "http://evil.invalid", "x-forwarded-origin": "http://127.0.0.1:3000" }]) assert.equal(frontendRequestStatus(req(undefined, headers), 3000), 403);
  for (const name of ["Host", "Origin", "Authorization", "Cookie", "Content-Length", "Last-Event-ID", "Connection"]) assert.equal(frontendRequestStatus(req(undefined, {}, "GET", ["Host", "127.0.0.1:3000", name, "x", name, "y"]), 3000), 400);
  for (const headers of [{ "transfer-encoding": "chunked" }, { upgrade: "websocket" }, { expect: "100-continue" }, { trailer: "authorization" }, { connection: "authorization" }, { connection: "keep-alive, cookie" }]) assert.equal(frontendRequestStatus(req(undefined, headers), 3000), 400);
  for (const target of ["//evil.invalid/x", "http://evil.invalid/x", "/api\\logs", "/api#fragment", "/api%zz", "/bad path"]) assert.equal(frontendRequestStatus(req(target), 3000), 400);
  assert.equal(frontendRequestStatus(req(undefined, { "content-length": "1048577" }), 3000), 413);
});

test("only bodyless GET and exact bounded source selectors reach the backend", (t) => {
  const { bridge, upstream } = setup(t);
  const invalid = [req(undefined, {}, "POST"), req(undefined, { "content-length": "1" }), req("/api/logs/stream?token=a&token=b"), req("/api/logs/stream?since=-1"), req("/api/logs/stream?since=9223372036854775808"), req("/api/logs/stream?other=1"), req("/api/exploration/activity/stream?task=0"), req("/api/exploration/activity/stream?since=0"), req("/api/side-questions/x/events?since=0"), req(undefined, { "last-event-id": "not-a-cursor" })];
  for (const request of invalid) { const response = new Response(); assert.equal(bridge.handle(request, response), true); assert.ok([400, 405].includes(response.statusCode)); }
  assert.equal(upstream.calls.length, 0);
});

test("auth/query/cursor remain exact only to fixed backend, without forwarded or hop headers", (t) => {
  const { bridge, upstream } = setup(t);
  const target = "/api/exploration/activity/stream?task=4&since=8&token=a%2Bb%2Fc%3D";
  bridge.handle(req(target, { authorization: "Bearer invalid-higher-priority", cookie: "artex_token=valid-lower-priority", origin: "http://127.0.0.1:3000", "last-event-id": "9", "x-forwarded-host": "evil.invalid", "proxy-authorization": "secret", "accept-encoding": "gzip", "x-extra": "private" }), new Response());
  assert.equal(upstream.calls.length, 1);
  const options = upstream.calls[0].options;
  assert.equal(options.hostname, "127.0.0.1"); assert.equal(options.port, 8787); assert.equal(options.path, target); assert.equal(options.agent, false);
  assert.deepEqual(options.headers, { accept: "text/event-stream", "accept-encoding": "identity", connection: "close", authorization: "Bearer invalid-higher-priority", cookie: "artex_token=valid-lower-priority", origin: "http://127.0.0.1:3000", "last-event-id": "9" });
});

test("first SSE bytes arrive before upstream EOF; frames are not parsed/reencoded", async (t) => {
  const { bridge, upstream } = setup(t);
  const response = new Response(); bridge.handle(req(), response);
  const stream = upstream.respond(0, 200, { "set-cookie": "unwanted=secret", location: "https://evil.invalid", "access-control-allow-origin": "*" });
  stream.write(Buffer.from("id: 4\ndata: {\"x\":"));
  await tick();
  assert.equal(response.flushed, true); assert.equal(response.body(), "id: 4\ndata: {\"x\":"); assert.equal(response.writableEnded, false);
  assert.equal(response.headers["set-cookie"], undefined); assert.equal(response.headers.location, undefined); assert.equal(response.headers["access-control-allow-origin"], undefined);
  stream.end(Buffer.from("1}\n\n")); await tick(); await tick();
  assert.equal(response.body(), "id: 4\ndata: {\"x\":1}\n\n"); assert.equal(response.writableFinished, true); assert.equal(bridge.activeStreams, 0);
});

test("204 stops cleanly; errors and redirects never forward cookies, body secrets or redirect credentials", async (t) => {
  const { bridge, upstream } = setup(t);
  for (const [code, expected] of [[204, 204], [301, 502], [307, 502], [401, 401], [403, 403], [503, 503]]) {
    const response = new Response(); bridge.handle(req(), response);
    const stream = upstream.respond(upstream.calls.length - 1, code, { location: "https://evil.invalid/?token=private", "set-cookie": "secret" });
    await tick(); assert.equal(response.statusCode, expected); assert.doesNotMatch(response.body(), /private|secret/); assert.equal(stream.destroyed, true);
  }
  assert.equal(upstream.calls.length, 6); assert.equal(bridge.activeStreams, 0);
});

test("non-SSE or encoded success is rejected rather than buffered/decompressed", (t) => {
  const { bridge, upstream } = setup(t);
  for (const headers of [{ "content-type": "application/json" }, { "content-encoding": "gzip" }]) {
    const response = new Response(); bridge.handle(req(), response); upstream.respond(upstream.calls.length - 1, 200, headers); assert.equal(response.statusCode, 502);
  }
  assert.equal(bridge.activeStreams, 0);
});

test("concurrency is bounded; disconnect before headers releases upstream and slot", async (t) => {
  const { bridge, upstream } = setup(t, { maxStreams: 1 });
  const first = new Response(); bridge.handle(req(), first);
  const second = new Response(); bridge.handle(req(), second); assert.equal(second.statusCode, 503); assert.equal(upstream.calls.length, 1);
  first.destroy(); await tick(); assert.equal(upstream.calls[0].client.destroyed, true); assert.equal(bridge.activeStreams, 0);
  bridge.handle(req(), new Response()); assert.equal(upstream.calls.length, 2);
});

test("cancellation while downstream is backpressured closes response and upstream", async (t) => {
  const { bridge, upstream } = setup(t);
  const response = new Response({ blocked: true }); bridge.handle(req(), response);
  const stream = upstream.respond(); stream.write(Buffer.alloc(65536)); await tick();
  assert.equal(response.writableNeedDrain, true);
  response.destroy(); await tick(); await tick();
  assert.equal(stream.destroyed, true); assert.equal(upstream.calls[0].client.destroyed, true); assert.equal(bridge.activeStreams, 0);
});

test("byte limit terminates without appending a fake SSE error or completion", async (t) => {
  const { bridge, upstream } = setup(t, { maxBytes: 3 });
  const response = new Response(); bridge.handle(req(), response);
  const stream = upstream.respond(); stream.write("abc"); await tick(); stream.write("d"); await tick(); await tick();
  assert.equal(response.body(), "abc"); assert.equal(response.destroyed, true); assert.equal(stream.destroyed, true); assert.equal(bridge.activeStreams, 0);
});

test("handshake, idle and absolute lifetime are bounded without retries", async (t) => {
  for (const phase of ["handshakeMs", "idleMs", "lifetimeMs"]) {
    const { bridge, upstream } = setup(t, { [phase]: 10 });
    const response = new Response(); bridge.handle(req(), response);
    if (phase !== "handshakeMs") upstream.respond();
    await delay(40);
    assert.equal(bridge.activeStreams, 0); assert.equal(upstream.calls[0].client.destroyed, true); assert.equal(upstream.calls.length, 1);
    if (phase === "handshakeMs") assert.equal(response.statusCode, 504); else assert.equal(response.destroyed, true);
  }
});

test("shutdown seals admission and closes every active stream", async (t) => {
  const { bridge, upstream } = setup(t);
  const pending = req(); bridge.handle(pending, new Response());
  const response = new Response(); bridge.handle(req(), response); const stream = upstream.respond(1); stream.write(": ping\n\n"); await tick();
  bridge.close(); assert.equal(bridge.activeStreams, 0); assert.equal(stream.destroyed, true); assert.ok(upstream.calls.every(({ client }) => client.destroyed));
  const refused = new Response(); bridge.handle(req(), refused); assert.equal(refused.statusCode, 503); assert.equal(upstream.calls.length, 2);
});

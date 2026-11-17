// Real loopback HTTP with synthetic bytes only. No Next, browser, database,
// credentials, provider, external target or non-loopback listener is involved.
import assert from "node:assert/strict";
import { once } from "node:events";
import { createServer, get } from "node:http";
import { connect } from "node:net";
import test from "node:test";
import { createSseBridge } from "../dev/python-sse-bridge.mjs";

async function listen(server) {
  server.listen(0, "127.0.0.1"); await once(server, "listening"); return server.address().port;
}
async function fixture(t, respond) {
  const calls = [];
  const backend = createServer((req, res) => { calls.push({ url: req.url, headers: req.headers }); respond(req, res); });
  const backendPort = await listen(backend);
  let bridge;
  const frontend = createServer({ maxHeaderSize: 16384 }, (req, res) => {
    if (!bridge.handle(req, res)) { res.writeHead(404); res.end(); }
  });
  frontend.on("clientError", (_error, socket) => socket.end("HTTP/1.1 400 Bad Request\r\nConnection: close\r\nContent-Length: 0\r\n\r\n"));
  const frontendPort = await listen(frontend);
  bridge = createSseBridge({ frontendPort, backendPort, handshakeMs: 1000, idleMs: 1000, lifetimeMs: 3000 });
  t.after(async () => {
    bridge.close(); frontend.closeAllConnections(); backend.closeAllConnections();
    await Promise.all([new Promise((resolve) => frontend.close(resolve)), new Promise((resolve) => backend.close(resolve))]);
  });
  function open(path = "/api/logs/stream?since=0&token=synthetic-query", headers = {}) {
    const request = get({ hostname: "127.0.0.1", port: frontendPort, path, headers }, () => {});
    return once(request, "response").then(([response]) => response);
  }
  return { calls, open, frontendPort, backendPort, bridge };
}

test("real HTTP forwards first frame before EOF, exact auth/cursor, and no upstream cookies", { timeout: 5000 }, async (t) => {
  let release;
  const f = await fixture(t, (_req, res) => {
    res.writeHead(200, { "Content-Type": "text/event-stream", "Set-Cookie": "unsupported=synthetic", "Access-Control-Allow-Origin": "*" });
    res.write("id: 8\ndata: first\n\n");
    release = () => res.end("id: 9\ndata: second\n\n");
  });
  const origin = `http://127.0.0.1:${f.frontendPort}`;
  const response = await f.open("/api/exploration/activity/stream?task=1&since=7&token=a%2Bb", { Origin: origin, Authorization: "Bearer synthetic", Cookie: "artex_token=synthetic-cookie", "Last-Event-ID": "8", "X-Forwarded-Host": "evil.invalid" });
  assert.equal(response.statusCode, 200);
  const [first] = await once(response, "data");
  assert.equal(first.toString(), "id: 8\ndata: first\n\n");
  assert.equal(response.complete, false);
  assert.equal(response.headers["set-cookie"], undefined); assert.equal(response.headers["access-control-allow-origin"], undefined);
  assert.equal(f.calls[0].url, "/api/exploration/activity/stream?task=1&since=7&token=a%2Bb");
  assert.equal(f.calls[0].headers.host, `127.0.0.1:${f.backendPort}`);
  assert.equal(f.calls[0].headers.origin, origin); assert.equal(f.calls[0].headers.authorization, "Bearer synthetic"); assert.equal(f.calls[0].headers.cookie, "artex_token=synthetic-cookie"); assert.equal(f.calls[0].headers["last-event-id"], "8");
  assert.equal(f.calls[0].headers["x-forwarded-host"], undefined);
  const rest = []; response.on("data", (chunk) => rest.push(chunk)); const ended = once(response, "end"); release(); await ended;
  assert.equal(Buffer.concat(rest).toString(), "id: 9\ndata: second\n\n");
});

test("real downstream disconnect closes an open upstream stream", { timeout: 5000 }, async (t) => {
  let closed;
  const closure = new Promise((resolve) => { closed = resolve; });
  const f = await fixture(t, (_req, res) => { res.on("close", closed); res.writeHead(200, { "Content-Type": "text/event-stream" }); res.write(": ping\n\n"); });
  const response = await f.open(); await once(response, "data"); response.destroy(); await closure;
  assert.equal(f.bridge.activeStreams, 0);
});

test("real raw duplicate auth/Host framing never reaches upstream", { timeout: 5000 }, async (t) => {
  const f = await fixture(t, (_req, res) => res.end("unexpected"));
  for (const duplicate of ["Authorization: a\r\nAuthorization: b", `Host: 127.0.0.1:${f.frontendPort}`, "Origin: http://evil.invalid\r\nOrigin: http://evil.invalid"]) {
    const socket = connect(f.frontendPort, "127.0.0.1"); await once(socket, "connect");
    const chunks = []; socket.on("data", (chunk) => chunks.push(chunk));
    socket.end(`GET /api/logs/stream?token=synthetic HTTP/1.1\r\nHost: 127.0.0.1:${f.frontendPort}\r\n${duplicate}\r\nConnection: close\r\n\r\n`);
    await once(socket, "end"); assert.match(Buffer.concat(chunks).toString(), /^HTTP\/1\.1 400 /);
  }
  assert.equal(f.calls.length, 0);
});

test("redirects are not followed and error bodies cannot leak through the stream", { timeout: 5000 }, async (t) => {
  const f = await fixture(t, (_req, res) => { res.writeHead(307, { Location: "https://unreachable.invalid/?token=synthetic-secret", "Set-Cookie": "unsupported=secret" }); res.end("private upstream data"); });
  const response = await f.open(); const chunks = []; for await (const chunk of response) chunks.push(chunk);
  assert.equal(response.statusCode, 502); assert.equal(response.headers.location, undefined); assert.equal(response.headers["set-cookie"], undefined);
  assert.doesNotMatch(Buffer.concat(chunks).toString(), /private|secret/); assert.equal(f.calls.length, 1);
});

// Explicit development-only SSE transport. Not a general HTTP proxy or an auth
// service. The caller owns a same-origin frontend and an already authorized
// Python loopback listener. No sockets are opened during module import.
import { request as httpRequest } from "node:http";
import { Transform, pipeline } from "node:stream";

const routes = [
  /^\/api\/logs\/stream$/,
  /^\/api\/exploration\/activity\/stream$/,
  /^\/api\/side-questions\/[A-Za-z0-9_.:-]{1,128}\/events$/,
];
const forwarded = ["authorization", "cookie", "origin", "last-event-id"];
const unique = ["host", "origin", "authorization", "cookie", "content-length", "last-event-id", "connection"];
// An empty Python log stream may emit its first ping/header only after 20s.
const defaults = Object.freeze({ maxStreams: 4, maxBytes: 64 * 1024 * 1024, handshakeMs: 30000, idleMs: 30000, lifetimeMs: 300000 });

function port(value) {
  if (!Number.isInteger(value) || value < 1024 || value > 65535) throw new Error("Explicit unprivileged loopback ports required");
  return value;
}

export function bridgeConfig(options) {
  if (!options || Object.getPrototypeOf(options) !== Object.prototype) throw new Error("Explicit bridge options required");
  const allowed = new Set(["frontendPort", "backendPort", ...Object.keys(defaults)]);
  if (Object.keys(options).some((key) => !allowed.has(key))) throw new Error("Unknown bridge option");
  const frontendPort = port(options.frontendPort);
  const backendPort = port(options.backendPort);
  if (frontendPort === backendPort) throw new Error("Frontend and backend must use different ports");
  const result = { ...defaults, ...options, frontendPort, backendPort };
  for (const [key, lower, upper] of [["maxStreams", 1, 16], ["maxBytes", 1, 64 * 1024 * 1024], ["handshakeMs", 10, 30000], ["idleMs", 10, 60000], ["lifetimeMs", 10, 300000]]) {
    if (!Number.isInteger(result[key]) || result[key] < lower || result[key] > upper) throw new Error("Bounded bridge limits required");
  }
  return Object.freeze(result);
}

export function isSsePath(target) {
  return typeof target === "string" && routes.some((route) => route.test(target.split("?", 1)[0]));
}

// Shared entrance gate for the optional frontend host, before Next or SSE sees
// the request. Exact raw targets avoid URL normalization/authority confusion.
export function frontendRequestStatus(req, frontendPort) {
  const target = req.url;
  if (typeof target !== "string" || target.length > 20000 || !target.startsWith("/") || target.startsWith("//") || /[\\#\s\x00-\x1f\x7f-\uffff]/.test(target) || /%(?![0-9a-f]{2})/i.test(target)) return 400;
  if (!Array.isArray(req.rawHeaders) || req.rawHeaders.length > 128) return 400;
  const count = new Map();
  let bytes = 0;
  for (let index = 0; index < req.rawHeaders.length; index += 2) {
    const name = req.rawHeaders[index];
    const value = req.rawHeaders[index + 1];
    if (typeof name !== "string" || typeof value !== "string" || /[\r\n\0]/.test(value)) return 400;
    bytes += Buffer.byteLength(name) + Buffer.byteLength(value) + 4;
    count.set(name.toLowerCase(), (count.get(name.toLowerCase()) ?? 0) + 1);
  }
  if (bytes > 16384 || unique.some((name) => (count.get(name) ?? 0) > 1)) return 400;
  const authority = `127.0.0.1:${frontendPort}`;
  if (req.headers.host !== authority || (req.headers.origin !== undefined && req.headers.origin !== `http://${authority}`)) return 403;
  if (req.headers.upgrade !== undefined || req.headers["transfer-encoding"] !== undefined || req.headers.expect !== undefined || req.headers.trailer !== undefined) return 400;
  const connection = req.headers.connection;
  if (connection !== undefined && (typeof connection !== "string" || connection.split(",").some((part) => !["close", "keep-alive"].includes(part.trim().toLowerCase())))) return 400;
  const length = req.headers["content-length"];
  if (length !== undefined && (typeof length !== "string" || !/^(0|[1-9][0-9]{0,6})$/.test(length) || Number(length) > 1048576)) return 413;
  return 0;
}

function validCursor(value, nonzero = false) {
  return /^[0-9]{1,19}$/.test(value) && BigInt(value) < 2n ** 63n && (!nonzero || BigInt(value) > 0n);
}

function streamRequestStatus(req) {
  if (req.method !== "GET") return 405;
  if (req.headers["content-length"] !== undefined && req.headers["content-length"] !== "0") return 400;
  const path = req.url.split("?", 1)[0];
  const query = new URLSearchParams(req.url.includes("?") ? req.url.slice(req.url.indexOf("?") + 1) : "");
  const activity = path === "/api/exploration/activity/stream";
  const allowed = new Set(path === "/api/logs/stream" ? ["since", "token"] : activity ? ["task", "since", "token"] : ["token"]);
  const seen = new Set();
  for (const [key, value] of query) {
    if (!allowed.has(key) || seen.has(key) || (key === "token" && value.length > 16384) || (key === "since" && !validCursor(value)) || (key === "task" && !validCursor(value, true))) return 400;
    seen.add(key);
  }
  if (activity && !seen.has("task")) return 400;
  const last = req.headers["last-event-id"];
  if (last !== undefined && (typeof last !== "string" || !validCursor(last))) return 400;
  return 0;
}

export function rejectRequest(res, status) {
  if (res.headersSent) { res.destroy(); return; }
  const body = Buffer.from('{"error":"Development transport unavailable"}\n');
  res.writeHead(status, { "Content-Type": "application/json", "Content-Length": body.length, "Cache-Control": "no-store", "Connection": "close" });
  res.end(body);
}

export function createSseBridge(options, request = httpRequest) {
  const config = bridgeConfig(options);
  const active = new Set();
  let stopped = false;

  function handle(req, res) {
    if (!isSsePath(req.url)) return false;
    const status = frontendRequestStatus(req, config.frontendPort) || streamRequestStatus(req);
    if (status) { rejectRequest(res, status); return true; }
    if (stopped || active.size >= config.maxStreams) { rejectRequest(res, 503); return true; }

    let upstream;
    let incoming;
    let finished = false;
    let headerTimer;
    let idleTimer;
    let lifetimeTimer;
    function finish() {
      if (finished) return;
      finished = true;
      clearTimeout(headerTimer); clearTimeout(idleTimer); clearTimeout(lifetimeTimer);
      active.delete(cancel);
      req.off("aborted", cancel);
      res.off("close", cancel);
      incoming?.destroy();
      upstream?.destroy();
    }
    function cancel() { finish(); if (!res.writableFinished) res.destroy(); }
    function fail(status = 502) { if (finished) return; finish(); rejectRequest(res, status); }
    function idle() { clearTimeout(idleTimer); idleTimer = setTimeout(() => fail(504), config.idleMs); }

    active.add(cancel);
    req.on("aborted", cancel);
    res.on("close", cancel);
    headerTimer = setTimeout(() => fail(504), config.handshakeMs);
    lifetimeTimer = setTimeout(() => fail(504), config.lifetimeMs);
    const headers = { accept: "text/event-stream", "accept-encoding": "identity", connection: "close" };
    for (const name of forwarded) if (req.headers[name] !== undefined) headers[name] = req.headers[name];
    try {
      // No DNS, environment proxy, redirect following, URL joining or retries.
      // Host is generated for this fixed backend; the original Origin remains
      // unchanged for its explicit frontend_origin gate. Auth precedence stays
      // with the real backend, including invalid bearer over valid cookie/query.
      upstream = request({ protocol: "http:", hostname: "127.0.0.1", port: config.backendPort, method: "GET", path: req.url, headers, agent: false, maxHeaderSize: 16384 }, (response) => {
        if (finished) { response.destroy(); return; }
        incoming = response;
        clearTimeout(headerTimer);
        const code = response.statusCode;
        if (code === 204) { finish(); res.writeHead(204, { "Cache-Control": "no-store", Connection: "close" }); res.end(); return; }
        if (code !== 200) { fail(Number.isInteger(code) && code >= 400 && code <= 599 ? code : 502); return; }
        const type = response.headers["content-type"];
        const encoding = response.headers["content-encoding"];
        if (typeof type !== "string" || !/^text\/event-stream(?:\s*;\s*charset=utf-8)?$/i.test(type) || (encoding !== undefined && encoding !== "identity")) { fail(); return; }
        // Never copy Set-Cookie, Location, CORS, framing or other arbitrary
        // upstream headers into the frontend origin.
        res.writeHead(200, { "Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no", "X-Content-Type-Options": "nosniff", "Connection": "close" });
        res.flushHeaders();
        idle();
        let bytes = 0;
        const bounded = new Transform({ transform(chunk, encoding, done) {
          bytes += chunk.length;
          if (bytes > config.maxBytes) { done(new Error("Stream bound exceeded")); return; }
          idle();
          done(null, chunk);
        } });
        // pipeline propagates backpressure and closes both sides on interruption.
        // It does not wait for a whole SSE frame/body before forwarding bytes.
        pipeline(response, bounded, res, () => finish());
      });
      upstream.on("error", () => fail());
      upstream.end();
    } catch { fail(); }
    return true;
  }

  return Object.freeze({ handle, close() { stopped = true; for (const cancel of [...active]) cancel(); }, get activeStreams() { return active.size; } });
}

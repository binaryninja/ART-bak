// Explicit local prebuilt preview, not a production deployment or a dev-watcher fix.
// Public Next dev:false mode; never sweep global handles or force process exit.

import { createDevelopmentShutdown, parsePorts } from "./python-dev-server.mjs";
import { validatePreviewBuild } from "./python-preview-profile.mjs";
import { createSseBridge, frontendRequestStatus, rejectRequest } from "./python-sse-bridge.mjs";
import { constants } from "node:fs";
import { open } from "node:fs/promises";
import { createServer } from "node:http";
import { fileURLToPath, pathToFileURL } from "node:url";

async function readArtifact(name, limit) {
  const file = await open(
    new URL(`../.next/${name}`, import.meta.url),
    constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK,
  );
  try {
    const stat = await file.stat();
    if (!stat.isFile() || stat.size < 1 || stat.size > limit) throw new Error("Invalid preview build artifact");
    const value = await file.readFile({ encoding: "utf8" });
    if (Buffer.byteLength(value) > limit) throw new Error("Invalid preview build artifact");
    return value;
  } finally {
    await file.close();
  }
}

async function readPreviewBuild(backendPort) {
  const [buildId, required, routes] = await Promise.all([
    readArtifact("BUILD_ID", 256),
    readArtifact("required-server-files.json", 4194304),
    readArtifact("routes-manifest.json", 4194304),
  ]);
  return validatePreviewBuild({ buildId, required: JSON.parse(required), routes: JSON.parse(routes) }, backendPort);
}

export async function run(args) {
  const config = parsePorts(args);
  if (process.env.NEXT_EXPORT === "1" || process.env.NEXT_PUBLIC_MOCK === "1")
    throw new Error("Non-export, non-mock preview required");
  // Validate before importing Next or binding a listener. Runtime environment alone
  // cannot change build-time client flags or the serialized production rewrite.
  await readPreviewBuild(config.backendPort);
  process.env.ARTEX_PYTHON_PREVIEW = "1";
  process.env.NODE_ENV = "production";
  process.env.NEXT_PUBLIC_MOCK = "0";
  process.env.NEXT_PUBLIC_ARTEX_BACKEND_PROFILE = "python";
  process.env.NEXT_PUBLIC_SSE_BASE = "";
  process.env.AUTOPENTEST_API = `http://127.0.0.1:${config.backendPort}`;
  process.env.NEXT_TELEMETRY_DISABLED = "1";
  const { default: next } = await import("next");
  const app = next({
    dev: false,
    hostname: "127.0.0.1",
    port: config.frontendPort,
    dir: fileURLToPath(new URL("../", import.meta.url)),
  });
  try {
    await app.prepare();
  } catch (error) {
    await app.close();
    throw error;
  }
  const fallback = app.getRequestHandler();
  const bridge = createSseBridge(config);
  const log = (event, detail = "") => {
    const stamp = new Date().toISOString();
    console.error(`[preview ${stamp}] ${event}${detail ? ` ${detail}` : ""}`);
  };
  const requestPath = (req) => String(req.url ?? "").split("?", 1)[0] || "/";
  // Trace every non-asset response, including SSE bridge rejections, without
  // ever logging query strings or headers (they can carry session tokens).
  const traceRequest = (req, res, startedAt) => {
    res.on("finish", () => {
      const path = requestPath(req);
      if (path.startsWith("/_next/static/") || path.startsWith("/_next/image")) return;
      log(`${req.method} ${path} -> ${res.statusCode} (${Date.now() - startedAt}ms)`);
    });
  };
  const logHandlerError = (req, res, error) => {
    log(
      `handler error ${req.method} ${requestPath(req)}`,
      `${error?.name ?? "Error"}: ${error?.message ?? "unknown"}\n${error?.stack ?? ""}`,
    );
    // Aborted or already-answered requests cannot receive a meaningful 503;
    // destroying them would truncate an in-flight response.
    if (req.destroyed || res.writableEnded || res.headersSent) return;
    rejectRequest(res, 503);
  };
  const server = createServer(
    // keepAliveTimeout must outlive the browser's pooled-socket idle window.
    // Closing sockets too eagerly races Chrome's connection reuse: a request
    // written to a closing socket never fully arrives, requestTimeout fires,
    // and the client sees a dead connection mid-navigation.
    { maxHeaderSize: 16384, headersTimeout: 5000, requestTimeout: 10000, keepAliveTimeout: 5000 },
    (req, res) => {
      const startedAt = Date.now();
      traceRequest(req, res, startedAt);
      const status = frontendRequestStatus(req, config.frontendPort);
      if (status) {
        log(`gate rejected ${req.method} ${requestPath(req)} -> ${status}`);
        rejectRequest(res, status);
        return;
      }
      if (bridge.handle(req, res)) return;
      try {
        Promise.resolve(fallback(req, res)).catch((error) => logHandlerError(req, res, error));
      } catch (error) {
        logHandlerError(req, res, error);
      }
    },
  );
  server.maxConnections = 32;
  // Deliberately no maxRequestsPerSocket: one-request-per-socket closes forced
  // a close/reuse race on every single request. Concurrency stays bounded by
  // maxConnections, headersTimeout, requestTimeout, and keepAliveTimeout.
  server.on("checkContinue", (_req, res) => rejectRequest(res, 417));
  server.on("checkExpectation", (_req, res) => rejectRequest(res, 417));
  // No upgrade tunnelling, arbitrary TCP forwarding or claimed HMR support.
  server.on("upgrade", (_req, socket) => socket.destroy());
  server.on("connect", (_req, socket) => socket.destroy());
  server.on("clientError", (error, socket) => {
    log(`client error ${error?.code ?? error?.name ?? "unknown"}`);
    // Send a real HTTP response instead of destroying the socket, so a browser
    // mid-navigation receives a refreshable status rather than a dead socket.
    const status = error?.code === "ERR_HTTP_REQUEST_TIMEOUT" ? "408 Request Timeout" : "400 Bad Request";
    socket.end(`HTTP/1.1 ${status}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n`);
  });
  const { stop } = createDevelopmentShutdown({
    bridge,
    server,
    app,
    report: () => {
      process.exitCode = 1;
      console.error("Python preview frontend cleanup failed.");
    },
  });
  try {
    await new Promise((resolve, reject) => {
      server.once("error", reject);
      server.listen(config.frontendPort, "127.0.0.1", resolve);
    });
  } catch (error) {
    await stop();
    throw error;
  }
  return { stop, origin: `http://127.0.0.1:${config.frontendPort}` };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  run(process.argv.slice(2))
    .then(({ origin }) =>
      console.log(`Python preview frontend: ${origin}. Prebuilt, non-watching; browser validation pending.`),
    )
    .catch(() => {
      // Do not expose request URLs, token values, environment or upstream details.
      console.error(
        "Python preview frontend could not start. Check explicit ports and the matching non-mock preview build.",
      );
      process.exitCode = 1;
    });
}

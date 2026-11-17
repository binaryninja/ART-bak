// Explicitly invoked development host. Default npm dev/build/static export are
// unchanged. No credentials, provider, database or backend runtime is created.
import { createServer } from "node:http";
import { fileURLToPath, pathToFileURL } from "node:url";
import { bridgeConfig, createSseBridge, frontendRequestStatus, rejectRequest } from "./python-sse-bridge.mjs";

export function parsePorts(args) {
  if (args.length !== 4 || args[0] !== "--frontend-port" || args[2] !== "--backend-port" || !args[1].match(/^[1-9][0-9]{3,4}$/) || !args[3].match(/^[1-9][0-9]{3,4}$/)) throw new Error("Use --frontend-port PORT --backend-port PORT");
  return bridgeConfig({ frontendPort: Number(args[1]), backendPort: Number(args[3]) });
}

// Dependency-free lifecycle seam. Concurrent/repeated callers share completion;
// signals report only a fixed message, never an underlying cleanup error.
export function createDevelopmentShutdown({ bridge, server, app, signals = process, report = (message) => console.error(message) }) {
  let completion;
  function stop() {
    if (completion) return completion;
    signals.off("SIGINT", onSignal);
    signals.off("SIGTERM", onSignal);
    // Assign before cleanup callbacks can run, including reentrant stop calls.
    completion = Promise.resolve().then(async () => {
      let failed = false;
      try { bridge.close(); } catch { failed = true; }
      const serverClosed = new Promise((resolve) => {
        try {
          server.close((error) => {
            if (error && error.code !== "ERR_SERVER_NOT_RUNNING") failed = true;
            resolve();
          });
        } catch { failed = true; resolve(); }
      });
      try { server.closeAllConnections(); } catch { failed = true; }
      try { await app.close(); } catch { failed = true; }
      await serverClosed;
      if (failed) throw new Error("Development frontend cleanup failed");
    });
    return completion;
  }
  function onSignal() {
    void stop().catch(() => {
      try { report("Development frontend cleanup failed."); } catch { /* Contain reporter failures too. */ }
    });
  }
  signals.once("SIGINT", onSignal);
  signals.once("SIGTERM", onSignal);
  return Object.freeze({ stop });
}

export async function run(args) {
  const config = parsePorts(args);
  if (process.env.NEXT_EXPORT === "1" || process.env.NEXT_PUBLIC_MOCK === "1" || process.env.NODE_ENV === "production") throw new Error("Development-only non-mock frontend required");
  // Set before Next loads/compiles the client. Only this explicit Python host
  // changes these process-local defaults, never a file or production build.
  process.env.NODE_ENV = "development";
  process.env.NEXT_PUBLIC_ARTEX_BACKEND_PROFILE = "python";
  process.env.NEXT_PUBLIC_SSE_BASE = "";
  process.env.AUTOPENTEST_API = `http://127.0.0.1:${config.backendPort}`;
  process.env.NEXT_TELEMETRY_DISABLED = "1";
  const { default: next } = await import("next");
  const app = next({ dev: true, hostname: "127.0.0.1", port: config.frontendPort, dir: fileURLToPath(new URL("../", import.meta.url)) });
  try { await app.prepare(); } catch (error) { await app.close(); throw error; }
  const fallback = app.getRequestHandler();
  const bridge = createSseBridge(config);
  const server = createServer({ maxHeaderSize: 16384, headersTimeout: 5000, requestTimeout: 10000, keepAliveTimeout: 1000 }, (req, res) => {
    const status = frontendRequestStatus(req, config.frontendPort);
    if (status) { rejectRequest(res, status); return; }
    if (bridge.handle(req, res)) return;
    try { Promise.resolve(fallback(req, res)).catch(() => rejectRequest(res, 503)); }
    catch { rejectRequest(res, 503); }
  });
  server.maxConnections = 32;
  server.maxRequestsPerSocket = 1;
  server.on("checkContinue", (_req, res) => rejectRequest(res, 417));
  server.on("checkExpectation", (_req, res) => rejectRequest(res, 417));
  // No upgrade tunnelling, arbitrary TCP forwarding or claimed HMR support.
  server.on("upgrade", (_req, socket) => socket.destroy());
  server.on("connect", (_req, socket) => socket.destroy());
  server.on("clientError", (_error, socket) => socket.destroy());
  const { stop } = createDevelopmentShutdown({ bridge, server, app });
  try {
    await new Promise((resolve, reject) => { server.once("error", reject); server.listen(config.frontendPort, "127.0.0.1", resolve); });
  } catch (error) { await stop(); throw error; }
  return { stop, origin: `http://127.0.0.1:${config.frontendPort}` };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  run(process.argv.slice(2)).then(({ origin }) => console.log(`Development frontend: ${origin}. SSE bridge only; browser validation pending.`)).catch(() => {
    // Do not expose request URLs, token values, environment or upstream details.
    console.error("Python development frontend could not start. Check explicit ports and installed frontend dependencies.");
    process.exitCode = 1;
  });
}

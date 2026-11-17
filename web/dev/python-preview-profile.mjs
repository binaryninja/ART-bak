// Inert build/launch compatibility checks, not signatures or an untrusted-build sandbox.
export const previewEnvironment = Object.freeze({
  NEXT_PUBLIC_ARTEX_BACKEND_PROFILE: "python",
  NEXT_PUBLIC_MOCK: "0",
  NEXT_PUBLIC_SSE_BASE: "",
});

export function previewBackend(value) {
  if (typeof value !== "string" || !/^http:\/\/127\.0\.0\.1:[1-9][0-9]{3,4}$/.test(value))
    throw new Error("Explicit preview loopback backend required");
  const port = Number(value.slice(value.lastIndexOf(":") + 1));
  if (port < 1024 || port > 65535) throw new Error("Explicit preview loopback backend required");
  return value;
}

export function validatePreviewBuild({ buildId, required, routes }, backendPort) {
  const fail = () => {
    throw new Error("Matching Python preview build required");
  };
  if (!Number.isInteger(backendPort)) fail();
  const backend = previewBackend(`http://127.0.0.1:${backendPort}`);
  if (typeof buildId !== "string" || !/^[A-Za-z0-9_-]{1,128}\n?$/.test(buildId)) fail();
  const config = required?.config;
  if (
    required?.version !== 1 ||
    !config ||
    config.output !== undefined ||
    config.distDir !== ".next" ||
    config.basePath !== "" ||
    config.trailingSlash !== false
  )
    fail();
  if (
    !config.env ||
    Object.keys(config.env).length !== 3 ||
    Object.entries(previewEnvironment).some(([key, value]) => config.env[key] !== value)
  )
    fail();
  if (routes?.version !== 3 || routes.basePath !== "") fail();
  const rewrites = routes.rewrites;
  if (
    !rewrites ||
    !Array.isArray(rewrites.beforeFiles) ||
    rewrites.beforeFiles.length ||
    !Array.isArray(rewrites.fallback) ||
    rewrites.fallback.length ||
    !Array.isArray(rewrites.afterFiles) ||
    rewrites.afterFiles.length !== 1
  )
    fail();
  const route = rewrites.afterFiles[0];
  if (
    !route ||
    Object.keys(route).sort().join(",") !== "destination,regex,source" ||
    route.source !== "/api/:path*" ||
    route.destination !== `${backend}/api/:path*` ||
    route.regex !== "^/api(?:/((?:[^/]+?)(?:/(?:[^/]+?))*))?(?:/)?$"
  )
    fail();
  return Object.freeze({ buildId: buildId.trim(), backend });
}

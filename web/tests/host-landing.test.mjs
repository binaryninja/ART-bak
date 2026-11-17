import {
  DEFAULT_HOME,
  readLandingProfile,
  SKILLS_HOME,
  signInLanding,
  startHostLanding,
} from "../src/lib/host-landing.mjs";
import { ApiError } from "../src/lib/mutation-contract.mjs";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const read = (path) => readFileSync(new URL(`../src/${path}`, import.meta.url), "utf8");
const settle = async () => {
  await Promise.resolve();
  await Promise.resolve();
};
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
};
function attempt(readProfile, extra = {}) {
  const navigations = [],
    errors = [],
    signals = [];
  const retire = startHostLanding({
    readProfile: (_token, signal) => {
      signals.push(signal);
      return readProfile(signal);
    },
    getToken: () => "original-token",
    navigate: (destination) => navigations.push(destination),
    onError: (message) => errors.push(message),
    ...extra,
  });
  return { retire, navigations, errors, signals };
}

test("only real Python needs host discovery; legacy and fixture destinations are fixed", () => {
  for (const profile of [undefined, "go", "", "PYTHON", "other"])
    for (const mock of [false, true]) assert.equal(signInLanding(profile, mock), DEFAULT_HOME);
  assert.equal(signInLanding("python", true), DEFAULT_HOME);
  assert.equal(signInLanding("python", false), "/");
  assert.equal(DEFAULT_HOME, "/function/tasks");
  assert.equal(SKILLS_HOME, "/system/skills");
});

test("each exact Skills profile selects Skills without inferring write authority", async () => {
  for (const profile of [
    { profile: "skills-workspace", writes_enabled: false },
    { profile: "skills-workspace", writes_enabled: true },
    { profile: "skills-workspace", writes_enabled: true, deletes_enabled: true },
    { profile: "skills-workspace", writes_enabled: true, imports_enabled: true },
    { profile: "skills-workspace", writes_enabled: true, deletes_enabled: true, imports_enabled: true },
  ]) {
    const run = attempt(() => Promise.resolve(profile));
    await settle();
    assert.deepEqual(run.navigations, [SKILLS_HOME]);
    assert.deepEqual(run.errors, []);
    run.retire();
  }
});

test("only a known ApiError 404 chooses the unchanged full-host destination", async () => {
  const run = attempt(() => Promise.reject(new ApiError(404, {}, "Not found")));
  await settle();
  assert.deepEqual(run.navigations, [DEFAULT_HOME]);
  assert.deepEqual(run.errors, []);
  run.retire();
});

test("denial, unavailable, ambiguous, malformed and forged 404 responses never choose a destination", async () => {
  for (const error of [
    ...[0, 400, 401, 403, 405, 500, 503].map((status) => new ApiError(status, {}, "Synthetic private detail")),
    ...[{ pending: true }, { unknown: true }, { retry: false }].map(
      (payload) => new ApiError(404, payload, "Not confirmed"),
    ),
    { status: 404 },
    null,
    new Error("Synthetic private detail"),
  ]) {
    const run = attempt(() => Promise.reject(error));
    await settle();
    assert.deepEqual(run.navigations, []);
    assert.equal(run.errors.length, 1);
    assert.ok(!run.errors[0].includes("Synthetic private detail"));
    run.retire();
  }
  for (const value of [
    null,
    {},
    [],
    { profile: "skills-workspace", writes_enabled: "true" },
    { profile: "skills-workspace", writes_enabled: false, imports_enabled: true },
    { profile: "skills-workspace", writes_enabled: true, redirect: "https://example.invalid" },
  ]) {
    const run = attempt(() => Promise.resolve(value));
    await settle();
    assert.deepEqual(run.navigations, []);
    assert.equal(run.errors.length, 1);
    run.retire();
  }
});

test("synchronous read failure is explicit and does not escape the ownership helper", () => {
  const run = attempt(() => {
    throw new Error("Synthetic private detail");
  });
  assert.deepEqual(run.navigations, []);
  assert.equal(run.errors.length, 1);
  run.retire();
});

test("no credential means no read and no unauthenticated fallback", () => {
  const run = attempt(() => assert.fail("must not read"), { getToken: () => null });
  assert.equal(run.signals.length, 0);
  assert.deepEqual(run.navigations, []);
  assert.equal(run.errors.length, 1);
  run.retire();
});

test("retired completion cannot navigate or publish an error after cleanup", async () => {
  for (const failure of [false, true]) {
    const pending = deferred();
    const run = attempt(() => pending.promise);
    run.retire();
    assert.equal(run.signals[0].aborted, true);
    if (failure) pending.reject(new ApiError(404, {}, "Not found"));
    else pending.resolve({ profile: "skills-workspace", writes_enabled: false });
    await settle();
    assert.deepEqual(run.navigations, []);
    assert.deepEqual(run.errors, []);
  }
});

test("cleanup/setup replay and retry use a fresh owner even when the retired read hangs", async () => {
  const first = deferred(),
    second = deferred();
  const old = attempt(() => first.promise);
  old.retire();
  const latest = attempt(() => second.promise);
  assert.notEqual(old.signals[0], latest.signals[0]);
  assert.equal(latest.signals[0].aborted, false);
  old.retire();
  assert.equal(latest.signals[0].aborted, false);
  second.resolve({ profile: "skills-workspace", writes_enabled: false });
  await settle();
  first.reject(new ApiError(404, {}, "Late unsupported"));
  await settle();
  assert.deepEqual(old.navigations, []);
  assert.deepEqual(old.errors, []);
  assert.deepEqual(latest.navigations, [SKILLS_HOME]);
  latest.retire();
});

test("a changed or removed token cannot receive the old session's landing decision", async () => {
  for (const next of ["new-token", null]) {
    let token = "original-token";
    const pending = deferred();
    const run = attempt(() => pending.promise, { getToken: () => token });
    token = next;
    pending.resolve({ profile: "skills-workspace", writes_enabled: true });
    await settle();
    assert.deepEqual(run.navigations, []);
    assert.match(run.errors[0], /Sign-in changed/);
    assert.equal(run.signals[0].aborted, true);
    run.retire();
  }
});

test("bounded timeout aborts the read and ignores any late success or unsupported result", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  for (const failure of [false, true]) {
    const pending = deferred();
    const run = attempt(() => pending.promise);
    t.mock.timers.tick(9999);
    assert.equal(run.errors.length, 0);
    t.mock.timers.tick(1);
    assert.match(run.errors[0], /timed out/);
    assert.equal(run.signals[0].aborted, true);
    if (failure) pending.reject(new ApiError(404, {}, "Late unsupported"));
    else pending.resolve({ profile: "skills-workspace", writes_enabled: true });
    await settle();
    assert.deepEqual(run.navigations, []);
    assert.equal(run.errors.length, 1);
    run.retire();
  }
});

test("settled success cancels its deadline and cleanup is idempotent", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const run = attempt(() => Promise.resolve({ profile: "skills-workspace", writes_enabled: false }));
  await settle();
  t.mock.timers.tick(20000);
  run.retire();
  run.retire();
  assert.deepEqual(run.navigations, [SKILLS_HOME]);
  assert.deepEqual(run.errors, []);
});

test("actual proxy retains auth protection and routes only authenticated Python entry to discovery", () => {
  const source = read("proxy.ts");
  const body = source
    .slice(source.indexOf("const AUTH_PAGES"), source.indexOf("export const config"))
    .replace("export function proxy(request: NextRequest)", "function proxy(request)");
  for (const profile of ["python", "go", undefined])
    for (const mock of ["0", "1"]) {
      const context = {
        URL,
        signInLanding,
        process: { env: { NEXT_PUBLIC_ARTEX_BACKEND_PROFILE: profile, NEXT_PUBLIC_MOCK: mock } },
        NextResponse: { next: () => "next", redirect: (url) => url.pathname },
      };
      vm.runInNewContext(`${body}\nglobalThis.run = proxy;`, context);
      const request = (pathname, token) => ({
        nextUrl: { pathname },
        url: `https://example.invalid${pathname}`,
        cookies: { get: () => (token ? { value: token } : undefined) },
      });
      for (const path of ["/login", "/setup", "/login/nested"])
        assert.equal(context.run(request(path, "token")), mock === "1" ? "next" : signInLanding(profile, false));
      assert.equal(context.run(request("/system/skills", null)), mock === "1" ? "next" : "/login");
      assert.equal(context.run(request("/system/skills", "token")), "next");
      assert.equal(context.run(request("/", "token")), "next");
      assert.equal(context.run(request("/login", null)), "next");
    }
});

test("actual login token and submit branches use fixed profile-aware entry without changing credentials", async () => {
  const source = read("app/(auth)/login/page.tsx");
  const effect = source.slice(
    source.indexOf("    // Redirect signed-in users directly"),
    source.indexOf("  }, [router]);"),
  );
  const submit = source
    .slice(source.indexOf("  async function handleSubmit"), source.indexOf("  if (checking)"))
    .replace("e: React.FormEvent", "e");
  for (const profile of ["python", "go", undefined])
    for (const mock of [false, true]) {
      const paths = [],
        credentials = [];
      let currentToken = "existing";
      const context = {
        signInLanding,
        process: { env: { NEXT_PUBLIC_ARTEX_BACKEND_PROFILE: profile } },
        MOCK: mock,
        auth: { getToken: () => currentToken, setToken: (token) => credentials.push(token) },
        window: { location: { replace: (path) => paths.push(path) } },
        router: { replace: () => assert.fail("unexpected setup navigation") },
        api: {
          authStatus: () => assert.fail("existing token does not call status"),
          login: async (name, password) => {
            assert.equal(name, "ARTEX");
            assert.equal(password, "inert-password");
            return { token: "fresh" };
          },
        },
        agreed: true,
        password: "inert-password",
        setError: () => undefined,
        setLoading: () => undefined,
        setChecking: () => undefined,
      };
      vm.runInNewContext(`(function(){${effect}})();`, context);
      assert.deepEqual(paths, [signInLanding(profile, mock)]);
      assert.deepEqual(credentials, ["existing"]);
      currentToken = null;
      vm.runInNewContext(`${submit}\nglobalThis.submit = handleSubmit;`, context);
      await context.submit({
        preventDefault() {
          return undefined;
        },
      });
      assert.deepEqual(paths, [signInLanding(profile, mock), signInLanding(profile, mock)]);
      assert.deepEqual(credentials, ["existing", "fresh"]);
    }
});

test("root and component use only landing/profile, explicit retry and commit-phase retirement", () => {
  const root = read("app/(external)/page.tsx"),
    component = read("components/host-landing.tsx");
  assert.match(root, /if \(destination !== "\/"\) redirect\(destination\)/);
  assert.match(root, /return <HostLanding \/>/);
  assert.match(component, /useLayoutEffect\(\(\) =>/);
  assert.match(component, /return startHostLanding\(/);
  assert.equal(component.includes("@/lib/api"), false);
  assert.match(component, /readProfile: readLandingProfile/);
  assert.match(component, /role="alert"/);
  assert.match(component, /Retry host check/);
  assert.match(component, /Sign in again/);
  assert.match(
    component,
    /if \(!auth.getToken\(\)\) \{[\s\S]*?auth.clearToken\(\);[\s\S]*?window.location.replace\("\/login"\)/,
  );
  const digest = (path) =>
    createHash("sha256")
      .update(readFileSync(new URL(`../src/${path}`, import.meta.url)))
      .digest("hex");
  // Skills file GET now bypasses cache; the shared authentication transport is unchanged.
  assert.equal(digest("lib/api.ts"), "1dceabd52e1c892e3007fd05b094cc167cc35409d19b071c969c98f1022ff07b");
  assert.equal(digest("lib/auth.ts"), "f7df65baa69bf7945a36394d196a534a4d9d35c396ecc1da204ba1f106283f3d");
});

test("actual landing effect retires before a replacement attempt and only calls the profile read", async () => {
  const source = read("components/host-landing.tsx");
  const effect = source.slice(
    source.indexOf("  useLayoutEffect(() => {") + "  useLayoutEffect(() => {".length,
    source.indexOf("  }, [attempt, router]);"),
  );
  const reads = [],
    errors = [],
    navigations = [],
    hardNavigations = [];
  let cleared = 0;
  const context = {
    startHostLanding,
    setError: (message) => errors.push(message),
    auth: {
      getToken: () => "token",
      clearToken: () => {
        cleared += 1;
      },
    },
    readLandingProfile: (_token, signal) => {
      const next = deferred();
      reads.push({ ...next, signal });
      return next.promise;
    },
    router: { replace: (path) => navigations.push(path) },
    window: { location: { replace: (path) => hardNavigations.push(path) } },
  };
  vm.runInNewContext(`globalThis.effect = function(){${effect}};`, context);
  const retireFirst = context.effect();
  assert.equal(reads.length, 1);
  retireFirst();
  const retireSecond = context.effect();
  assert.equal(reads.length, 2);
  assert.equal(reads[0].signal.aborted, true);
  assert.equal(reads[1].signal.aborted, false);
  reads[0].reject(new ApiError(404, {}, "Retired"));
  reads[1].resolve({ profile: "skills-workspace", writes_enabled: false });
  await settle();
  assert.deepEqual(navigations, [SKILLS_HOME]);
  assert.deepEqual(errors, ["", ""]);
  assert.equal(cleared, 0);
  assert.deepEqual(hardNavigations, []);
  retireSecond();
});

test("actual landing missing-token branch clears stale cookie and requests only login", () => {
  const source = read("components/host-landing.tsx");
  const effect = source.slice(
    source.indexOf("  useLayoutEffect(() => {") + "  useLayoutEffect(() => {".length,
    source.indexOf("  }, [attempt, router]);"),
  );
  let cleared = 0;
  const destinations = [];
  const context = {
    startHostLanding: () => assert.fail("must not begin profile request"),
    setError: () => undefined,
    auth: {
      getToken: () => null,
      clearToken: () => {
        cleared += 1;
      },
    },
    window: { location: { replace: (path) => destinations.push(path) } },
  };
  vm.runInNewContext(`(function(){${effect}})();`, context);
  assert.equal(cleared, 1);
  assert.deepEqual(destinations, ["/login"]);
});

const drain = () => new Promise((resolve) => setImmediate(resolve));
function transportAttempt(t, response, extra = {}) {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, init) => {
    calls.push({ url, init });
    return response;
  });
  const navigations = [],
    errors = [];
  const retire = startHostLanding({
    readProfile: readLandingProfile,
    getToken: () => "original-token",
    navigate: (path) => navigations.push(path),
    onError: (message) => errors.push(message),
    ...extra,
  });
  return { calls, navigations, errors, retire };
}

test("actual landing transport sends only fixed no-store authenticated GET with redirect refusal", async (t) => {
  const run = transportAttempt(t, Response.json({ profile: "skills-workspace", writes_enabled: false }));
  await drain();
  assert.deepEqual(run.navigations, [SKILLS_HOME]);
  assert.equal(run.calls.length, 1);
  const { url, init } = run.calls[0];
  assert.equal(url, "/api/skills-workspace");
  assert.equal(init.method, "GET");
  assert.equal(init.cache, "no-store");
  assert.equal(init.redirect, "error");
  assert.equal(init.body, undefined);
  assert.deepEqual(init.headers, { Accept: "application/json", Authorization: "Bearer original-token" });
  assert.ok(init.signal instanceof AbortSignal);
  run.retire();
});

test("actual JSON 404 supports fallback, malformed and non-JSON 404 cannot choose a destination", async (t) => {
  for (const response of [
    new Response("<html>Not found</html>", { status: 404, headers: { "content-type": "text/html" } }),
    new Response("{truncated", { status: 404, headers: { "content-type": "application/json" } }),
    Response.json({}, { status: 404 }),
    Response.json({ error: "" }, { status: 404 }),
    Response.json({ error: "Missing", unknown: true }, { status: 404 }),
    Response.json({ error: "Missing", redirect: SKILLS_HOME }, { status: 404 }),
  ]) {
    const run = transportAttempt(t, response);
    await drain();
    assert.deepEqual(run.navigations, []);
    assert.equal(run.errors.length, 1);
    run.retire();
    t.mock.restoreAll();
  }
  const run = transportAttempt(t, Response.json({ error: "Not Found" }, { status: 404 }));
  await drain();
  assert.deepEqual(run.navigations, [DEFAULT_HOME]);
  assert.deepEqual(run.errors, []);
  run.retire();
});

test("retired original profile fetch401 cannot clear a replacement credential or navigate login", async (t) => {
  const response = deferred();
  let token = "original-token";
  const navigations = [],
    errors = [],
    hardNavigations = [];
  const originalWindow = globalThis.window,
    originalStorage = globalThis.localStorage,
    originalDocument = globalThis.document;
  globalThis.window = { location: { href: "unchanged", replace: (path) => hardNavigations.push(path) } };
  globalThis.localStorage = {
    getItem: () => token,
    removeItem: () => {
      token = null;
    },
  };
  globalThis.document = { cookie: "new-session-cookie" };
  t.after(() => {
    globalThis.window = originalWindow;
    globalThis.localStorage = originalStorage;
    globalThis.document = originalDocument;
  });
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, init) => {
    calls.push({ url, init });
    return response.promise;
  });
  const retire = startHostLanding({
    readProfile: readLandingProfile,
    getToken: () => token,
    navigate: (path) => navigations.push(path),
    onError: (message) => errors.push(message),
  });
  retire();
  token = "replacement-token";
  response.resolve(Response.json({ error: "Unauthorized" }, { status: 401 }));
  await drain();
  assert.equal(calls[0].init.headers.Authorization, "Bearer original-token");
  assert.equal(calls[0].init.signal.aborted, true);
  assert.equal(token, "replacement-token");
  assert.equal(document.cookie, "new-session-cookie");
  assert.equal(window.location.href, "unchanged");
  assert.deepEqual(hardNavigations, []);
  assert.deepEqual(navigations, []);
  assert.deepEqual(errors, []);
});

test("active actual401 and403 stay explicit read recovery, with no token or navigation side effects", async (t) => {
  for (const status of [401, 403, 503]) {
    const run = transportAttempt(t, Response.json({ error: "Synthetic private server detail" }, { status }));
    await drain();
    assert.deepEqual(run.navigations, []);
    assert.equal(run.errors.length, 1);
    assert.ok(!run.errors[0].includes("Synthetic private"));
    run.retire();
    t.mock.restoreAll();
  }
});

test("actual fetch failure, wrong status and malformed success do not fall back", async (t) => {
  for (const response of [
    Response.json({ profile: "skills-workspace", writes_enabled: true }, { status: 202 }),
    Response.json({ profile: "skills-workspace", writes_enabled: false, imports_enabled: true }),
    new Response("bad-json", { status: 200, headers: { "content-type": "application/json" } }),
  ]) {
    const run = transportAttempt(t, response);
    await drain();
    assert.deepEqual(run.navigations, []);
    assert.equal(run.errors.length, 1);
    run.retire();
    t.mock.restoreAll();
  }
  t.mock.method(globalThis, "fetch", async () => {
    throw new TypeError("Synthetic network failure");
  });
  const run = attempt((signal) => readLandingProfile("original-token", signal));
  await drain();
  assert.deepEqual(run.navigations, []);
  assert.equal(run.errors.length, 1);
  run.retire();
});

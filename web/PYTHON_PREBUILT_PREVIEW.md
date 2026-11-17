# Explicit prebuilt Python frontend preview

This is a **local, non-watching preview profile**, not production hosting, a full
ARTEX replacement, or a repair of Next's development watcher lifecycle. Default
`npm run dev`, `build`, `build:static`, and the existing Python development host
are unchanged. It uses Next's public `dev: false` custom-server mode.

## Why a separate profile

With the pinned Next 16.2.9 development host, a real Skills request followed by
SIGTERM left 41 `FSEventWrap` resources alive after the original `stop()` resolved;
the child did not exit within 20 seconds. Next's routing Watchpack is not exposed
by the public custom-server cleanup API. Changing bundlers is not a verified fix.
The existing `python-dev-server.mjs` remains affected. This profile does **not**
sweep global handles, monkeypatch Next, unref unknown resources, terminate a
worker, or call `process.exit()` to disguise that failure.

## Build and run

Use trusted checked-out source and dependencies. Keep `.next` and source unchanged
while serving; do not build/export into an active preview's directory. From `web`:

```sh
ARTEX_PYTHON_PREVIEW=1 AUTOPENTEST_API=http://127.0.0.1:8787 npm run build
node dev/python-preview-server.mjs --frontend-port 3000 --backend-port 8787
```

The build opt-in fixes the public Python profile, non-mock mode (`"0"`), and empty
same-origin SSE base. It rejects mock or static-export builds and requires an
explicit canonical unprivileged IPv4 loopback backend. The backend port is baked
into Next's rewrite manifest. **Changing `AUTOPENTEST_API` only at launch cannot
repoint a production build. Rebuild for a different backend port.**

Start the independently authorized Python listener separately. For the limited
Skills host, see `python/SKILLS_WORKSPACE_HOST.md` and explicitly trust this
frontend origin with `--frontend-origin http://127.0.0.1:3000`. Use an existing
private root and outside-root password file; add `--enable-writes` only when those
filesystem writes are intended. Nothing here starts a database, provider, task
runtime, tool, or Python process. The Skills host still does not provide general
ARTEX endpoints or task/SSE functionality.

The launcher refuses a missing/malformed build, static-export or standalone
output, wrong client profile/mock/SSE flags, alternate API rewrite or wrong
backend port **before importing Next or opening its listener**. These are
stale/misprofile build guards tied to the locked Next artifact format, not signed
artifact verification or protection against a malicious writable source/build.
An upstream artifact format change must be reviewed, not silently accepted.

## Transport and lifetime

The profile reuses the explicit exact-Host/Origin/framing checks, fixed IPv4
loopback bind, bounded HTTP server, and three-route SSE bridge. It is not an
arbitrary URL proxy and grants no new backend execution authority. Authentication
is still the backend's responsibility. Missing-Origin requests and whole-origin
trust keep their existing meaning; no full CSRF/TLS/production-security claim.

SIGINT/SIGTERM calls the existing shared shutdown completion, closes the owned
bridge/server/connections and awaits public Next `close()`. There is no timeout
that declares success and no forced exit. A reported cleanup failure sets a
nonzero exit status but does not kill remaining resources. A resolved Next close
alone does not prove every internal callback succeeded; live acceptance must also
observe the child exiting naturally. Backend shutdown/lease drain remains a
separate explicitly observed event, not a promise inferred from frontend exit.

## Evidence boundaries

Pure tests exercise build options, exact serialized manifests, stale/malformed
artifact refusal before Next import, and unchanged default/static/mock config.
Real acceptance is separately recorded against an exact source tree: a fresh
opt-in build, actual child preview and Python Skills CLI, bcrypt/JWT, pure UI
model/receipt helpers over HTTP, Unicode mkdir/create/upsert/read/HEAD, denial
cases, page/asset delivery, natural SIGTERM exits, and only then backend lease
reacquisition. This is **not** a mounted React/browser interaction test, not direct
execution of the `api.ts` fetch wrapper, and not a development-watcher pass.
A standard Chromium launch was blocked by the environment's socket restriction;
no sandbox/network workaround is part of this profile.

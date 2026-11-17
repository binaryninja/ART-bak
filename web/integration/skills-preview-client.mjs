// Explicit synthetic smoke client. No browser rendering, credentials in argv,
// retries of mutations, API replacement, provider calls or task endpoints.

import { createSkillWorkspace, skillFileKey, skillResourcePath } from "../src/lib/skill-workspace.mjs";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { setTimeout } from "node:timers/promises";
import "./load-skills-api.mjs";

const [port, mode, passwordPath] = process.argv.slice(2);
assert.match(port, /^[1-9][0-9]{3,4}$/u);
assert.ok(Number(port) >= 1024 && Number(port) <= 65535);
assert.ok(["read-only", "write-enabled"].includes(mode));
const writable = mode === "write-enabled";
const origin = `http://127.0.0.1:${port}`;
const nativeFetch = globalThis.fetch;
const password = (await readFile(passwordPath, "utf8")).trimEnd();
async function request(path, init = {}) {
  assert.ok(path.startsWith("/") && !path.startsWith("//"));
  const headers = new Headers(init.headers);
  headers.set("Origin", origin);
  headers.set("Connection", "close");
  return nativeFetch(`${origin}${path}`, {
    ...init,
    redirect: "manual",
    signal: init.signal ?? AbortSignal.timeout(10000),
    headers,
  });
}
assert.equal((await request("/api/skills")).status, 401);
const login = await request("/api/auth/login", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ username: "ARTEX", password }),
});
assert.equal(login.status, 200);
const { token } = await login.json();
assert.equal(typeof token, "string");
assert.ok(token.length > 0);
process.env.NEXT_PUBLIC_MOCK = "0";
process.env.NEXT_PUBLIC_ARTEX_BACKEND_PROFILE = "python";
const storage = new Map([["artex_token", token]]);
globalThis.localStorage = { getItem: (key) => storage.get(key) ?? null, removeItem: (key) => storage.delete(key) };
globalThis.window = { location: { protocol: "http:", hostname: "127.0.0.1", href: origin } };
globalThis.document = { cookie: "" };
const calls = [];
globalThis.fetch = (path, init = {}) => {
  assert.ok(typeof path === "string" && path.startsWith("/api/"));
  calls.push({ path, method: init.method ?? "GET" });
  return request(path, init);
};
const { api } = await import("../src/lib/api.ts");
const { MOCK } = await import("../src/lib/mock/enabled.ts");
assert.equal(MOCK, false);
const model = createSkillWorkspace(api, { requireProfile: true });
const input = { name: "資料", description: "Synthetic 世界", instructions: "# Inert instructions\n" };
assert.equal(await model.createSkill(input), null);
assert.equal(calls.length, 0, "unchecked mode must not dispatch a mutation");
const retire = model.readProfile();
for (let n = 0; n < 500 && model.getSnapshot().profile.status === "loading"; n++) await setTimeout(20);
assert.equal(model.getSnapshot().profile.status, "ready");
assert.equal(model.getSnapshot().writeModeEnabled, writable);
assert.equal(model.getSnapshot().deleteModeEnabled, false);
assert.equal(model.getSnapshot().importModeEnabled, false);
assert.deepEqual(await api.skillWorkspaceProfile(), { profile: "skills-workspace", writes_enabled: writable });
assert.deepEqual(
  (await api.skills()).map((s) => s.name),
  ["sample"],
);
const original = await api.readSkillFile("sample", "SKILL.md");
assert.ok(original.includes("Synthetic preview fixture"));
const mutations = () => calls.filter((call) => !["GET", "HEAD"].includes(call.method)).length;
if (!writable) {
  const before = mutations();
  assert.equal(await model.createSkill(input), null);
  assert.equal(await model.create("sample", "blocked.txt", "file"), false);
  assert.equal(mutations(), before);
  await assert.rejects(api.createSkill(input), (error) => error.status === 403);
  await assert.rejects(api.writeSkillFile("sample", "blocked.txt", "no effect"), (error) => error.status === 403);
  assert.equal(await api.readSkillFile("sample", "SKILL.md"), original);
} else {
  assert.deepEqual(await model.createSkill(input), { name: "資料" });
  assert.equal(await model.create("資料", "参考", "dir"), true);
  assert.equal(await model.create("資料", "参考/例.txt", "file"), true);
  model.edit("資料", "参考/例.txt", "Edited 世界\n");
  assert.equal(await model.save("資料", "参考/例.txt"), true);
  assert.equal(model.getSnapshot().drafts.get(skillFileKey("資料", "参考/例.txt")).dirty, false);
  assert.equal(await api.readSkillFile("資料", "参考/例.txt"), "Edited 世界\n");
  await assert.rejects(api.createSkill(input), (error) => error.status === 409);
  const row = (await api.skills()).find((skill) => skill.name === "資料");
  assert.ok(row.files.includes("参考/例.txt"));
  assert.equal(row.description, input.description);
  const headers = { Authorization: `Bearer ${token}` };
  const resource = `/api${skillResourcePath("資料", "参考/例.txt")}`;
  const get = await request(resource, { headers });
  const body = await get.arrayBuffer();
  const head = await request(resource, { method: "HEAD", headers });
  assert.equal(head.status, 200);
  assert.equal((await head.arrayBuffer()).byteLength, 0);
  assert.equal(Number(head.headers.get("Content-Length")), body.byteLength);
}
// Next middleware/page/asset delivery is checked, but no React effects run here.
const headers = { Cookie: `artex_token=${token}` };
const page = await request("/system/skills", { headers });
assert.equal(page.status, 200);
const html = await page.text();
const asset = html.match(/<script[^>]+src="([^" ]+)"/u)?.[1];
assert.ok(asset?.startsWith("/_next/"));
const script = await request(asset, { headers });
assert.equal(script.status, 200);
await script.arrayBuffer();
retire();
console.log(`PASS ${mode}: original API/model over Next-to-Python; page/asset delivery, not a browser.`);

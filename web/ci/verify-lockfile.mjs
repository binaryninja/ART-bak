// Compatibility/provenance checks, not an untrusted-PR sandbox or package audit.
import { existsSync, readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

function record(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function fail() {
  throw new Error("Official registry lockfile with SHA-512 integrity required");
}
export function verifyLockfile(manifest, lock) {
  if (!record(manifest) || !record(lock) || lock.lockfileVersion !== 3 || !record(lock.packages)) fail();
  const root = lock.packages[""];
  if (!record(root) || root.name !== manifest.name || root.version !== manifest.version) fail();
  if (manifest.workspaces !== undefined || manifest.overrides !== undefined || root.workspaces !== undefined) fail();
  for (const field of ["dependencies", "devDependencies", "optionalDependencies"]) {
    const expected = manifest[field] ?? {},
      actual = root[field] ?? {};
    if (!record(expected) || !record(actual)) fail();
    const keys = Object.keys(expected).sort();
    if (keys.join("\0") !== Object.keys(actual).sort().join("\0")) fail();
    for (const key of keys) {
      const version = expected[key];
      // Deliberately restricted to the existing simple semver-range contract.
      // New aliases, URLs, file/git/workspace dependencies need separate review.
      if (
        typeof version !== "string" ||
        !/^[~^]?[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$/.test(version) ||
        actual[key] !== version
      )
        fail();
    }
  }
  let count = 0;
  for (const [name, value] of Object.entries(lock.packages)) {
    if (name === "") continue;
    if (
      !name.startsWith("node_modules/") ||
      name.includes("\\") ||
      name.split("/").some((part) => part === "." || part === ".." || part === "") ||
      !record(value) ||
      value.link !== undefined
    )
      fail();
    if (typeof value.version !== "string" || typeof value.resolved !== "string" || typeof value.integrity !== "string")
      fail();
    let url;
    try {
      url = new URL(value.resolved);
    } catch {
      fail();
    }
    if (
      url.href !== value.resolved ||
      url.protocol !== "https:" ||
      url.hostname !== "registry.npmjs.org" ||
      url.port ||
      url.username ||
      url.password ||
      url.search ||
      url.hash ||
      !url.pathname.endsWith(".tgz")
    )
      fail();
    if (
      !/^sha512-[A-Za-z0-9+/]{86}==$/.test(value.integrity) ||
      Buffer.from(value.integrity.slice(7), "base64").toString("base64") !== value.integrity.slice(7)
    )
      fail();
    count += 1;
  }
  if (!count) fail();
  return count;
}

export function checkRepository() {
  const root = new URL("../", import.meta.url);
  // These would override the reviewed npm lock/config path. CI starts clean.
  for (const name of [".npmrc", "../.npmrc", "npm-shrinkwrap.json", "node_modules"]) {
    if (existsSync(new URL(name, root))) throw new Error("Clean reviewed npm project required");
  }
  return verifyLockfile(
    JSON.parse(readFileSync(new URL("package.json", root), "utf8")),
    JSON.parse(readFileSync(new URL("package-lock.json", root), "utf8")),
  );
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    console.log(`Verified ${checkRepository()} official integrity-locked dependency entries.`);
  } catch {
    console.error("Frontend dependency preflight refused; inspect the reviewed lockfile and project configuration.");
    process.exitCode = 1;
  }
}

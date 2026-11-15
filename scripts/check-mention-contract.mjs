// Offline source-contract check: reads text and tests regex data only.
// Does not import or execute the application, Go packages, or database code.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const root = new URL("../", import.meta.url);
const backend = readFileSync(new URL("server/chat_mentions.go", root), "utf8");
const frontend = readFileSync(new URL("web/src/lib/chat-mentions.ts", root), "utf8");
const backendPattern = backend.match(/var chatMentionPattern = regexp\.MustCompile\(`([^`]+)`\)/)?.[1];
const frontendPattern = frontend.match(/value\.matchAll\(\/(.+)\/g\)/)?.[1];
assert(backendPattern, "backend mention regex not found");
assert(frontendPattern, "frontend mention regex not found");
const serverRegex = new RegExp(backendPattern, "g");
const clientRegex = new RegExp(frontendPattern, "g");
const mapText = backend.match(/var chatMentionKinds = map\[string\]string\{([\s\S]*?)\n\}/)?.[1];
assert(mapText, "backend mention kind map not found");
const backendKinds = new Map([...mapText.matchAll(/"([^"\\]*(?:\\.[^"\\]*)*)":\s*"([^"\\]+)"/g)]
  .map((match) => [JSON.parse(`"${match[1]}"`), match[2]]));
const entries = [...frontend.matchAll(/\{ kind: "([^"]+)", label: "([^"]+)", legacyLabel: "([^"\\]*(?:\\.[^"\\]*)*)", alias: "([^"]+)" \}/g)]
  .map((match) => ({ kind: match[1], label: match[2], legacy: JSON.parse(`"${match[3]}"`) }));
assert.equal(entries.length, 9, "expected nine stable mention kinds");
assert.match(frontend, /const kind = mentionKinds\.find\(\(entry\) => entry\.kind === item\.kind\)\?\.label \?\? "Asset";/,
  "new tokens must select the English display label");
let cases = 0;
for (const entry of entries) {
  for (const label of new Set([entry.label, entry.legacy, ...(entry.kind === "app" ? ["App"] : [])])) {
    const token = `@[${label}#42 Example]`;
    const server = [...token.matchAll(serverRegex)];
    const client = [...token.matchAll(clientRegex)];
    assert.equal(server.length, 1, `server rejected ${label}`);
    assert.equal(client.length, 1, `client rejected ${label}`);
    assert.equal(server[0][1], label);
    assert.equal(client[0][1], label);
    assert.equal(server[0][2], "42");
    assert.equal(client[0][2], "42");
    assert.equal(backendKinds.get(label), entry.kind, `wrong kind mapping for ${label}`);
    cases++;
  }
}
for (const token of ["@[Unknown#42]", "@[Finding#-1]", "@[Asset#abc]", "ordinary@example.com"]) {
  assert.equal([...token.matchAll(serverRegex)].length, 0);
  assert.equal([...token.matchAll(clientRegex)].length, 0);
  cases++;
}
assert.match(backend, /strconv\.ParseInt\(m\[2\], 10, 64\)/);
assert.match(backend, /err != nil \|\| id <= 0/);
assert.match(backend, /key := kind \+ ":" \+ strconv\.FormatInt\(id, 10\)/);
assert.match(backend, /len\(refs\) > maxChatMentions/);
assert.match(backend, /const maxChatMentions = 10/);
console.log(`${cases} source-contract cases passed. Numeric-ID guards and canonical deduplication remain present.`);
console.log("Static/offline check only; not a Go, frontend runtime, or database integration test.");

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { stripTypeScriptTypes } from "node:module";
import test from "node:test";
import vm from "node:vm";

// Execute the real pure display helper, not a copied implementation or React mount.
const source = await readFile(new URL("../src/lib/utils.ts", import.meta.url), "utf8");
const start = source.indexOf("const initialSegmenter =");
const end = source.indexOf("export function formatCurrency", start);
assert(start >= 0 && end > start);
const helper = source.slice(start, end).replace("export const getInitials", "const getInitials");
function load(segmenter = Intl.Segmenter) {
  const context = vm.createContext({ Intl: { Segmenter: segmenter } });
  vm.runInContext(stripTypeScriptTypes(`${helper}\nglobalThis.initials = getInitials;`), context);
  return context.initials;
}
const initials = load();

test("existing ASCII, spacing, capitalization and one-initial-per-word behavior remains", () => {
  for (const [name, expected] of [
    ["Ada Lovelace", "AL"],
    ["  grace\tbrewster\nMurray Hopper  ", "GBMH"],
    ["alice", "A"],
    ["Jean-Luc Picard", "JP"],
    ["123 Example", "1E"],
  ])
    assert.equal(initials(name), expected);
});
test("empty and non-string values retain the existing question-mark fallback", () => {
  for (const value of ["", " \t\n", null, undefined, 12, {}, ["Ada"], false]) assert.equal(initials(value), "?");
});
test("supplementary Unicode letters are never truncated into a lone surrogate", () => {
  assert.equal(initials("𐐨da Lovelace"), "𐐀L");
  assert.equal(initials("𠮷田 太郎"), "𠮷太");
  assert.equal(initials("😀 User"), "😀U");
});
test("decomposed accents and combining marks remain attached to their initial", () => {
  assert.equal(initials("e\u0301mile Zola"), "E\u0301Z");
  assert.equal(initials("Émile Zola"), "ÉZ");
  assert.equal(initials("a\u0308\u0301 Name"), "A\u0308\u0301N");
  assert.equal(initials("किरण कुमार"), "किकु");
});
test("emoji modifiers, joined sequences, flags and keycaps remain whole graphemes", () => {
  assert.equal(initials("👩🏽‍🚀 Ada"), "👩🏽‍🚀A");
  assert.equal(initials("🇨🇦 Example"), "🇨🇦E");
  assert.equal(initials("1️⃣ Example"), "1️⃣E");
});
test("existing Unicode uppercase expansions and whitespace word boundaries are preserved", () => {
  assert.equal(initials("ßeta Smith"), "SSS");
  assert.equal(initials("alice\u00a0bob\u3000carol"), "ABC");
  assert.equal(initials("élise 王"), "É王");
});
test("older engines without Segmenter use code-point-safe fallback without throwing", () => {
  const legacy = load(null);
  assert.equal(legacy("𐐨da Lovelace"), "𐐀L");
  assert.equal(legacy("😀 User"), "😀U");
  assert.equal(legacy("Ada Lovelace"), "AL");
  assert.equal(legacy(""), "?");
  // Fallback promises code-point safety, not full combining/ZWJ grapheme support.
  assert.equal(legacy("e\u0301mile Zola"), "EZ");
});
test("both sidebar avatar surfaces still consume the same display helper", async () => {
  for (const file of ["nav-user.tsx", "account-switcher.tsx"]) {
    const content = await readFile(new URL(`../src/app/(main)/_components/sidebar/${file}`, import.meta.url), "utf8");
    assert.match(content, /import \{[^}]*\bgetInitials\b[^}]*\} from "@\/lib\/utils"/);
    assert.equal((content.match(/getInitials\(/g) ?? []).length, 2);
  }
});

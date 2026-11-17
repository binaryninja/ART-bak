import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";

const page = readFileSync(new URL("../src/app/(main)/system/skills/page.tsx", import.meta.url), "utf8");
const doc = readFileSync(new URL("../SKILL_IMPORT_WORKSPACE.md", import.meta.url), "utf8");
const oldCaption =
  "Not supported: streamed/data-descriptor ZIPs, extra or comment fields, preamble/trailer data, legacy non-UTF-8-marked names or MCP metadata. Partial effects cannot be assumed rolled back if an import outcome is unknown.";
const caption =
  "Standard Info-ZIP archives within this subset may include matching timestamp and UID/GID fields; these are not restored. Not supported: other extra fields, streamed/data-descriptor ZIPs, ZIP64, encryption, comments, preamble/trailer data, non-ASCII names without a UTF-8 flag or MCP metadata. Partial effects cannot be assumed rolled back if an import outcome is unknown.";

test("Info-ZIP compatibility changes one literal only, not any Skills behavior", () => {
  assert.equal(page.split(caption).length - 1, 1);
  assert.equal(page.includes(oldCaption), false);
  const restored = page.replace(caption, oldCaption);
  assert.equal(
    createHash("sha256").update(restored).digest("hex"),
    "06ec2725b979d5048451ed6f68ff2aeb9f1bd60f304db8def5276a5675683b25",
  );
});

test("copy limits Info-ZIP support to validated metadata rather than general ZIP compatibility", () => {
  for (const phrase of [
    "within this subset",
    "matching timestamp and UID/GID",
    "not restored",
    "other extra fields",
    "streamed/data-descriptor",
    "ZIP64",
    "encryption",
    "comments",
    "non-ASCII names without a UTF-8 flag",
    "MCP metadata",
    "Partial effects cannot be assumed rolled back",
  ])
    assert.ok(caption.includes(phrase), phrase);
  for (const phrase of [
    "0x5455",
    "0x7875",
    "version-1",
    "32 total extra bytes per header",
    "1–4 bytes",
    "paired between local and central",
    "common values must match",
    "Unicode\nfilename override fields are not supported",
    "not support for all\nInfo-ZIP archives",
    "ownership and modes are not taken from the archive",
  ])
    assert.ok(doc.includes(phrase), phrase);
});

test("existing import limits, separate mode and create-only uncertainty copy remain", () => {
  for (const phrase of [
    "Archive at most 1 MiB",
    "each file at most 256 KiB",
    "at most 128 entries",
    "Existing skills are never overwritten",
    "own confirmed host option",
    "Fixture-only acknowledgement",
  ])
    assert.ok(page.includes(phrase), phrase);
  for (const phrase of [
    "not a claim of all-or-nothing",
    "partial effects under quarantine",
    "No automatic retry",
    "does not inspect or install an archive",
    "not browser guarantees",
  ])
    assert.ok(doc.replace(/\s+/g, " ").includes(phrase), phrase);
});

import assert from "node:assert/strict";
import test from "node:test";
import { assetSummaryDisplay, retestErrorDisplay } from "./legacy-display-text.ts";

test("exact legacy provenance and retest messages display in English", () => {
  assert.equal(assetSummaryDisplay("\u4efb\u52a1\u6267\u884c\u671f\u95f4\u81ea\u52a8\u5173\u8054"), "Automatically associated during task execution");
  assert.equal(assetSummaryDisplay("\u7531\u5386\u53f2\u4efb\u52a1\u8d44\u4ea7\u5173\u8054\u8fc1\u79fb"), "Migrated from a historical task-asset association");
  assert.equal(retestErrorDisplay("\u590d\u6d4b\u4f1a\u8bdd\u5df2\u5220\u9664"), "Retest conversation has been deleted");
});

test("unknown/custom text and wrong-context messages are untouched", () => {
  const legacy = "\u590d\u6d4b\u4f1a\u8bdd\u5df2\u5220\u9664";
  for (const text of ["", "custom evidence", "constructor", "__proto__", ` ${legacy}`, `${legacy} detail`]) {
    assert.equal(assetSummaryDisplay(text), text);
    assert.equal(retestErrorDisplay(text), text);
  }
  assert.equal(assetSummaryDisplay(legacy), legacy);
  const asset = "\u4efb\u52a1\u6267\u884c\u671f\u95f4\u81ea\u52a8\u5173\u8054";
  assert.equal(retestErrorDisplay(asset), asset);
  const row = Object.freeze({ source_summary: asset, error: legacy });
  assetSummaryDisplay(row.source_summary); retestErrorDisplay(row.error);
  assert.equal(row.source_summary, asset); assert.equal(row.error, legacy);
});

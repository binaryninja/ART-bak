import assert from "node:assert/strict";
import test from "node:test";
import { taskDetailHref } from "../src/lib/task-detail-route.mjs";

test("only the explicit recon runtime selects the campaign report", () => {
  assert.equal(taskDetailHref({ id: "4", runtime_kind: "servicenow-recon" }), "/function/recon?id=4");
  assert.equal(taskDetailHref({ id: "4" }), "/function/tasks/detail?id=4");
  assert.equal(taskDetailHref({ id: "4", runtime_kind: "unknown" }), "/function/tasks/detail?id=4");
  assert.equal(taskDetailHref({ id: "4&other=5", runtime_kind: "servicenow-recon" }), "/function/recon?id=4%26other%3D5");
});

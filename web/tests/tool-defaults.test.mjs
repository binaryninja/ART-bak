import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { stripTypeScriptTypes } from "node:module";
import test from "node:test";
import vm from "node:vm";

// Actual source helpers and save closure, not a mounted React/browser test.
const source = await readFile(new URL("../src/app/(main)/system/tools/page.tsx", import.meta.url), "utf8");
function section(start, end, offset = 0) {
  const a = source.indexOf(start, offset),
    b = source.indexOf(end, a + start.length);
  assert(a >= 0 && b > a, start);
  return source.slice(a, b);
}
const helpers = section("function toRows(", "// ToolEditor");
const ctx = vm.createContext({ structuredClone });
vm.runInContext(stripTypeScriptTypes(`${helpers}\nglobalThis.helpers={toRows,coerceDefault,applyRows};`), ctx);
const { toRows, coerceDefault, applyRows } = ctx.helpers;
const plain = (value) => JSON.parse(JSON.stringify(value));
const schema = (type) => ({
  type: "object",
  properties: { sample: { type, description: "old", default: type === "boolean" ? true : 10 } },
  required: ["sample"],
  additionalProperties: false,
});
const row = (type, raw, more = {}) => ({
  name: "sample",
  type,
  description: "updated",
  required: true,
  scalar: true,
  defaultStr: raw,
  ...more,
});

test("blank input still removes a default intentionally while nonblank strings remain literal", () => {
  for (const type of ["string", "number", "integer", "boolean"]) assert.equal(coerceDefault(type, "  "), undefined);
  for (const raw of ["false", "0", "  padded string  ", "\\n"]) assert.equal(coerceDefault("string", raw), raw);
});
test("finite numbers retain their type and accepted decimal/scientific/signed syntax", () => {
  for (const [raw, value] of [
    ["0", 0],
    ["-2.5", -2.5],
    [".125", 0.125],
    ["1e3", 1000],
    ["+12", 12],
    ["1e100", 1e100],
    ["5e-324", 5e-324],
    ["0e9999", 0],
  ])
    assert.equal(coerceDefault("number", raw), value);
});
test("booleans accept true/false case-insensitively but never turn a typo into false", () => {
  for (const raw of ["true", "TRUE", " True "]) assert.equal(coerceDefault("boolean", raw), true);
  for (const raw of ["false", "FALSE", " False "]) assert.equal(coerceDefault("boolean", raw), false);
  for (const raw of ["yes", "no", "1", "0", "truthy", "falsee"])
    assert.throws(() => coerceDefault("boolean", raw, "enabled"), /enabled: boolean default must be true or false/);
});
test("invalid numbers and nonfinite/underflow values reject instead of deleting or serializing null", () => {
  for (const type of ["number", "integer"])
    for (const raw of ["not-a-number", "NaN", "Infinity", "-Infinity", "1e999", "1e-999", "1_000"])
      assert.throws(() => coerceDefault(type, raw, "timeout"));
});
test("integer defaults accept exact values including exponent notation and range boundaries", () => {
  for (const [raw, value] of [
    ["0", 0],
    ["12", 12],
    ["-12", -12],
    ["12.000", 12],
    ["1.2e1", 12],
    ["1e3", 1000],
    ["00012", 12],
    ["9007199254740991", Number.MAX_SAFE_INTEGER],
    ["-9007199254740991", Number.MIN_SAFE_INTEGER],
  ])
    assert.equal(coerceDefault("integer", raw), value);
});
test("fractional and rounded integer text cannot silently become a different integer", () => {
  for (const raw of [
    "1.5",
    "1.0000000000000001",
    "9007199254740993",
    "9007199254740992",
    "-9007199254740992",
    ".1",
    "1e-1",
    "1e-999",
    "999999999999999999999999999999999999",
  ])
    assert.throws(() => coerceDefault("integer", raw));
});
test("zero exponents and trailing fractional zeroes are not mistaken for underflow", () => {
  for (const type of ["integer", "number"])
    for (const raw of ["0e-99999", "0.000e9999", "00.000", "0.0e-1000"]) assert.equal(coerceDefault(type, raw), 0);
  for (const raw of ["1.00", "100e-2", "12.30e1"]) assert.equal(coerceDefault("integer", raw), Number(raw));
});
test("valid schema updates preserve structure and unrelated annotations and scalar JSON types", () => {
  const input = {
    type: "object",
    properties: {
      count: { type: "integer", minimum: 0, default: 4 },
      flag: { type: "boolean", default: false },
      text: { type: "string", default: "old" },
      list: { type: "array", items: { type: "string" }, default: ["keep"] },
    },
    required: ["count"],
    additionalProperties: false,
  };
  const original = structuredClone(input),
    rows = toRows(input);
  for (const r of rows) {
    if (r.name === "count") r.defaultStr = "12";
    if (r.name === "flag") r.defaultStr = "TRUE";
    if (r.name === "text") r.defaultStr = " new ";
  }
  const output = plain(applyRows(input, rows));
  assert.deepEqual(input, original);
  assert.equal(output.properties.count.default, 12);
  assert.equal(output.properties.flag.default, true);
  assert.equal(output.properties.text.default, " new ");
  assert.deepEqual(output.properties.list.default, ["keep"]);
  assert.equal(output.properties.count.minimum, 0);
  assert.deepEqual(output.required, ["count"]);
  assert.equal(output.additionalProperties, false);
});
test("nested array-item defaults validate with field identity and never mutate the source on failure", () => {
  const input = {
    type: "object",
    properties: {
      items: { type: "array", items: { type: "object", properties: { sample: { type: "integer", default: 3 } } } },
    },
  };
  const original = structuredClone(input),
    rows = toRows(input);
  rows.find((r) => r.parentKey === "items").defaultStr = "1.5";
  assert.throws(() => applyRows(input, rows), /items.sample: enter an exact integer/);
  assert.deepEqual(input, original);
  rows.find((r) => r.parentKey === "items").defaultStr = "6";
  assert.equal(applyRows(input, rows).properties.items.items.properties.sample.default, 6);
});
test("invalid later row cannot partially mutate the original schema", () => {
  const input = {
    type: "object",
    properties: { first: { type: "string", default: "old" }, sample: { type: "number", default: 10 } },
  };
  const original = structuredClone(input);
  assert.throws(() => applyRows(input, [row("string", "new", { name: "first" }), row("number", "oops")]));
  assert.deepEqual(input, original);
});
function saveHarness(type, raw) {
  const calls = [],
    events = [],
    tool = { key: "synthetic_tool", schema: schema(type) };
  const context = vm.createContext({
    structuredClone,
    tool,
    description: "synthetic",
    rows: [row(type, raw)],
    bound: ["planner"],
    enabled: true,
    setSaving: (v) => events.push(["saving", v]),
    toast: { success: (v) => events.push(["success", v]), error: (v) => events.push(["error", v]) },
    onSaved: () => events.push(["saved"]),
    onClose: () => events.push(["closed"]),
    api: {
      saveTool: async (key, payload) => {
        calls.push({ key, payload: plain(payload) });
        return { ok: true };
      },
    },
  });
  const save = section("  async function save() {", "  async function reset()", source.indexOf("// ToolEditor"));
  vm.runInContext(stripTypeScriptTypes(`${helpers}\n${save}\nglobalThis.save=save;`), context);
  return { save: context.save, calls, events, tool };
}
test("actual save handler stops invalid metadata before API dispatch and retains editor/draft", async () => {
  for (const [type, raw] of [
    ["number", "PRIVATE_INVALID_DEFAULT"],
    ["number", "1e999"],
    ["integer", "1.5"],
    ["boolean", "typo"],
  ]) {
    const h = saveHarness(type, raw),
      original = structuredClone(h.tool.schema);
    await h.save();
    assert.deepEqual(h.calls, []);
    assert.deepEqual(h.tool.schema, original);
    assert(h.events.some((e) => e[0] === "error" && e[1].includes("sample:")));
    assert(!h.events.some((e) => ["saved", "closed", "success"].includes(e[0])));
    assert(!JSON.stringify(h.events).includes("PRIVATE_INVALID_DEFAULT"));
    assert.deepEqual(h.events.at(-1), ["saving", false]);
  }
});
test("actual save handler submits one valid typed metadata payload and keeps existing success lifecycle", async () => {
  for (const [type, raw, value] of [
    ["integer", "1.2e1", 12],
    ["number", "-2.5", -2.5],
    ["boolean", "TRUE", true],
    ["string", " literal ", " literal "],
  ]) {
    const h = saveHarness(type, raw);
    await h.save();
    assert.equal(h.calls.length, 1);
    assert.equal(h.calls[0].key, "synthetic_tool");
    assert.equal(h.calls[0].payload.schema.properties.sample.default, value);
    assert.deepEqual(h.calls[0].payload.agents, ["planner"]);
    assert.equal(h.calls[0].payload.enabled, true);
    assert(h.events.some((e) => e[0] === "saved"));
    assert(h.events.some((e) => e[0] === "closed"));
  }
});
test("intentional clear reaches the save API without default but never null", async () => {
  const h = saveHarness("number", "");
  await h.save();
  assert.equal(h.calls.length, 1);
  assert.equal(Object.hasOwn(h.calls[0].payload.schema.properties.sample, "default"), false);
});

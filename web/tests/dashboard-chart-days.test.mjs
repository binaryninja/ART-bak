import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import { stripTypeScriptTypes } from "node:module";
import test from "node:test";
import vm from "node:vm";

// Actual display helpers and memo callbacks, not a copied aggregator or React mount.
const source = await readFile(new URL("../src/app/(main)/dashboard/page.tsx", import.meta.url), "utf8");
function section(start, end) {
  const a = source.indexOf(start),
    b = source.indexOf(end, a + start.length);
  assert(a >= 0 && b > a);
  return source.slice(a, b);
}
const helpers = stripTypeScriptTypes(section("function tokenChartRange(", "function fmtTokens("));
const callbacks = stripTypeScriptTypes(section("  const dailyTokenDataOld =", "  const dailyTokenData ="));
const fixed = Date.parse("2026-11-05T00:30:00Z");
const plain = (value) => JSON.parse(JSON.stringify(value));
function run(input = {}, now = fixed) {
  class Clock extends Date {
    constructor(...args) {
      super(...(args.length ? args : [now]));
    }
  }
  const context = vm.createContext({
    Date: Clock,
    React: { useMemo: (fn) => fn() },
    tasks: [],
    convTokens: [],
    usageStats: { daily: [] },
    tokenDays: 7,
    tokenTab: "all",
    defaultProfileId: 1,
    profileIdByName: new Map([
      ["one", 1],
      ["two", 2],
    ]),
    ...input,
  });
  vm.runInContext(
    `${helpers}\n${callbacks}\nglobalThis.result={old:dailyTokenDataOld,new:dailyTokenDataNew};globalThis.range=tokenChartRange;globalThis.day=tokenChartDay`,
    context,
  );
  return { result: plain(context.result), range: (days) => plain(context.range(days)), day: context.day };
}
const day = (date, offset = 0) =>
  new Date(Date.parse(`${date}T00:00:00Z`) + offset * 86400000).toISOString().slice(0, 10);
const usage = (date, profile = "one", n = 1) => ({
  date,
  profile_name: profile,
  input_tokens: n,
  output_tokens: n * 2,
  cache_read_tokens: n * 3,
});
const task = (date, profile = 1, n = 1) => ({
  created_at: `${date}T12:00:00Z`,
  llm_profile_id: profile,
  tokens: { input_tokens: n, output_tokens: n * 2, cache_read_tokens: n * 3 },
});

test("every selectable range includes exactly N UTC calendar days through today", () => {
  for (const n of [7, 30, 90, 180, 365]) {
    const h = run({ tokenDays: n }),
      range = h.range(n),
      first = day("2026-11-05", -(n - 1));
    assert.deepEqual(range, { start: first, end: "2026-11-05" });
    const dates = Array.from({ length: n + 2 }, (_, i) => day(first, i - 1));
    const result = run({
      tokenDays: n,
      tasks: dates.map((d) => task(d)),
      usageStats: { daily: dates.map((d) => usage(d)) },
    }).result;
    for (const rows of [result.old, result.new]) {
      assert.equal(rows.length, n);
      assert.equal(rows[0].date, first.slice(5));
      assert.equal(rows.at(-1).date, "11-05");
      assert.equal(
        rows.reduce((sum, r) => sum + r.input, 0),
        n,
      );
    }
  }
});
test("year and leap-day boundaries remain inclusive with no extra oldest date", () => {
  assert.deepEqual(run({}, Date.parse("2026-01-03T23:59:59Z")).range(7), { start: "2025-12-28", end: "2026-01-03" });
  assert.deepEqual(run({}, Date.parse("2024-03-01T00:00:01Z")).range(7), { start: "2024-02-24", end: "2024-03-01" });
});
test("UTC range does not shift across client timezones or either DST transition", () => {
  const script = `${helpers}\nconsole.log(JSON.stringify(["2026-11-05T00:30:00Z","2026-03-10T23:30:00Z"].map(ts=>tokenChartRange(7,new Date(ts)))))`;
  for (const TZ of ["UTC", "America/Los_Angeles", "Pacific/Kiritimati"])
    assert.deepEqual(
      JSON.parse(execFileSync(process.execPath, ["-e", script], { env: { ...process.env, TZ }, encoding: "utf8" })),
      [
        { start: "2026-10-30", end: "2026-11-05" },
        { start: "2026-03-04", end: "2026-03-10" },
      ],
    );
});
test("legacy explicit-offset timestamps are assigned to their UTC day", () => {
  const h = run();
  assert.equal(h.day("2026-11-04T23:30:00-05:00"), "2026-11-05");
  assert.equal(h.day("2026-10-30T00:30:00+09:00"), "2026-10-29");
  const inside = { ...task("2026-11-04"), created_at: "2026-11-04T23:30:00-05:00" };
  const outside = { ...task("2026-10-30"), created_at: "2026-10-30T00:30:00+09:00" };
  assert.deepEqual(run({ tasks: [inside, outside] }).result.old, [
    { date: "11-05", input: 1, output: 2, cacheRead: 3 },
  ]);
});
test("invalid legacy timestamps are skipped without throwing or inventing a date", () => {
  const h = run({
    tasks: [{ ...task("2026-11-05"), created_at: "invalid" }],
    convTokens: [{ created_at: "", input_tokens: 5, output_tokens: 5, cache_read_tokens: 5 }],
  });
  assert.equal(h.day("invalid"), undefined);
  assert.deepEqual(h.result.old, []);
});
test("profile selection and per-day sums preserve both existing source semantics", () => {
  const tasks = [task("2026-11-05", 1, 2), task("2026-11-05", 2, 7)];
  const convTokens = [
    { created_at: "2026-11-05T10:00:00Z", llm_profile_id: 1, input_tokens: 3, output_tokens: 6, cache_read_tokens: 9 },
  ];
  const daily = [usage("2026-11-05", "one", 2), usage("2026-11-05", "two", 7), usage("2026-11-05", "one", 3)];
  for (const [tokenTab, n] of [
    ["all", 12],
    [1, 5],
    [2, 7],
  ]) {
    const result = run({ tasks, convTokens, usageStats: { daily }, tokenTab }).result;
    assert.deepEqual(result.old, [{ date: "11-05", input: n, output: n * 2, cacheRead: n * 3 }]);
    assert.deepEqual(result.new, result.old);
  }
});
test("empty, sparse and future-only inputs do not synthesize verified zero buckets", () => {
  assert.deepEqual(run().result, { old: [], new: [] });
  assert.deepEqual(run({ tasks: [task("2026-11-06")], usageStats: { daily: [usage("2026-11-06")] } }).result, {
    old: [],
    new: [],
  });
  const result = run({ tasks: [task("2026-11-01")], usageStats: { daily: [usage("2026-11-01")] } }).result;
  assert.equal(result.old.length, 1);
  assert.deepEqual(result.old, result.new);
});
test("unknown ledger profile attribution remains the existing default-bucket behavior", () => {
  const data = { usageStats: { daily: [usage("2026-11-05", "deleted", 4)] } };
  assert.equal(run({ ...data, tokenTab: 1 }).result.new[0].input, 4);
  assert.deepEqual(run({ ...data, tokenTab: 2 }).result.new, []);
});
test("UI labels disclose UTC calendar days while all-time totals and fetch remain separate", () => {
  assert.match(source, /UTC calendar days, including today\./);
  for (const n of [7, 30, 90, 180, 365]) assert(source.includes(`{ days: ${n}, label: "${n} days" }`));
  assert.match(source, /api\.usageStats\(365\)/);
  assert.doesNotMatch(section("  type Bucket =", "  // Legacy daily:"), /tokenDays|tokenChartRange|tokenChartDay/);
  assert.doesNotMatch(helpers + callbacks, /fetch|setInterval|setTimeout/);
});

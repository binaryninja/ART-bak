import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import { stripTypeScriptTypes } from "node:module";
import test from "node:test";
import vm from "node:vm";

// Real display formatter with a fixed clock. No mounted UI, network or mutations.
const source = await readFile(new URL("../src/app/(main)/dashboard/page.tsx", import.meta.url), "utf8");
const start = source.indexOf("function fmtRel("),
  end = source.indexOf("function fmtTokens(", start);
assert(start >= 0 && end > start);
const helper = stripTypeScriptTypes(source.slice(start, end));
const now = Date.parse("2026-11-17T12:00:00.000Z");
function load(fixedNow) {
  class Clock extends Date {
    static now() {
      return fixedNow;
    }
  }
  const context = vm.createContext({ Date: Clock });
  vm.runInContext(`${helper}\nglobalThis.format=fmtRel;`, context);
  return context.format;
}
const format = load(now);
const past = (ms) => (now - ms) / 1000;
const future = (ms) => (now + ms) / 1000;

test("valid past timestamps retain existing seconds/minutes/hours/days and boundary rounding", () => {
  for (const [ms, expected] of [
    [0, "0s ago"],
    [999, "0s ago"],
    [1000, "1s ago"],
    [59999, "59s ago"],
    [60000, "1m ago"],
    [3599999, "59m ago"],
    [3600000, "1h ago"],
    [86399999, "23h ago"],
    [86400000, "1d ago"],
    [172800000, "2d ago"],
  ])
    assert.equal(format(past(ms)), expected);
});
test("future timestamps use forward wording rather than negative ago labels", () => {
  for (const [ms, expected] of [
    [1, "in 1s"],
    [999, "in 1s"],
    [1000, "in 1s"],
    [30000, "in 30s"],
    [59999, "in 59s"],
    [60000, "in 1m"],
    [3599999, "in 59m"],
    [3600000, "in 1h"],
    [86399999, "in 23h"],
    [86400000, "in 1d"],
    [172800000, "in 2d"],
  ])
    assert.equal(format(future(ms)), expected);
});
test("missing and numeric zero sentinels remain unavailable", () => {
  for (const value of [undefined, null, "", 0, -0, false]) assert.equal(format(value), "—");
});
test("invalid dates, nonfinite numbers and out-of-Date-range numeric values are unavailable", () => {
  for (const value of [
    "not-a-date",
    "2026-99-99T00:00:00Z",
    " ",
    NaN,
    Infinity,
    -Infinity,
    1e308,
    -1e308,
    8.64e12 + 1,
    -8.64e12 - 1,
  ])
    assert.equal(format(value), "—");
});
test("Unix seconds and explicit ISO offsets describe the same instant", () => {
  for (const value of [
    "2026-11-17T11:30:00Z",
    "2026-11-17T06:30:00-05:00",
    "2026-11-17T20:30:00+09:00",
    Date.parse("2026-11-17T11:30:00Z") / 1000,
  ])
    assert.equal(format(value), "30m ago");
  assert.equal(format("2026-11-17T08:30:00-05:00"), "in 1h");
});
test("explicit timestamps remain timezone-independent across representative system zones", () => {
  const script = `${helper}\nDate.now=()=>${now};console.log(JSON.stringify([fmtRel("2026-11-17T11:30:00Z"),fmtRel("2026-11-17T08:30:00-05:00"),fmtRel(${past(86400000)}),fmtRel("invalid")]))`;
  for (const TZ of ["UTC", "America/Los_Angeles", "Pacific/Kiritimati"])
    assert.deepEqual(
      JSON.parse(execFileSync(process.execPath, ["-e", script], { env: { ...process.env, TZ }, encoding: "utf8" })),
      ["30m ago", "in 1h", "1d ago", "—"],
    );
});
test("past rounding agrees with the original formula over deterministic positive durations", () => {
  for (let i = 0; i < 1000; i++) {
    const elapsed = i * 97234719;
    const s = Math.floor(elapsed / 1000),
      m = Math.floor(s / 60),
      h = Math.floor(m / 60);
    let expected;
    if (s < 60) expected = `${s}s ago`;
    else if (m < 60) expected = `${m}m ago`;
    else if (h < 24) expected = `${h}h ago`;
    else expected = `${Math.floor(h / 24)}d ago`;
    assert.equal(format(past(elapsed)), expected);
  }
});
test("all three existing dashboard display callers remain wired without adding a clock timer", () => {
  assert.match(source, /fmtRel\(a.ts\)/);
  assert.match(source, /fmtRel\(f.ts\)/);
  assert.match(source, /fmtRel\(t.last_activity_unix \?\? t.created_unix\)/);
  assert.doesNotMatch(helper, /fetch|setInterval|setTimeout/);
});

test("explicit offsets preserve elapsed time across repeated and skipped DST hours", () => {
  const fall = load(Date.parse("2026-11-01T07:00:00Z"));
  assert.equal(fall("2026-11-01T01:30:00-04:00"), "1h ago");
  assert.equal(fall("2026-11-01T01:30:00-05:00"), "30m ago");
  const spring = load(Date.parse("2026-03-08T08:00:00Z"));
  assert.equal(spring("2026-03-08T01:30:00-05:00"), "1h ago");
  assert.equal(spring("2026-03-08T03:30:00-04:00"), "30m ago");
});

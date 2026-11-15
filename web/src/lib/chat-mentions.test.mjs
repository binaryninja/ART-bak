import assert from "node:assert/strict";
import test from "node:test";
import { activeMention, mentionSearch, mentionToken, selectedMentions } from "./chat-mentions.ts";

test("mention trigger supports Chinese and cursor placement without hijacking email", () => {
  assert.equal(activeMention("user@example.com", 16), null);
  assert.equal(activeMention("\u5df2\u9009 @[\u6f0f\u6d1e#1 X]", 12), null);
  assert.deepEqual(activeMention("\u67e5\u770b@\u6f0f\u6d1e \u540e\u9762\u7684\u6587\u5b57", 5), { start: 2, end: 5, query: "\u6f0f\u6d1e" });
  assert.equal(activeMention("@\u6f0f\u6d1e\n\u4e0b\u4e00\u884c", 8), null);
});

test("English display labels, legacy aliases, IP and keyword search", () => {
  assert.equal(mentionSearch("").categories.length, 9);
  assert.equal(mentionSearch("Finding SQL injection").query, "SQL injection");
  assert.equal(mentionSearch("Endpoint GET /api").query, "GET /api");
  assert.equal(mentionSearch("\u6f0f").categories[0].kind, "finding");
  assert.equal(mentionSearch("\u6f0f\u6d1e").kind, "finding");
  assert.equal(mentionSearch("\u6f0f\u6d1eSQL\u6ce8\u5165").query, "SQL\u6ce8\u5165");
  assert.equal(mentionSearch("ip 192.0.2.1").kind, "ip");
  assert.equal(mentionSearch("\u63a5\u53e3 GET /api").query, "GET /api");
  assert.equal(mentionSearch("acme.com").kind, "");
});

test("tokens roundtrip labels and removing one reference preserves its neighbors", () => {
  const first = mentionToken({ kind: "finding", id: 12, label: "\u6807\u9898[1]\n\u63cf\u8ff0" });
  const second = mentionToken({ kind: "ip", id: 13, label: "192.0.2.1" });
  const value = `\u5206\u6790 ${first} \u548c ${second}`;
  const selected = selectedMentions(value);
  assert.equal(selected.length, 2);
  assert.equal(selected[0].label, "Finding #12 · \u6807\u9898（1） \u63cf\u8ff0");
  const next = value.slice(0, selected[0].start) + value.slice(selected[0].start + selected[0].token.length);
  assert.equal(selectedMentions(next)[0].token, second);
});

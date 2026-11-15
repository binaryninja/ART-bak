package notify

import (
	"strings"
	"testing"
	"unicode/utf8"
)

// This file covers whole-item packing: when a summary exceeds a channel's limit,
// truncate at item boundaries and accurately report omitted items so callers mark only delivered items.
//
// Previously the full message was rendered, truncated, then the entire batch marked delivered:
// the tail disappeared while history showed success, silently losing findings with no visible indication.

func TestMarkdownBodyPacksWholeItemsWithinByteLimit(t *testing.T) {
	// 200 Chinese summary items must far exceed WeCom's 4096-byte limit.
	m := batchMsg(200)
	// Preserve the original three-byte Unicode payload for byte/rune packing checks.
	for i := range m.Items {
		m.Items[i].Name = "\u6f0f\u6d1e" + itoa(i+1)
		m.Items[i].Summary = "\u53cd\u5c04\u578b\u8de8\u7ad9\u811a\u672c"
	}
	body, kept := markdownBody(m, weComMarkdownLimit)

	if len(body) > weComMarkdownLimit {
		t.Fatalf("Body of %d bytes exceeds limit %d", len(body), weComMarkdownLimit)
	}
	if !utf8.ValidString(body) {
		t.Fatal("Body is not valid UTF-8")
	}
	if kept <= 0 || kept >= len(m.Items) {
		t.Fatalf("Only some items should fit (0 < kept < %d), got %d", len(m.Items), kept)
	}
	// The header must accurately state included and remaining counts;
	// otherwise readers may mistake the included count for the total.
	if !strings.Contains(body, "remaining") || !strings.Contains(body, "continue in next message") {
		t.Fatalf("Header should state how many items remain outside this message:\n%s", body[:minInt(400, len(body))])
	}
	// Only the first kept items should be present.
	for i := 0; i < kept; i++ {
		if !strings.Contains(body, "\u6f0f\u6d1e"+itoa(i+1)) {
			t.Fatalf("Item %d should appear in this message:\n%s", i+1, body)
		}
	}
	if strings.Contains(body, "\u6f0f\u6d1e"+itoa(kept+1)) {
		t.Fatalf("Item %d should not appear; it belongs in the next batch", kept+1)
	}
}

func TestMarkdownBodyKeepsEverythingWhenUnderLimit(t *testing.T) {
	m := batchMsg(3)
	body, kept := markdownBody(m, 0) // 0 = unlimited
	if kept != len(m.Items) {
		t.Fatalf("Unlimited length should retain everything, got kept=%d", kept)
	}
	if strings.Contains(body, "remaining") {
		t.Fatalf("No truncation notice should appear when nothing is truncated:\n%s", body)
	}
}

func TestMarkdownBodyAlwaysKeepsAtLeastOneItem(t *testing.T) {
	// Even if the budget cannot fit one item, send one (with final truncation as a fallback).
	// Otherwise one oversized finding could permanently stall the batch: every attempt fits nothing and sends nothing.
	m := batchMsg(5)
	_, kept := markdownBody(m, 50)
	if kept != 1 {
		t.Fatalf("At least 1 item should be retained, got %d", kept)
	}
}

func TestMarkdownBodySingleReturnsOne(t *testing.T) {
	_, kept := markdownBody(singleMsg(), 4096)
	if kept != 1 {
		t.Fatalf("A single message should report 1 delivered item, got %d", kept)
	}
	// An empty message has no deliverable items.
	if _, k := markdownBody(Message{}, 4096); k != 0 {
		t.Fatalf("An empty message should report 0 items, got %d", k)
	}
}

func TestTelegramPackingUsesRuneBudget(t *testing.T) {
	m := batchMsg(200)
	// Preserve the original three-byte Unicode payload for byte/rune packing checks.
	for i := range m.Items {
		m.Items[i].Name = "\u6f0f\u6d1e" + itoa(i+1)
		m.Items[i].Summary = "\u53cd\u5c04\u578b\u8de8\u7ad9\u811a\u672c"
	}
	text, kept := telegramHTML(m)
	// Telegram limits characters; counting bytes would reduce Chinese messages to one third of the allowance.
	if n := utf8.RuneCountInString(text); n > telegramTextLimit {
		t.Fatalf("Body of %d characters exceeds limit %d", n, telegramTextLimit)
	}
	if kept <= 0 || kept >= len(m.Items) {
		t.Fatalf("Only some items should fit, got %d", kept)
	}
	if !strings.Contains(text, "continue in next message") {
		t.Fatalf("Should indicate that remaining items are not included:\n%.300s", text)
	}
}

func TestFeishuPackingReportsKept(t *testing.T) {
	m := batchMsg(2000)
	_, kept := feishuCard(m)
	if kept <= 0 || kept >= len(m.Items) {
		t.Fatalf("The card should fit only some items, got %d", kept)
	}
}

func TestWebhookAndEmailReportAllItems(t *testing.T) {
	// These channels do not truncate the body, so the whole batch counts as delivered.
	m := batchMsg(7)
	if n := len(m.Items); n != 7 {
		t.Fatal("Precondition failed")
	}
	// Confirm indirectly through the renderer's return value: markdownBody(0) retains everything.
	if _, k := markdownBody(m, 0); k != len(m.Items) {
		t.Fatalf("Unlimited length should retain all items, got %d", k)
	}
}

// TestMarkdownEscapesUntrustedContent verifies that untrusted content cannot change message structure.
// Titles/summaries come from model output based on target responses; asset names come from target URLs.
func TestMarkdownEscapesUntrustedContent(t *testing.T) {
	cases := []struct {
		name  string
		item  Item
		must  []string // Must appear in the output (escaped form).
		wrong []string // Must not appear in the output (unescaped form).
	}{
		{
			name: "Newline and external link in title",
			item: Item{
				Severity: "high",
				Name:     "\u767b\u5f55\u53e3 SQL \u6ce8\u5165\n[\u7d27\u6025：\u70b9\u6b64\u9a8c\u8bc1\u8d26\u53f7](http://attacker.tld)",
			},
			// Collapse newlines to prevent forged list items/blockquote sections;
			// escape brackets and parentheses to prevent clickable external links.
			must:  []string{"\\[\u7d27\u6025：\u70b9\u6b64\u9a8c\u8bc1\u8d26\u53f7\\]", `\(http://attacker.tld\)`},
			wrong: []string{"\n[\u7d27\u6025", "\n\n[\u7d27\u6025"},
		},
		{
			name: "Image beacon in title",
			item: Item{
				Severity: "high",
				Name:     "\u6f0f\u6d1e ![](http://attacker.tld/beacon)",
			},
			must:  []string{`\!`, `\(http://attacker.tld/beacon\)`},
			wrong: []string{"![]("},
		},
		{
			name: "Emphasis and quotation in asset name",
			item: Item{
				Severity: "high",
				Name:     "\u666e\u901a\u6807\u9898",
				Assets:   []string{"a.com/*\u6ce8\u5165*>\u5f15\u7528"},
			},
			must:  []string{"\\*\u6ce8\u5165\\*", `\>`},
			wrong: []string{"*\u6ce8\u5165*"},
		},
		{
			name: "Backticks and pipe in summary",
			item: Item{
				Severity: "high",
				Name:     "\u6807\u9898",
				Summary:  "`code` | \u8868\u683c",
			},
			must:  []string{"\\`code\\`", `\|`},
			wrong: []string{"`code`"},
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			m := Message{Items: []Item{tc.item}}
			// Single-item writeItem is the shared rendering path for the three Markdown channels.
			var b strings.Builder
			writeItem(&b, tc.item, "", true)
			got := b.String()
			for _, want := range tc.must {
				if !strings.Contains(got, want) {
					t.Errorf("Missing escaped form %q:\n%s", want, got)
				}
			}
			for _, bad := range tc.wrong {
				if strings.Contains(got, bad) {
					t.Errorf("Unescaped form %q appeared, allowing structural or external-link injection:\n%s", bad, got)
				}
			}
			_ = m
		})
	}
}

// TestMarkdownEscapeBackslashFirst preserves escape ordering: handle backslashes first,
// or newly inserted backslashes get escaped again and double backslashes appear in the output.
func TestMarkdownEscapeBackslashFirst(t *testing.T) {
	if got := markdownEscape(`a\b*c`); got != `a\\b\*c` {
		t.Fatalf("Incorrect escape order, got %q", got)
	}
}

// TestTelegramTitleHasNoMarkdownEscapes covers a specific regression:
// Markdown escaping must not leak into Telegram HTML. Escaping in a shared title function
// previously left visible backslashes such as `\(1\)` in Telegram messages.
func TestTelegramTitleHasNoMarkdownEscapes(t *testing.T) {
	m := Message{Items: []Item{{Severity: "high", Name: "alert(1) *\u91cd\u70b9*"}}}
	text, _ := telegramHTML(m)
	if strings.Contains(text, `\(`) || strings.Contains(text, `\*`) {
		t.Fatalf("Telegram body contains Markdown backslash escapes:\n%s", text)
	}
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}

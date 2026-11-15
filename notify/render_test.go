package notify

import (
	"strings"
	"testing"
	"unicode/utf8"
)

func TestTruncateBytesKeepsValidUTF8(t *testing.T) {
	// Core invariant: WeCom limits bytes; Chinese characters occupy three bytes each.
	// A naive byte cut can split a character, producing invalid UTF-8 rejected by the platform.
	// Exercise every possible cut point with mixed Chinese/English inputs of coprime lengths.
	inputs := []string{
		"\u4e2d\u6587\u6d4b\u8bd5\u5185\u5bb9",
		"\u6df7\u5408 mixed \u5185\u5bb9 content",
		"a\u4e2db\u6587c\u6d4bd\u8bd5e",
		"🔴🟠🟡🔵", // Four-byte emoji make incorrect cuts especially obvious.
		strings.Repeat("\u6f0f\u6d1e", 100),
	}
	for _, in := range inputs {
		for max := 1; max <= len(in)+2; max++ {
			got := TruncateBytes(in, max)
			if !utf8.ValidString(got) {
				t.Fatalf("Input %q max=%d: produced invalid UTF-8 %q", in, max, got)
			}
			if len(got) > max {
				t.Fatalf("Input %q max=%d: result of %d bytes exceeds limit", in, max, len(got))
			}
			// Do not alter content when no truncation is needed.
			if len(in) <= max && got != in {
				t.Fatalf("Input %q max=%d: content changed despite fitting the limit -> %q", in, max, got)
			}
		}
	}
}

func TestTruncateBytesZeroMeansUnlimited(t *testing.T) {
	long := strings.Repeat("x", 10000)
	if got := TruncateBytes(long, 0); got != long {
		t.Fatal("max=0 should mean unlimited")
	}
	if got := TruncateBytes(long, -5); got != long {
		t.Fatal("max<0 should mean unlimited")
	}
}

func TestTruncateBytesEllipsisBudget(t *testing.T) {
	// When max is shorter than the ellipsis, appending the ellipsis must not exceed the limit.
	got := TruncateBytes("abcdefgh", 1)
	if len(got) > 1 {
		t.Fatalf("max=1: result %q of length %d exceeds limit", got, len(got))
	}
	// Ordinary truncation should include an ellipsis.
	if got := TruncateBytes("abcdefgh", 5); !strings.HasSuffix(got, ellipsis) {
		t.Fatalf("Expected ellipsis, got %q", got)
	}
}

func TestTruncateRunesCountsCharactersNotBytes(t *testing.T) {
	// Preserve the distinction from TruncateBytes: Telegram limits characters,
	// whereas counting bytes would reduce Chinese messages to one third of the allowance.
	s := "\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341"
	got := TruncateRunes(s, 5)
	if n := utf8.RuneCountInString(got); n != 5 {
		t.Fatalf("Expected 5 characters, got %d (%q)", n, got)
	}
	// The same string should be substantially shorter when measured in bytes.
	if utf8.RuneCountInString(TruncateBytes(s, 5)) >= 5 {
		t.Fatal("Byte-based truncation must not produce the same character count as rune-based truncation")
	}
}

func TestOneLineCollapsesWhitespace(t *testing.T) {
	got := OneLine("\u7b2c\u4e00\u884c\n\n\u7b2c\u4e8c\u884c\t\u5e26\u5236\u8868   \u591a\u7a7a\u683c", 0)
	if strings.ContainsAny(got, "\n\t") {
		t.Fatalf("All whitespace should collapse, got %q", got)
	}
	if strings.Contains(got, "  ") {
		t.Fatalf("Consecutive spaces should not remain, got %q", got)
	}
	// The result must remain readable and valid after truncation.
	got = OneLine("\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341", 4)
	if n := utf8.RuneCountInString(got); n != 4 {
		t.Fatalf("Expected 4 characters, got %d (%q)", n, got)
	}
}

func TestTruncateHTMLNeverCutsTagInHalf(t *testing.T) {
	// Naive HTML truncation can leave fragments like `<a href="htt`, causing rejection of the entire message.
	s := "<b>\u6807\u9898</b>\u6b63\u6587\u6b63\u6587\u6b63\u6587<a href=\"https://example.com/very/long/path\">\u67e5\u770b\u8be6\u60c5</a>"
	for max := 1; max <= utf8.RuneCountInString(s)+2; max++ {
		got := TruncateHTML(s, max)
		if n := utf8.RuneCountInString(got); max > 0 && n > max {
			t.Fatalf("max=%d: result of %d characters exceeds limit", max, n)
		}
		// The tail must not contain an unclosed `<` (a final `<` with no subsequent `>`).
		if lt := strings.LastIndex(got, "<"); lt >= 0 && !strings.Contains(got[lt:], ">") {
			t.Fatalf("max=%d: trailing tag was cut -> %q", max, got)
		}
	}
}

func TestAssetLineOmitsExcess(t *testing.T) {
	if got := assetLine(nil, 3); got != "" {
		t.Fatalf("No assets should return an empty string, got %q", got)
	}
	if got := assetLine([]string{"a", "b"}, 3); got != "a, b" {
		t.Fatalf("All assets should be listed when within the limit, got %q", got)
	}
	// When over the limit, show the total so readers know how many assets were omitted.
	got := assetLine([]string{"a", "b", "c", "d", "e"}, 2)
	if !strings.Contains(got, "5 total") {
		t.Fatalf("Should indicate a total of 5, got %q", got)
	}
}

func TestSeverityAndStatusLabels(t *testing.T) {
	if AtLeast("", "low") {
		t.Fatal("Empty severity has ordinal zero and should fail any threshold")
	}
	if !AtLeast("critical", "") {
		t.Fatal("An empty threshold should allow the event")
	}
	if got := StatusLabel("fixed"); got != "Fixed" {
		t.Fatalf("Unexpected status mapping, got %q", got)
	}
	// Echo unknown statuses unchanged rather than inventing labels.
	if got := StatusLabel("weird_status"); got != "weird_status" {
		t.Fatalf("Unknown status should be echoed unchanged, got %q", got)
	}
}

// TestTruncateHTMLNeverCutsEntity covers an audited omission: truncation must avoid
// both partial tags and partial HTML entities.
//
// Cutting `&amp;` into `&amp` may make an entity-aware parser reject the entire message;
// that is too costly given how common long summaries are.
func TestTruncateHTMLNeverCutsEntity(t *testing.T) {
	s := "aaaa&amp;bbbb&lt;cccc&quot;dddd"
	for max := 1; max <= utf8.RuneCountInString(s)+2; max++ {
		got := TruncateHTML(s, max)
		// The tail must not contain an entity fragment with `&` but no matching `;`.
		if amp := strings.LastIndex(got, "&"); amp >= 0 && !strings.Contains(got[amp:], ";") {
			t.Fatalf("max=%d: trailing entity fragment %q", max, got[amp:])
		}
		if strings.Contains(got, "&amp\x00") {
			t.Fatalf("max=%d: malformed entity appeared", max)
		}
	}
}

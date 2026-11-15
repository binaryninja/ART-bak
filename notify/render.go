package notify

import (
	"strings"
	"unicode/utf8"
)

const ellipsis = "…"

// TruncateBytes limits s to max bytes, retaining valid UTF-8 and whole characters.
//
// WeCom markdown has a 4096-byte limit, not a character limit. A CJK character
// uses three bytes; slicing mid-character creates invalid UTF-8 and can reject
// the whole message or display replacement glyphs. Backtrack from the budget
// to the nearest rune start (utf8.RuneStart recognizes continuation bytes).
//
// max<=0 means unlimited. Append an ellipsis unless the budget cannot fit it.
func TruncateBytes(s string, max int) string {
	if max <= 0 || len(s) <= max {
		return s
	}
	budget := max - len(ellipsis)
	suffix := ellipsis
	if budget < 0 {
		// If max cannot fit the ellipsis, truncate without one to stay within budget.
		budget = max
		suffix = ""
	}
	cut := budget
	for cut > 0 && !utf8.RuneStart(s[cut]) {
		cut--
	}
	return s[:cut] + suffix
}

// OneLine folds whitespace and truncates by characters. Use it for IM titles:
// raw newlines in summaries would disrupt tables/headings.
// max<=0 disables the length limit.
func OneLine(s string, max int) string {
	s = strings.Join(strings.Fields(s), " ")
	return TruncateRunes(s, max)
}

// TruncateRunes limits characters rather than bytes and appends an ellipsis.
// max<=0 means unlimited.
//
// WeCom uses bytes while Telegram uses characters. The wrong unit silently
// underfills messages: 4096 bytes fit only about1365 CJK characters.
// Keep both functions and select the platform-appropriate one.
func TruncateRunes(s string, max int) string {
	if max <= 0 {
		return s
	}
	runes := []rune(s)
	if len(runes) <= max {
		return s
	}
	if max <= 1 {
		return string(runes[:max])
	}
	return string(runes[:max-1]) + ellipsis
}

// TruncateHTML truncates an HTML fragment by characters without partial tags.
//
// Plain truncation could leave `<a href="htt`, causing parser rejection or
// swallowing subsequent text into an attribute. First truncate by characters,
// then backtrack before any unclosed trailing `<`.
//
// Do not balance tags: Telegram closes open tags. Handling quoted attributes,
// comments and self-closing tags would add complexity without enough benefit.
func TruncateHTML(s string, max int) string {
	if max <= 0 || len([]rune(s)) <= max {
		return s
	}
	cut := TruncateRunes(s, max)
	// If the suffix has an unclosed tag opener (< without later >), backtrack before it.
	if lt := strings.LastIndex(cut, "<"); lt >= 0 && !strings.Contains(cut[lt:], ">") {
		cut = cut[:lt]
	}
	// Also backtrack over a partial HTML entity such as &amp without its semicolon.
	// Entity-aware parsers may reject the whole message otherwise; a long digest
	// is routine and should not cause an entire notification to be dropped.
	if amp := strings.LastIndex(cut, "&"); amp >= 0 && !strings.Contains(cut[amp:], ";") {
		cut = cut[:amp]
	}
	return cut
}

// packItemCount finds how many whole items fit the digest budget.
//
// Rendering everything then truncating silently drops later items while their
// delivery records may still be marked successful. Whole-item packing leaves
// those items for the next batch; kept reports the number actually delivered
// in this message.
//
// maxSize<=0 means unlimited; reserve accounts for header/footer space.
// size measures the appropriate platform unit (bytes for WeCom/DingTalk,
// characters for Telegram). The wrong unit silently underfills CJK messages.
// render produces the actual item text; content-dependent size cannot be guessed.
//
// Return at least one when items remain, relying on final truncation for an
// extremely long single item so it cannot permanently block the batch.
func packItemCount(items []Item, maxSize, reserve int, footer string, size func(string) int, render func(Item, int) string) int {
	if maxSize <= 0 {
		return len(items)
	}
	budget := maxSize - reserve - size(footer)
	if budget < 0 {
		budget = 0
	}
	used := 0
	for i, it := range items {
		used += size(render(it, i))
		if used > budget && i > 0 {
			return i
		}
	}
	return len(items)
}

// byteSize and runeSize name the packing units explicitly instead of anonymous
// closures whose measurement units are hard to see.
func byteSize(s string) int { return len(s) }
func runeSize(s string) int { return utf8.RuneCountInString(s) }

// assetLine joins asset display names; above limit, abbreviate and show total.
// A finding may reference dozens of assets, which would overwhelm the message.
func assetLine(assets []string, limit int) string {
	if len(assets) == 0 {
		return ""
	}
	if limit <= 0 || len(assets) <= limit {
		return strings.Join(assets, ", ")
	}
	return strings.Join(assets[:limit], ", ") + " and others, " + itoa(len(assets)) + " total"
}

// itoa abbreviates strconv.Itoa for display text without repeated imports.
func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var buf [20]byte
	i := len(buf)
	for n > 0 {
		i--
		buf[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		buf[i] = '-'
	}
	return string(buf[i:])
}

package notify

import (
	"fmt"
	"strings"
)

// Shared rendering for Markdown channels (DingTalk and WeCom). Feishu uses
// card JSON, Telegram HTML and email HTML in their respective adapters.

// maxAssetsShown bounds displayed assets. Findings can reference dozens;
// listing all adds noise, and few readers inspect domains beyond the fourth.
const maxAssetsShown = 3

// maxSummaryRunes bounds summaries: IM prompts readers to view details,
// not a full report; complete content remains in the platform.
const maxSummaryRunes = 120

// markdownReservedBytes reserves header (digest/count/severity/truncation)
// and footer (platform link) space during whole-item packing. Losing these
// would hide which batch it is and how many items remain undisplayed.
const markdownReservedBytes = 320

// markdownEscape escapes Markdown metacharacters.
//
// Finding titles, summaries, classes and asset names are untrusted: model
// output reflects target responses, and discovered asset URLs include target-
// controlled query strings. Without escaping, a title such as
//
// Login SQL injection\n[Urgent: verify account](http://attacker.tld)
//
// becomes a clickable external link in DingTalk/Feishu. An image such as
// `![](http://attacker.tld/beacon)` may be fetched by the reader client,
// revealing the reader IP and that the finding was read. Even harmless bold
// or blockquote injection can push severe findings below the fold.
//
// Escape heading/link/emphasis/list/quote/strikethrough syntax. Process `\`
// first so later inserted escapes are not escaped again.
func markdownEscape(s string) string {
	replacer := strings.NewReplacer(
		`\`, `\\`,
		"`", "\\`",
		"*", `\*`,
		"_", `\_`,
		"[", `\[`,
		"]", `\]`,
		"(", `\(`,
		")", `\)`,
		"!", `\!`,
		"#", `\#`,
		">", `\>`,
		"|", `\|`,
		"~", `\~`,
	)
	return replacer.Replace(s)
}

// markdownText normalizes untrusted text to one line and escapes it for body
// rendering. Escaping alone does not stop newlines from forging list items
// or blockquotes.
func markdownText(s string, maxRunes int) string {
	return markdownEscape(OneLine(s, maxRunes))
}

// markdownTitle returns raw unescaped text for channel title bars/cards.
//
// Do not escape here: four output contexts share this title, including Markdown,
// Telegram HTML, Feishu plain_text and Webhook JSON/email subjects. Each
// context has different escaping rules; Markdown escapes would leave visible
// backslashes in HTML or contaminate JSON values. Escape at each output sink
// (writeItem/feishuItemLines/telegramEscape). Escaping the shared title once
// caused literal `\(1\)` in Telegram messages.
func markdownTitle(m Message) string {
	if m.Batch {
		return fmt.Sprintf("Finding digest · %d total", len(m.Items))
	}
	if len(m.Items) == 0 {
		return "Finding notification"
	}
	it := m.Items[0]
	return fmt.Sprintf("[%s] %s", SeverityLabel(it.Severity), OneLine(it.Title(), 0))
}

// markdownBody returns the body and actual included item count.
//
// kept is the number delivered in this message. Mark only those first items
// delivered; items excluded by channel limits must remain for the next batch.
// Otherwise truncation silently drops findings while delivery history falsely
// reports that the entire batch was delivered.
//
// maxBytes<=0 means unlimited.
func markdownBody(m Message, maxBytes int) (string, int) {
	if !m.Batch {
		if len(m.Items) == 0 {
			return "", 0
		}
		var b strings.Builder
		writeItem(&b, m.Items[0], "", true)
		// Send even an oversized single finding, relying on final truncation;
		// partial information is preferable to delivering nothing.
		return TruncateBytes(b.String(), maxBytes), 1
	}

	footer := ""
	if m.HomeURL != "" {
		footer = fmt.Sprintf("\n[View all in platform](%s)\n", m.HomeURL)
	}
	kept := packItemCount(m.Items, maxBytes, markdownReservedBytes, footer, byteSize, func(it Item, idx int) string {
		var b strings.Builder
		writeItem(&b, it, fmt.Sprintf("%d. ", idx+1), false)
		return b.String()
	})

	items := m.Items[:kept]
	var b strings.Builder
	b.WriteString(markdownBatchIntro(m, items, len(m.Items)))
	for i, it := range items {
		writeItem(&b, it, fmt.Sprintf("%d. ", i+1), false)
	}
	b.WriteString(footer)
	return TruncateBytes(b.String(), maxBytes), kept
}

// markdownBatchIntro renders time window, count and severity distribution,
// letting recipients judge urgency without opening the platform.
//
// items are the entries that fit; total is the full batch count. When they
// differ, explicitly state how many continue in the next message, rather
// than implying the displayed count is everything.
func markdownBatchIntro(m Message, items []Item, total int) string {
	var b strings.Builder
	if m.WindowMinutes > 0 {
		fmt.Fprintf(&b, "**Last %d minutes: %d new findings**", m.WindowMinutes, total)
	} else {
		fmt.Fprintf(&b, "**%d new findings**", total)
	}
	if extra := total - len(items); extra > 0 {
		fmt.Fprintf(&b, " (showing first %d here; remaining %d continue in next message)", len(items), extra)
	}
	// Show severity distribution for only the included items, so a displayed
	// Critical3 count matches the entries readers can actually count.
	counts := map[string]int{}
	for _, it := range items {
		counts[it.Severity]++
	}
	var parts []string
	for _, sev := range []string{"critical", "high", "medium", "low"} {
		if n := counts[sev]; n > 0 {
			parts = append(parts, fmt.Sprintf("%s %d", SeverityLabel(sev), n))
		}
	}
	if len(parts) > 0 {
		b.WriteString("\n" + strings.Join(parts, " · "))
	}
	b.WriteString("\n\n")
	return b.String()
}

// writeItem renders one finding.
//
// prefix is the digest sequence number; single=true includes summary/detail
// link. Digest items stay on one line so50 findings do not become a document.
//
// External title/class/assets/summary pass through markdownText for one-line
// normalization and escaping. Detail links derive from administrator-configured
// public_base_url and must remain clickable, so output them directly.
func writeItem(b *strings.Builder, it Item, prefix string, single bool) {
	line := fmt.Sprintf("%s**%s · %s**", prefix, SeverityLabel(it.Severity), markdownText(it.Title(), 0))
	if !single {
		// Digest mode: one line with compressed assets and summary.
		var extras []string
		if a := assetLine(it.Assets, maxAssetsShown); a != "" {
			extras = append(extras, markdownText(a, 0))
		}
		if it.Summary != "" {
			extras = append(extras, markdownText(it.Summary, 60))
		}
		if len(extras) > 0 {
			line += " — " + strings.Join(extras, " · ")
		}
		b.WriteString(line + "\n")
		return
	}
	b.WriteString(line + "\n")
	if it.IsStatusChange() {
		fmt.Fprintf(b, "**Status change**: %s → %s\n",
			markdownText(StatusLabel(it.FromStatus), 0), markdownText(StatusLabel(it.ToStatus), 0))
	}
	if it.VulnClass != "" && it.VulnClass != it.Title() {
		fmt.Fprintf(b, "**Type**: %s\n", markdownText(it.VulnClass, 0))
	}
	if a := assetLine(it.Assets, maxAssetsShown); a != "" {
		fmt.Fprintf(b, "**Assets**: %s\n", markdownText(a, 0))
	}
	if it.Summary != "" {
		if s := markdownText(it.Summary, maxSummaryRunes); s != "" {
			fmt.Fprintf(b, "**Summary**: %s\n", s)
		}
	}
	if it.DetailURL != "" {
		fmt.Fprintf(b, "[View details](%s)\n", it.DetailURL)
	}
}

// Package notify implements IM/email channel adapters for finding notifications.
//
// This leaf package depends only on the standard library, not DB/server code.
// Configuration arrives as map[string]any (notification_channels.config JSONB),
// and content as Message. Signing, UTF-8 truncation and filter matching can be
// tested without PostgreSQL; server code owns orchestration.
//
// Channel implementations must be stateless: multiple configurations and bot
// instances reuse them concurrently. Every credential comes from cfg; never cache
// Webhook URLs or credentials in implementation fields.
package notify

// Channel identifiers are the allowed notification_channels.kind values,
// validated by a server allowlist rather than DB CHECK for extensibility.
const (
	KindDingTalk = "dingtalk" // DingTalk custom bot
	KindFeishu   = "feishu"   // Feishu/Lark custom bot
	KindWeCom    = "wecom"    // WeCom group bot
	KindWebhook  = "webhook"  // Generic Webhook: custom method/headers/JSON template
	KindTelegram = "telegram" // Telegram Bot API
	KindEmail    = "email"    // SMTP email
)

// Event kinds correspond to notification_events.kind.
const (
	EventFindingCreated       = "finding_created"
	EventFindingStatusChanged = "finding_status_changed"
)

// InitKind is the fallback for an empty configuration kind.
const InitKind = KindDingTalk

// severityRank maps severity to comparable ranks. Unknown values return 0,
// so a configured threshold blocks them rather than flooding uncertain alerts.
var severityRank = map[string]int{
	"low":      1,
	"medium":   2,
	"high":     3,
	"critical": 4,
}

// SeverityRank returns a severity rank, or 0 for unknown values.
func SeverityRank(severity string) int { return severityRank[severity] }

// SeverityLabel returns English severity labels with emoji for titles/cards.
// Unknown values are returned unchanged, not invented.
func SeverityLabel(severity string) string {
	switch severity {
	case "critical":
		return "🔴 Critical"
	case "high":
		return "🟠 High"
	case "medium":
		return "🟡 Medium"
	case "low":
		return "🔵 Low"
	default:
		return severity
	}
}

// StatusLabel returns English handling-state labels for transition messages.
func StatusLabel(status string) string {
	switch status {
	case "pending":
		return "Pending"
	case "in_progress":
		return "In progress"
	case "confirmed":
		return "Confirmed"
	case "resolved":
		return "Resolved"
	case "fixed":
		return "Fixed"
	case "false_positive":
		return "False positive"
	case "ignored":
		return "Ignored"
	case "duplicate":
		return "Duplicate"
	case "risk_accepted":
		return "Risk accepted"
	default:
		return status
	}
}

// AtLeast checks the minimum severity; empty min allows everything.
// Unknown severity ranks 0 and is rejected by any nonempty known threshold.
func AtLeast(severity, min string) bool {
	if min == "" {
		return true
	}
	return SeverityRank(severity) >= SeverityRank(min)
}

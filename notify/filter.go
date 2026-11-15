package notify

import (
	"encoding/json"
	"fmt"
	"slices"
	"strings"
)

// Filter defines the notification_channels.filter JSONB contract for per-channel filters. All fields are optional; defaults mean no filtering, also the malformed-config fallback in ParseFilter.
type Filter struct {
	// MinSeverity is the low/medium/high/critical threshold; empty means no threshold.
	MinSeverity string `json:"min_severity"`
	// TaskIDs/AssetIDs impose no restriction when empty; otherwise the event must intersect them.
	TaskIDs  []int64 `json:"task_ids"`
	AssetIDs []int64 `json:"asset_ids"`
	// Empty VulnClassInclude accepts all classes; otherwise vulnclass must contain an included keyword. Any VulnClassExclude match rejects, with exclusion taking precedence. Case-insensitive substring matching avoids malformed regular expressions silently disabling channels.
	VulnClassInclude []string `json:"vulnclass_include"`
	VulnClassExclude []string `json:"vulnclass_exclude"`
	// OnStatusChange controls finding-status-change notifications, meaningful only in realtime mode.
	OnStatusChange bool `json:"on_status_change"`
}

// ParseFilter parses channel filtering configuration and never returns an error. Malformed configuration deliberately falls back to a zero-value Filter, matching all events. Sending one extra alert is preferable to silently missing a high-severity finding. A seemingly configured channel that sends nothing is the worst failure mode.
func ParseFilter(raw []byte) Filter {
	var f Filter
	if len(raw) == 0 {
		return f
	}
	// On parse failure f stays zero-valued, meaning no filtering.
	_ = json.Unmarshal(raw, &f)
	return f
}

// ValidMinSeverity checks thresholds; an empty string means unrestricted.
func ValidMinSeverity(s string) bool {
	if s == "" {
		return true
	}
	_, ok := severityRank[s]
	return ok
}

// Validate checks constrained filter values when saving a channel. Unknown thresholds make Match evaluate rank >= 0, always true; a typo such as "hgih" silently sends everything. That avoids missed alerts but floods channels despite users expecting severity filtering, with no visible configuration warning. Reject such silent degradation at write time.
//
// Validate only on writes. Reads retain ParseFilter's tolerant behavior so existing invalid data does not make the channel unreadable.
func (f Filter) Validate() error {
	if !ValidMinSeverity(f.MinSeverity) {
		return fmt.Errorf("invalid minimum severity %q; choose low / medium / high / critical, or leave empty for no limit", f.MinSeverity)
	}
	return nil
}

// Match decides whether to deliver an event to this filtered channel. Like ParseFilter, it never returns an error: internal anomalies count as matches. Check event type, severity threshold, task/asset scope, then vulnerability-class keywords.
func Match(f Filter, s Snapshot) bool {
	// Status changes require explicit opt-in. Default off because users generally expect new findings, not every status transition.
	if s.Kind == EventFindingStatusChanged && !f.OnStatusChange {
		return false
	}
	if !AtLeast(s.Severity, f.MinSeverity) {
		return false
	}
	if len(f.TaskIDs) > 0 && !slices.Contains(f.TaskIDs, s.TaskID) {
		return false
	}
	if len(f.AssetIDs) > 0 && !intersectsInt(f.AssetIDs, s.AssetIDs) {
		return false
	}
	// Exclusion takes precedence: any excluded keyword rejects even if inclusion also matches.
	if len(f.VulnClassExclude) > 0 && containsAnyFold(s.VulnClass, f.VulnClassExclude) {
		return false
	}
	if len(f.VulnClassInclude) > 0 && !containsAnyFold(s.VulnClass, f.VulnClassInclude) {
		return false
	}
	return true
}

func intersectsInt(a, b []int64) bool {
	// Linear scanning suffices for small, manually selected sets of a few dozen entries; building maps costs more than it saves.
	for _, v := range b {
		if slices.Contains(a, v) {
			return true
		}
	}
	return false
}

// containsAnyFold checks whether s contains any keyword, case-insensitively.
func containsAnyFold(s string, keywords []string) bool {
	lower := strings.ToLower(s)
	for _, kw := range keywords {
		kw = strings.ToLower(strings.TrimSpace(kw))
		if kw != "" && strings.Contains(lower, kw) {
			return true
		}
	}
	return false
}

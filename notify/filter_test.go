package notify

import "testing"

func TestParseFilterMalformedFallsBackToMatchAll(t *testing.T) {
	// Malformed JSON, empty input, and mistyped fields must all fall back to the zero-value Filter,
	// meaning no filtering. This implements the preference for extra notifications over missed ones:
	// errors or partial parsing could silently discard every high-severity notification after one configuration typo.
	cases := []struct {
		name string
		raw  string
	}{
		{"Empty input", ""},
		{"Invalid JSON", `{not json`},
		{"Truncated JSON", `{"min_severity":`},
		{"Type mismatch", `{"min_severity": 123, "task_ids": "abc"}`},
		{"Top-level array", `[1,2,3]`},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			f := ParseFilter([]byte(tc.raw))
			if f.MinSeverity != "" || len(f.TaskIDs) != 0 || len(f.AssetIDs) != 0 {
				t.Fatalf("Malformed configuration should become a zero-value Filter, got %+v", f)
			}
			// The zero-value Filter must match every event.
			ev := Snapshot{Kind: EventFindingCreated, Severity: "low", VulnClass: "XSS"}
			if !Match(f, ev) {
				t.Fatal("The zero-value Filter should match all events")
			}
		})
	}
}

func TestMatchSeverityThreshold(t *testing.T) {
	ev := func(sev string) Snapshot {
		return Snapshot{Kind: EventFindingCreated, Severity: sev}
	}
	cases := []struct {
		min    string
		sev    string
		expect bool
	}{
		{"", "low", true},
		{"", "critical", true},
		{"high", "critical", true},
		{"high", "high", true},
		{"high", "medium", false},
		{"high", "low", false},
		{"critical", "high", false},
		{"critical", "critical", true},
		// Unknown severity has ordinal zero and fails any nonempty threshold (do not notify when uncertain).
		{"low", "", false},
		{"low", "unknown", false},
		{"", "", true},
	}
	for _, tc := range cases {
		got := Match(Filter{MinSeverity: tc.min}, ev(tc.sev))
		if got != tc.expect {
			t.Errorf("min=%q sev=%q: want %v got %v", tc.min, tc.sev, tc.expect, got)
		}
	}
}

func TestMatchScopeRestrictions(t *testing.T) {
	ev := Snapshot{
		Kind:      EventFindingCreated,
		Severity:  "high",
		TaskID:    7,
		AssetIDs:  []int64{10, 20},
		VulnClass: "SQL\u6ce8\u5165",
	}
	cases := []struct {
		name   string
		filter Filter
		expect bool
	}{
		{"Empty scope is unrestricted", Filter{}, true},
		{"Task matches", Filter{TaskIDs: []int64{7}}, true},
		{"Task does not match", Filter{TaskIDs: []int64{8}}, false},
		{"Task selection includes a match", Filter{TaskIDs: []int64{8, 7}}, true},
		{"Asset sets overlap", Filter{AssetIDs: []int64{20, 99}}, true},
		{"Asset sets do not overlap", Filter{AssetIDs: []int64{99}}, false},
		{"Task and asset both match", Filter{TaskIDs: []int64{7}, AssetIDs: []int64{10}}, true},
		{"Task matches but asset does not", Filter{TaskIDs: []int64{7}, AssetIDs: []int64{99}}, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := Match(tc.filter, ev); got != tc.expect {
				t.Errorf("Want %v got %v", tc.expect, got)
			}
		})
	}
}

func TestMatchVulnClassKeywords(t *testing.T) {
	ev := func(class string) Snapshot {
		return Snapshot{Kind: EventFindingCreated, Severity: "high", VulnClass: class}
	}
	cases := []struct {
		name   string
		filter Filter
		class  string
		expect bool
	}{
		{"Empty include accepts all", Filter{}, "\u4efb\u610f\u7c7b\u578b", true},
		{"Include matches", Filter{VulnClassInclude: []string{"SQL"}}, "SQL\u6ce8\u5165", true},
		{"Include does not match", Filter{VulnClassInclude: []string{"\u547d\u4ee4\u6267\u884c"}}, "SQL\u6ce8\u5165", false},
		{"Any include keyword matches", Filter{VulnClassInclude: []string{"\u547d\u4ee4\u6267\u884c", "SQL"}}, "SQL\u6ce8\u5165", true},
		{"Case-insensitive matching", Filter{VulnClassInclude: []string{"sql"}}, "SQL\u6ce8\u5165", true},
		{"Exclude match rejects", Filter{VulnClassExclude: []string{"\u4fe1\u606f\u6cc4\u9732"}}, "\u4fe1\u606f\u6cc4\u9732", false},
		{"No exclude match allows", Filter{VulnClassExclude: []string{"\u4fe1\u606f\u6cc4\u9732"}}, "SQL\u6ce8\u5165", true},
		// Exclusion takes precedence over inclusion when both match.
		{"Exclusion precedes inclusion", Filter{
			VulnClassInclude: []string{"SQL"},
			VulnClassExclude: []string{"\u6ce8\u5165"},
		}, "SQL\u6ce8\u5165", false},
		// Ignore whitespace-only keywords, rather than matching every string containing a space.
		{"Whitespace keywords ignored", Filter{VulnClassInclude: []string{"", "  "}}, "SQL\u6ce8\u5165", false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := Match(tc.filter, ev(tc.class)); got != tc.expect {
				t.Errorf("Want %v got %v", tc.expect, got)
			}
		})
	}
}

func TestMatchStatusChangeRequiresOptIn(t *testing.T) {
	ev := Snapshot{Kind: EventFindingStatusChanged, Severity: "critical", FromStatus: "pending", ToStatus: "fixed"}
	// Off by default: notification of findings usually means newly discovered findings, not a status-change log.
	if Match(Filter{MinSeverity: "low"}, ev) {
		t.Fatal("Status changes should be skipped unless enabled")
	}
	if !Match(Filter{OnStatusChange: true}, ev) {
		t.Fatal("Status changes should match when on_status_change is enabled")
	}
	// Creation events are unaffected by on_status_change.
	created := Snapshot{Kind: EventFindingCreated, Severity: "critical"}
	if !Match(Filter{MinSeverity: "low"}, created) {
		t.Fatal("Creation events must not depend on on_status_change")
	}
}

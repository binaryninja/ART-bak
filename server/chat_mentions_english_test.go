package server

import (
	"fmt"
	"strings"
	"testing"
)

// These cases preserve the existing numeric-ID/type authority boundary while
// accepting English display labels alongside historical localized tokens.
func TestChatMentionEnglishAliases(t *testing.T) {
	cases := map[string]string{
		"Finding": "finding", "Asset": "asset", "Company": "company", "Endpoint": "endpoint",
		"IP": "ip", "Application": "app", "App": "app", "Domain": "root_domain",
		"Subdomain": "subdomain", "Service": "service",
	}
	for label, kind := range cases {
		t.Run(label, func(t *testing.T) {
			refs, err := parseChatMentions(fmt.Sprintf("@[%s#42 Display label]", label))
			if err != nil || len(refs) != 1 || refs[0].Kind != kind || refs[0].ID != 42 {
				t.Fatalf("refs=%+v err=%v", refs, err)
			}
		})
	}
	refs, err := parseChatMentions("@[Finding#12 New] @[\u6f0f\u6d1e#12 Legacy] @[App#42 Short] @[Application#42 Long]")
	if err != nil || len(refs) != 2 || refs[0].Kind != "finding" || refs[1].Kind != "app" {
		t.Fatalf("mixed aliases must deduplicate by type and ID: refs=%+v err=%v", refs, err)
	}
	for _, message := range []string{"@[Finding#0]", "@[Asset#999999999999999999999999]"} {
		if _, err := parseChatMentions(message); err == nil {
			t.Fatalf("accepted invalid numeric ID: %q", message)
		}
	}
	var message strings.Builder
	for id := 1; id <= 11; id++ {
		fmt.Fprintf(&message, "@[Asset#%d] ", id)
	}
	if _, err := parseChatMentions(message.String()); err == nil {
		t.Fatal("accepted more than ten references")
	}
	refs, err = parseChatMentions("@[Unknown#42] ordinary@example.com @Finding")
	if err != nil || len(refs) != 0 {
		t.Fatalf("unrecognized labels must remain plain text: refs=%+v err=%v", refs, err)
	}
}

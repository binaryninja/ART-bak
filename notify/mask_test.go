package notify

import (
	"errors"
	"strings"
	"testing"
)

func TestMaskedValueHidesBodyButKeepsTailHint(t *testing.T) {
	const secret = "https://oapi.dingtalk.com/robot/send?access_token=abcdef123456"
	got := MaskedValue(secret)
	if strings.Contains(got, "abcdef123456") {
		t.Fatalf("Masked value leaked the complete credential: %q", got)
	}
	if strings.Contains(got, "oapi.dingtalk.com") {
		t.Fatalf("Masked value must not expose the URL body: %q", got)
	}
	// Keep the last six characters so users can identify the bot.
	if !strings.HasSuffix(got, "123456") {
		t.Fatalf("Keep the last six characters as an identification hint: %q", got)
	}
	if !IsMasked(got) {
		t.Fatalf("IsMasked must recognize the masked value: %q", got)
	}
}

func TestMaskedValueShortSecretGivesNoHint(t *testing.T) {
	// Exposing the last six characters of a short credential would expose the entire credential.
	for _, s := range []string{"abc", "abcdef", ""} {
		got := MaskedValue(s)
		if got != MaskedPrefix {
			t.Fatalf("Credential of length %d must not expose a suffix hint, got %q", len(s), got)
		}
		if s != "" && strings.Contains(got, s) {
			t.Fatalf("Masked value contains the original value: %q", got)
		}
	}
}

func TestMaskConfigMasksOnlySecrets(t *testing.T) {
	cfg := map[string]any{
		"webhook": "https://example.com/hook?token=SECRETVALUE",
		"secret":  "SECtest123456",
		"port":    float64(587),
		"host":    "smtp.example.com",
	}
	masked := MaskConfig(KindDingTalk, cfg)
	for _, k := range []string{"webhook", "secret"} {
		s, _ := masked[k].(string)
		if !IsMasked(s) {
			t.Errorf("%s should be masked, got %q", k, s)
		}
	}
	// Noncredential fields must remain unchanged for the UI to display them.
	if masked["port"] != float64(587) {
		t.Errorf("Noncredential field port must remain unchanged: %v", masked["port"])
	}
}

func TestMaskConfigUnknownKindReturnsEmpty(t *testing.T) {
	// For unknown channel kinds, return empty UI configuration rather than potentially credential-bearing data.
	got := MaskConfig("nope", map[string]any{"webhook": "https://x/y?token=LEAK"})
	if len(got) != 0 {
		t.Fatalf("Unknown channel kind should return empty configuration, got %v", got)
	}
}

func TestMaskConfigDoesNotMutateInput(t *testing.T) {
	// Masking is presentation-only and must not alter stored real values.
	cfg := map[string]any{"webhook": "https://example.com/hook", "secret": "SECtest123456"}
	_ = MaskConfig(KindDingTalk, cfg)
	if IsMasked(cfg["secret"].(string)) {
		t.Fatal("MaskConfig mutated its input, allowing masked values to overwrite real credentials")
	}
}

func TestMergeConfigKeepsStoredOnMaskedIncoming(t *testing.T) {
	stored := map[string]any{"webhook": "https://real/hook", "secret": "REALSECRET", "method": "POST"}
	// The user changed only method; the browser submits masked values and the new method.
	incoming := map[string]any{
		"webhook": MaskedValue("https://real/hook"),
		"secret":  MaskedValue("REALSECRET"),
		"method":  "PUT",
	}
	got := MergeConfig(stored, incoming)
	if got["webhook"] != "https://real/hook" || got["secret"] != "REALSECRET" {
		t.Fatalf("Masked fields should preserve stored values, got %v", got)
	}
	if got["method"] != "PUT" {
		t.Fatalf("The changed field should take effect, got %v", got["method"])
	}
}

func TestMergeConfigEmptyStringClears(t *testing.T) {
	stored := map[string]any{"webhook": "https://real/hook", "secret": "REALSECRET"}
	got := MergeConfig(stored, map[string]any{"secret": ""})
	if _, ok := got["secret"]; ok {
		t.Fatalf("An empty string should clear the field, got %v", got)
	}
	// Keep unmentioned fields (partial-update semantics).
	if got["webhook"] != "https://real/hook" {
		t.Fatalf("Unmentioned fields should remain unchanged, got %v", got)
	}
}

func TestMergeConfigKeepsUnmentionedStoredKeys(t *testing.T) {
	stored := map[string]any{"host": "smtp.example.com", "port": float64(587), "password": "pw"}
	got := MergeConfig(stored, map[string]any{"port": float64(465)})
	if got["host"] != "smtp.example.com" || got["password"] != "pw" {
		t.Fatalf("Unmentioned fields should remain unchanged, got %v", got)
	}
	if got["port"] != float64(465) {
		t.Fatalf("Mentioned fields should be updated, got %v", got["port"])
	}
}

// TestPrepareConfigUpdateBlocksDestinationSwap covers the package's central security invariant:
// changing the destination must not forward old credentials to the new destination.
//
// These cases use attack-shaped inputs (changing only the URL and omitting credentials),
// not just valid inputs for the defense; testing only valid inputs can pass even with a broken defense.
func TestPrepareConfigUpdateBlocksDestinationSwap(t *testing.T) {
	cases := []struct {
		name     string
		kind     string
		stored   map[string]any
		incoming map[string]any
		// wantMissing is the credential key expected to be named in the error.
		wantMissing string
	}{
		{
			name: "Webhook URL change attempts to reuse Authorization header",
			kind: KindWebhook,
			stored: map[string]any{
				"url":     "https://legit.example.com/hook",
				"headers": map[string]any{"Authorization": "Bearer REAL-TOKEN"},
			},
			incoming:    map[string]any{"url": "https://attacker.tld/c"},
			wantMissing: "headers",
		},
		{
			name:        "Telegram base_url change attempts to send Bot Token to another endpoint",
			kind:        KindTelegram,
			stored:      map[string]any{"bot_token": "123456:REAL", "chat_id": "1", "base_url": "https://api.telegram.org"},
			incoming:    map[string]any{"base_url": "https://attacker.tld"},
			wantMissing: "bot_token",
		},
		{
			name:        "SMTP host change attempts to disclose password",
			kind:        KindEmail,
			stored:      map[string]any{"host": "smtp.corp.com", "port": 587, "password": "REALPW", "from": "a@b.c", "to": []any{"d@e.f"}},
			incoming:    map[string]any{"host": "smtp.attacker.tld"},
			wantMissing: "password",
		},
		{
			name:        "Email TLS change also requires resubmitting password",
			kind:        KindEmail,
			stored:      map[string]any{"host": "smtp.corp.com", "port": 587, "tls": false, "password": "REALPW", "from": "a@b.c", "to": []any{"d@e.f"}},
			incoming:    map[string]any{"tls": true},
			wantMissing: "password",
		},
		{
			// A masked value means reuse old credentials, which must also be rejected when the destination changes.
			name:        "Masked credentials submitted with new URL",
			kind:        KindTelegram,
			stored:      map[string]any{"bot_token": "123456:REAL", "chat_id": "1", "base_url": "https://api.telegram.org"},
			incoming:    map[string]any{"base_url": "https://attacker.tld", "bot_token": MaskedValue("123456:REAL")},
			wantMissing: "bot_token",
		},
		{
			name:        "DingTalk Webhook change attempts to reuse signing key",
			kind:        KindDingTalk,
			stored:      map[string]any{"webhook": "https://oapi.dingtalk.com/robot/send?access_token=OLD", "secret": "REALSEC"},
			incoming:    map[string]any{"webhook": "https://attacker.tld/hook"},
			wantMissing: "secret",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			merged, err := PrepareConfigUpdate(tc.kind, tc.stored, tc.incoming)
			if err == nil {
				t.Fatalf("Destination change without resubmitted credentials should be rejected; got configuration %v", merged)
			}
			var target *ErrDestinationChangedWithoutCredentials
			if !errors.As(err, &target) {
				t.Fatalf("Return a dedicated error type for actionable API feedback, got %T: %v", err, err)
			}
			found := false
			for _, m := range target.Missing {
				if m == tc.wantMissing {
					found = true
				}
			}
			if !found {
				t.Fatalf("Error should name missing credential key %q, got %v", tc.wantMissing, target.Missing)
			}
			// The error must tell the operator how to fix the problem.
			if !strings.Contains(err.Error(), tc.wantMissing) {
				t.Errorf("Error should mention %q: %v", tc.wantMissing, err)
			}
		})
	}
}

// TestPrepareConfigUpdateAllowsLegitimateEdits is the inverse case: legitimate edits must not be blocked,
// otherwise users may bypass or remove the protection as too annoying.
func TestPrepareConfigUpdateAllowsLegitimateEdits(t *testing.T) {
	cases := []struct {
		name     string
		kind     string
		stored   map[string]any
		incoming map[string]any
	}{
		{
			name:     "Rename only with unchanged configuration",
			kind:     KindWebhook,
			stored:   map[string]any{"url": "https://legit.example.com/hook", "headers": map[string]any{"Authorization": "Bearer REAL"}},
			incoming: map[string]any{"url": MaskedValue("https://legit.example.com/hook")},
		},
		{
			name:     "Change request method without changing URL or credentials",
			kind:     KindWebhook,
			stored:   map[string]any{"url": "https://legit.example.com/hook", "method": "POST"},
			incoming: map[string]any{"method": "PUT"},
		},
		{
			name:     "Change URL and supply new credentials together",
			kind:     KindWebhook,
			stored:   map[string]any{"url": "https://old.example.com/hook", "headers": map[string]any{"Authorization": "Bearer OLD"}},
			incoming: map[string]any{"url": "https://new.example.com/hook", "headers": map[string]any{"Authorization": "Bearer NEW"}},
		},
		{
			name:     "Change URL and explicitly declare credentials no longer needed",
			kind:     KindWebhook,
			stored:   map[string]any{"url": "https://old.example.com/hook", "headers": map[string]any{"Authorization": "Bearer OLD"}},
			incoming: map[string]any{"url": "https://new.example.com/hook", "headers": ""},
		},
		{
			name:     "Telegram chat_id change is not a destination change",
			kind:     KindTelegram,
			stored:   map[string]any{"bot_token": "t", "chat_id": "1", "base_url": "https://api.telegram.org"},
			incoming: map[string]any{"chat_id": "-100200"},
		},
		{
			name:     "Email recipient change is not a destination change",
			kind:     KindEmail,
			stored:   map[string]any{"host": "smtp.corp.com", "port": 587, "password": "PW", "from": "a@b.c", "to": []any{"x@y.z"}},
			incoming: map[string]any{"to": []any{"new@y.z"}},
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			merged, err := PrepareConfigUpdate(tc.kind, tc.stored, tc.incoming)
			if err != nil {
				t.Fatalf("Legitimate edit was incorrectly blocked: %v", err)
			}
			if merged == nil {
				t.Fatal("Merged result should be returned")
			}
		})
	}
}

// TestPrepareConfigUpdatePortTypeTolerance covers an easy source of false positives:
// frontend and stored JSON numbers use float64, but callers may supply another numeric type.
// Comparing int and float64 with == can incorrectly classify an unchanged port as changed,
// asking users who only renamed a channel to reenter passwords and undermining trust in the protection.
func TestPrepareConfigUpdatePortTypeTolerance(t *testing.T) {
	stored := map[string]any{"host": "smtp.corp.com", "port": float64(587), "password": "PW"}
	// Submit the same port as an int.
	if _, err := PrepareConfigUpdate(KindEmail, stored, map[string]any{"port": 587}); err != nil {
		t.Fatalf("Equal port values with different types must not count as a destination change: %v", err)
	}
	// A real port change must still be blocked.
	if _, err := PrepareConfigUpdate(KindEmail, stored, map[string]any{"port": 25}); err == nil {
		t.Fatal("Port change should be blocked")
	}
}

// TestPrepareConfigUpdateSurvivesRepeatedSaveWithBlankDestination covers optional destination fields:
// an empty Telegram base_url means use the official API URL.
//
// Previously, channels could fail every save from the second save onward:
//
// Creation stores base_url:"" directly from frontend config without MergeConfig.
// First save: MergeConfig treats the empty string as explicit clearing and deletes the key.
// Second save: incoming remains empty, but the stored key is absent, incorrectly implying a URL change.
// The masked bot_token then triggers HTTP 400 requiring credentials to be resubmitted.
//
// Users changed nothing but could no longer save without pasting their Bot Token again.
func TestPrepareConfigUpdateSurvivesRepeatedSaveWithBlankDestination(t *testing.T) {
	stored := map[string]any{"bot_token": "123:ABC", "chat_id": "-100", "base_url": ""}

	// Frontend buildConfig() submits every defined field: masked credentials and
	// empty strings for empty text boxes. Reproduce its complete output, not only changed keys.
	submit := func() map[string]any {
		return map[string]any{
			"bot_token": MaskedValue("123:ABC"),
			"chat_id":   "-100",
			"base_url":  "",
		}
	}

	// First save: only the channel name changed; configuration is submitted unchanged.
	merged, err := PrepareConfigUpdate(KindTelegram, stored, submit())
	if err != nil {
		t.Fatalf("First save was incorrectly blocked: %v", err)
	}
	if _, ok := merged["base_url"]; ok {
		t.Fatal("Premise changed: MergeConfig should delete the empty string; this case covers the absent-key state")
	}

	// Second save: identical submission; the user changed nothing.
	merged2, err := PrepareConfigUpdate(KindTelegram, merged, submit())
	if err != nil {
		t.Fatalf("Second save was incorrectly blocked despite no user changes: %v", err)
	}
	// Third save: confirm saves work consistently rather than only once.
	if _, err := PrepareConfigUpdate(KindTelegram, merged2, submit()); err != nil {
		t.Fatalf("Third save was incorrectly blocked: %v", err)
	}
	// Credentials must survive throughout, unaffected by empty-string clearing.
	if got := merged2["bot_token"]; got != "123:ABC" {
		t.Fatalf("Bot Token should preserve its original value, got %v", got)
	}
}

// TestPrepareConfigUpdateStillGuardsBlankDestinationChanges is the paired assertion:
// treating empty and absent keys as equivalent must not allow real destination changes.
// Both directions can transmit credentials: Telegram's Bot Token is in the URL path,
// so changing base_url sends the token to the new destination.
func TestPrepareConfigUpdateStillGuardsBlankDestinationChanges(t *testing.T) {
	// Direction one: empty (official URL) to a self-hosted URL.
	official := map[string]any{"bot_token": "123:ABC", "chat_id": "-100"}
	if _, err := PrepareConfigUpdate(KindTelegram, official, map[string]any{
		"bot_token": MaskedValue("123:ABC"),
		"base_url":  "https://tg-proxy.attacker.tld",
	}); err == nil {
		t.Fatal("Changing from the official URL to a self-hosted URL must require resubmitting the Token")
	}

	// Direction two: clearing a self-hosted URL (returning to the official API) also changes the destination.
	proxied := map[string]any{"bot_token": "123:ABC", "base_url": "https://proxy.internal/bot"}
	if _, err := PrepareConfigUpdate(KindTelegram, proxied, map[string]any{
		"bot_token": MaskedValue("123:ABC"),
		"base_url":  "",
	}); err == nil {
		t.Fatal("Clearing a self-hosted URL also changes the destination and must require resubmitting the Token")
	}
}

func TestDestinationKeysDeclaredForEveryKind(t *testing.T) {
	// Like SecretKeys, missing destination-key declarations leave a channel outside PrepareConfigUpdate protection.
	for kind, ch := range registry {
		if len(ch.DestinationKeys()) == 0 {
			t.Errorf("Channel %s declares no destination keys, disabling credential-forwarding protection", kind)
		}
		if len(ch.SecretKeys()) == 0 {
			t.Errorf("Channel %s declares no credential keys", kind)
		}
	}
}

func TestSecretKeysDeclaredForEveryKind(t *testing.T) {
	// The compiler requires SecretKeys on every channel; also verify each channel actually supplies keys.
	// An empty slice would allow its credentials to appear in plaintext in the browser.
	expect := map[string]bool{
		KindDingTalk: true, KindFeishu: true, KindWeCom: true,
		KindWebhook: true, KindTelegram: true, KindEmail: true,
	}
	for kind, ch := range registry {
		if !expect[kind] {
			t.Errorf("Channel %s has no masking expectation registered in this test", kind)
			continue
		}
		if len(ch.SecretKeys()) == 0 {
			t.Errorf("Channel %s declares no credential fields; its configuration would be echoed in plaintext", kind)
		}
	}
}

// TestPrepareConfigUpdateRejectsMaskedInContainer covers an audited gap:
// a masked sentinel nested inside a nonstring structure (such as webhook.headers)
// was not recognized by MergeConfig's string-prefix check, so literal "__masked__"
// could be stored as a real header value, silently breaking subsequent authentication.
func TestPrepareConfigUpdateRejectsMaskedInContainer(t *testing.T) {
	stored := map[string]any{
		"url":     "https://legit.example.com/hook",
		"headers": map[string]any{"Authorization": "Bearer REAL"},
	}
	// A masked sentinel nested inside an object.
	incoming := map[string]any{
		"headers": map[string]any{"Authorization": MaskedPrefix},
	}
	if _, err := PrepareConfigUpdate(KindWebhook, stored, incoming); err == nil {
		t.Fatal("Masked sentinels inside structures should be rejected rather than stored literally")
	}
	// A complete object containing real new values should still be accepted.
	ok := map[string]any{"headers": map[string]any{"Authorization": "Bearer NEW"}}
	if _, err := PrepareConfigUpdate(KindWebhook, stored, ok); err != nil {
		t.Fatalf("Submitting legitimate new headers must not be blocked: %v", err)
	}
}

package notify

import (
	"context"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"strings"
	"testing"
)

// This file covers two related hardening properties:
//   1. Delivery destinations must not use the server to reach internal services/cloud metadata (SSRF).
//   2. URL validation errors must not expose credentials embedded in the URL.
//
// Many tests use httptest receivers on 127.0.0.1, which the default guard blocks.
// TestMain enables AllowLocalTargetsEnv for those cases; each SSRF test below
// explicitly clears it to assert the deny-by-default behavior.

func TestMain(m *testing.M) {
	// Allow ordinary tests to reach local fake receivers; SSRF tests temporarily clear this themselves.
	_ = os.Setenv(AllowLocalTargetsEnv, "1")
	os.Exit(m.Run())
}

// TestDialGuardRejectsLoopbackByDefault asserts the core SSRF invariant:
// with the default configuration, the connection layer must reject loopback delivery.
func TestDialGuardRejectsLoopbackByDefault(t *testing.T) {
	var hit bool
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		hit = true
		_, _ = io.WriteString(w, `{"errcode":0}`)
	}))
	defer srv.Close()

	t.Setenv(AllowLocalTargetsEnv, "") // Disable the escape hatch to restore default behavior.
	_, err := (dingTalkChannel{}).Send(context.Background(),
		map[string]any{"webhook": srv.URL + "/robot/send"}, Message{Items: []Item{{Severity: "high"}}})
	if err == nil {
		t.Fatal("Loopback delivery must not be allowed by default")
	}
	if hit {
		t.Fatal("The request reached the local service; the guard did not work")
	}
	// The error must explain how to opt in (a local SMTP relay is a legitimate configuration).
	if !strings.Contains(err.Error(), AllowLocalTargetsEnv) {
		t.Errorf("Rejection must explain how to explicitly opt in: %v", err)
	}
}

// TestDialGuardAllowsLoopbackWhenOptedIn is the inverse case: explicit opt-in must work,
// otherwise legitimate local postfix/internal-relay deployments would be blocked indiscriminately.
func TestDialGuardAllowsLoopbackWhenOptedIn(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = io.WriteString(w, `{"errcode":0,"errmsg":"ok"}`)
	}))
	defer srv.Close()

	t.Setenv(AllowLocalTargetsEnv, "1")
	if _, err := (dingTalkChannel{}).Send(context.Background(),
		map[string]any{"webhook": srv.URL + "/robot/send"}, Message{Items: []Item{{Severity: "high"}}}); err != nil {
		t.Fatalf("Delivery should work after explicit opt-in: %v", err)
	}
}

func TestIsBlockedDialIP(t *testing.T) {
	blocked := []string{
		"127.0.0.1", "127.1.2.3", "::1",
		"169.254.169.254", // Cloud metadata endpoint: the main reason for this guard.
		"169.254.1.1", "fe80::1",
		"0.0.0.0", "::",
		"224.0.0.1", "ff02::1",
		"::ffff:127.0.0.1", // Normalize IPv4-mapped addresses before checking to prevent bypasses.
		"",
	}
	for _, s := range blocked {
		if !isBlockedDialIP(net.ParseIP(s)) {
			t.Errorf("%s should be rejected", s)
		}
	}
	// RFC1918 private networks are deliberately allowed: self-hosted Mattermost/SMTP relays are common legitimate uses.
	// This assertion preserves that tradeoff; adding private-network blocking will fail here,
	// forcing an explicit decision rather than silently breaking deployments.
	allowed := []string{"10.0.0.5", "172.16.3.4", "192.168.1.10", "8.8.8.8", "2606:4700::1111"}
	for _, s := range allowed {
		if isBlockedDialIP(net.ParseIP(s)) {
			t.Errorf("%s should be allowed (private networks are common legitimate destinations)", s)
		}
	}
}

// TestValidateHTTPURLRejectsLiteralPrivateTargets checks early configuration feedback:
// reject literal disallowed IPs on save, rather than waiting for the first delivery to fail.
func TestValidateHTTPURLRejectsLiteralPrivateTargets(t *testing.T) {
	t.Setenv(AllowLocalTargetsEnv, "")
	for _, raw := range []string{
		"http://127.0.0.1:8080/hook",
		"http://169.254.169.254/latest/meta-data/",
		"http://[::1]:8080/hook",
	} {
		if err := validateHTTPURL(raw); err == nil {
			t.Errorf("%s should be rejected during configuration", raw)
		}
	}
	// Public and private addresses still pass (private addresses also remain allowed at dial time).
	for _, raw := range []string{"https://oapi.dingtalk.com/robot/send", "http://10.0.0.9/hook"} {
		if err := validateHTTPURL(raw); err != nil {
			t.Errorf("%s should pass validation: %v", raw, err)
		}
	}
}

// TestValidateHTTPURLErrorNeverLeaksCredentials covers a branch missed in the previous audit pass.
//
// A failed url.Parse returns *url.Error, whose Error() includes the entire original URL. Previously,
// only http.Client.Do errors were redacted. The added permanent-failure cases
// (file://, gopher://, ftp://) all parsed successfully and reached the scheme-validation branch,
// so their passing results did not demonstrate safety of this branch: false assurance.
func TestValidateHTTPURLErrorNeverLeaksCredentials(t *testing.T) {
	cases := []string{
		"http://127.0.0.1/%zz?access_token=" + leakProbeToken,         // Invalid percent escape.
		"https://a.example.com:port/x?access_token=" + leakProbeToken, // Nonnumeric port.
		"http://[::1?access_token=" + leakProbeToken,                  // Unmatched bracket.
	}
	for _, raw := range cases {
		// First confirm the input actually makes url.Parse fail. Otherwise the test could silently
		// exercise another branch, reproducing the previous false assurance.
		if _, err := url.Parse(raw); err == nil {
			t.Errorf("%q should fail parsing; otherwise this test misses the intended branch", raw)
			continue
		}
		err := validateHTTPURL(raw)
		if err == nil {
			t.Errorf("%q should fail validation", raw)
			continue
		}
		assertNoSecret(t, err.Error(), leakProbeToken)
	}
	// Confirm channel-level wrapping also does not expose the URL.
	t.Setenv(AllowLocalTargetsEnv, "")
	err := (dingTalkChannel{}).Validate(map[string]any{"webhook": cases[0]})
	if err == nil {
		t.Fatal("Invalid URL should fail validation")
	}
	assertNoSecret(t, err.Error(), leakProbeToken)
}

// TestEmailDialGuardRejectsLoopbackByDefault covers the SMTP channel's dial guard.
//
// Email previously used a bare net.Dialer, the only gap in the SSRF protection: setting host to
// 169.254.169.254 or 127.0.0.1 allowed direct connections. If smtp.NewClient's handshake failed,
// the peer's response line entered the error and surfaced through last_error in delivery history:
// the semi-blind read primitive blocked in other channels. Refusal/timeout timing could also probe ports.
//
// TestMain globally enables AllowLocalTargetsEnv for the many 127.0.0.1 fake receivers,
// so this case must clear it itself; otherwise it passes with or without the guard,
// which is why the gap originally escaped the tests.
func TestEmailDialGuardRejectsLoopbackByDefault(t *testing.T) {
	f := newFakeSMTP(t)
	cfg := emailCfg(t, f, nil)

	t.Setenv(AllowLocalTargetsEnv, "") // Disable the escape hatch to restore default behavior.
	_, err := (emailChannel{}).Send(context.Background(), cfg, singleMsg())
	if err == nil {
		t.Fatal("Email delivery to loopback must not be allowed by default")
	}
	// No connection should be established: the Control hook blocks it before EHLO can be sent.
	if f.sawCommand("EHLO") || f.sawCommand("HELO") {
		t.Fatal("An SMTP session was established; the guard did not work")
	}
	// The error must explain opt-in (a local postfix relay is a legitimate configuration).
	if !strings.Contains(err.Error(), AllowLocalTargetsEnv) {
		t.Errorf("Rejection must explain how to explicitly opt in: %v", err)
	}
}

// TestEmailDialGuardAllowsLoopbackWhenOptedIn is the inverse case: explicit opt-in
// must permit delivery. Internal SMTP/local relays are common and must not be blocked indiscriminately.
func TestEmailDialGuardAllowsLoopbackWhenOptedIn(t *testing.T) {
	f := newFakeSMTP(t)
	cfg := emailCfg(t, f, nil)

	t.Setenv(AllowLocalTargetsEnv, "1")
	if _, err := (emailChannel{}).Send(context.Background(), cfg, singleMsg()); err != nil {
		t.Fatalf("Local SMTP delivery should work after explicit opt-in: %v", err)
	}
	if !f.sawCommand("EHLO") {
		t.Fatal("No EHLO observed; the session was not really established")
	}
}

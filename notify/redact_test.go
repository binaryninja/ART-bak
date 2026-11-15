package notify

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
)

// Invariant: no error text from any channel implementation may contain credentials.
//
// Separate coverage is needed because the original channel tests covered success and platform business errors,
// but not transport failures. Connection refusal, DNS failure, and timeout errors are especially dangerous:
// http.Client.Do returns *url.Error containing the complete URL, and these channels
// embed credentials in URLs. Error text can expose credentials through four outputs:
//
// notification_deliveries.last_error -> plaintext database storage
// GET /api/notify/deliveries -> browser response bypassing configuration masking
// Server logs -> often exported and retained
// Test-send endpoint's HTTP 502 response -> directly displayed in the UI
//
// Rather than testing one helper, each channel sends a request guaranteed to fail,
// and the returned error text is checked for the credential.

// credentialCases covers each channel's credential-in-URL pattern:
// DingTalk/WeCom query parameters, Feishu final path segment, and Telegram middle path segment.
var credentialCases = []struct {
	name   string
	ch     Channel
	cfg    map[string]any
	secret string
}{
	{
		name:   "DingTalk access_token in query",
		ch:     dingTalkChannel{},
		cfg:    map[string]any{"webhook": "http://127.0.0.1:1/robot/send?access_token=" + leakProbeToken},
		secret: leakProbeToken,
	},
	{
		name:   "WeCom key in query",
		ch:     weComChannel{},
		cfg:    map[string]any{"webhook": "http://127.0.0.1:1/cgi-bin/webhook/send?key=" + leakProbeToken},
		secret: leakProbeToken,
	},
	{
		name:   "Feishu hook ID in final path segment",
		ch:     feishuChannel{},
		cfg:    map[string]any{"webhook": "http://127.0.0.1:1/open-apis/bot/v2/hook/" + leakProbeToken},
		secret: leakProbeToken,
	},
	{
		name:   "Telegram bot token in middle path segment",
		ch:     telegramChannel{},
		cfg:    map[string]any{"bot_token": leakProbeToken, "chat_id": "1", "base_url": "http://127.0.0.1:1"},
		secret: leakProbeToken,
	},
	{
		name:   "DingTalk signing key",
		ch:     dingTalkChannel{},
		cfg:    map[string]any{"webhook": "http://127.0.0.1:1/robot/send", "secret": leakProbeToken},
		secret: leakProbeToken,
	},
}

// leakProbeToken is a sentinel that cannot be a real credential, searched for in error text.
const leakProbeToken = "LEAKPROBE0123456789abcdef"

// TestChannelErrorsNeverLeakCredentials checks the core invariant.
func TestChannelErrorsNeverLeakCredentials(t *testing.T) {
	for _, tc := range credentialCases {
		t.Run(tc.name, func(t *testing.T) {
			// Guaranteed failure: nobody listens on 127.0.0.1:1, exercising connection refusal.
			_, err := tc.ch.Send(context.Background(), tc.cfg, Message{
				Items: []Item{{FindingID: 1, Severity: "high", Name: "\u6cc4\u9732\u63a2\u9488"}},
			})
			if err == nil {
				t.Fatal("An unreachable destination should return an error")
			}
			assertNoSecret(t, err.Error(), tc.secret)
		})
	}
}

// TestChannelErrorsNeverLeakCredentialsInPermanentPath covers permanent failures:
// URL validation and platform business errors also surface error text and must not expose credentials.
func TestChannelErrorsNeverLeakCredentialsInPermanentPath(t *testing.T) {
	cases := []struct {
		name string
		ch   Channel
		cfg  map[string]any
	}{
		// A credential-bearing malformed URL exercises validateHTTPURL/url.Parse.
		{"Invalid DingTalk URL", dingTalkChannel{}, map[string]any{"webhook": "file:///" + leakProbeToken}},
		{"Invalid WeCom URL", weComChannel{}, map[string]any{"webhook": "gopher://" + leakProbeToken}},
		{"Invalid Feishu URL", feishuChannel{}, map[string]any{"webhook": "ftp://" + leakProbeToken + "/hook"}},
		{"Invalid Telegram API URL", telegramChannel{}, map[string]any{"bot_token": "tok", "chat_id": "1", "base_url": "file://" + leakProbeToken}},
		{"Invalid generic Webhook URL", webhookChannel{}, map[string]any{"url": "javascript:" + leakProbeToken}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, err := tc.ch.Send(context.Background(), tc.cfg, Message{Items: []Item{{Severity: "high"}}})
			if err == nil {
				t.Fatal("Invalid configuration should return an error")
			}
			assertNoSecret(t, err.Error(), leakProbeToken)
		})
	}
}

func assertNoSecret(t *testing.T, text, secret string) {
	t.Helper()
	if strings.Contains(text, secret) {
		t.Fatalf("Error text leaked credential %q:\n    %s", secret, text)
	}
}

func TestRedactRequestTargetKeepsOnlySchemeAndHost(t *testing.T) {
	cases := map[string]string{
		"https://oapi.dingtalk.com/robot/send?access_token=S1":    "https://oapi.dingtalk.com/…",
		"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=S2": "https://qyapi.weixin.qq.com/…",
		"https://open.feishu.cn/open-apis/bot/v2/hook/S3":         "https://open.feishu.cn/…",
		"https://api.telegram.org/botS4/sendMessage":              "https://api.telegram.org/…",
		"http://10.0.0.5:8080/hook":                               "http://10.0.0.5:8080/…",
	}
	for in, want := range cases {
		got := redactRequestTarget(in)
		if got != want {
			t.Errorf("redactRequestTarget(%q) = %q, want %q", in, got, want)
		}
		// Redacted output must not retain any path/query fragment from the original URL.
		if parts := strings.SplitN(in, "://", 2); len(parts) == 2 {
			if hostAndRest := strings.SplitN(parts[1], "/", 2); len(hostAndRest) == 2 && hostAndRest[1] != "" {
				if strings.Contains(got, hostAndRest[1]) {
					t.Errorf("Redacted output still contains path/query fragment %q: %q", hostAndRest[1], got)
				}
			}
		}
	}
	// Never echo an unparseable input unchanged.
	for _, bad := range []string{"", "://", "not a url", "http://"} {
		if got := redactRequestTarget(bad); strings.Contains(got, bad) && bad != "" {
			t.Errorf("Unparseable input %q was echoed as %q", bad, got)
		}
	}
}

// TestRedactTransportErrorStripsURL focuses on *url.Error,
// the http.Client.Do return type and the origin of the leak.
func TestRedactTransportErrorStripsURL(t *testing.T) {
	inner := errors.New("dial tcp 127.0.0.1:1: connect: connection refused")
	uerr := &url.Error{
		Op:  "Post",
		URL: "https://api.telegram.org/bot" + leakProbeToken + "/sendMessage",
		Err: inner,
	}
	got := redactTransportError(uerr)
	assertNoSecret(t, got, leakProbeToken)
	if !strings.Contains(got, "api.telegram.org") {
		t.Errorf("Host should be retained for troubleshooting, got %q", got)
	}
	if !strings.Contains(got, "connection refused") {
		t.Errorf("Underlying cause should be retained for troubleshooting, got %q", got)
	}
	// Preserve Op too; POST versus GET matters when troubleshooting.
	if !strings.Contains(got, "Post") {
		t.Errorf("Operation name should be retained, got %q", got)
	}
}

// TestRedactURLsInTextHandlesFallback covers custom errors other than *url.Error,
// such as redirect-policy failures, whose URLs must also be removed.
func TestRedactURLsInTextHandlesFallback(t *testing.T) {
	in := fmt.Sprintf("\u62d2\u7edd\u8de8\u4e3b\u673a\u91cd\u5b9a\u5411（a.example → http://b.example/bot%s/send）", leakProbeToken)
	got := redactURLsInText(in)
	assertNoSecret(t, got, leakProbeToken)
	if !strings.Contains(got, "http://b.example/…") {
		t.Errorf("URL should be replaced with its redacted form, got %q", got)
	}
	// Leave text without URLs unchanged.
	if plain := "dial tcp: connection refused"; redactURLsInText(plain) != plain {
		t.Error("Text without URLs must not be changed")
	}
}

// TestCrossHostRedirectRefused covers credential-bearing URLs followed across hosts.
// The two httptest servers use different ports on 127.0.0.1; different ports mean different Host values,
// providing a cross-host redirect case.
func TestCrossHostRedirectRefused(t *testing.T) {
	var hit bool
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		hit = true
		_, _ = io.WriteString(w, `{"errcode":0,"errmsg":"ok"}`)
	}))
	defer target.Close()

	redirector := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, target.URL+"/robot/send?access_token="+leakProbeToken, http.StatusTemporaryRedirect)
	}))
	defer redirector.Close()

	_, err := (dingTalkChannel{}).Send(context.Background(),
		map[string]any{"webhook": redirector.URL + "/robot/send?access_token=" + leakProbeToken},
		Message{Items: []Item{{Severity: "high"}}})
	if err == nil {
		t.Fatal("Cross-host redirect should be rejected")
	}
	if hit {
		t.Fatal("Redirect destination was contacted; credentials leaked with the redirect")
	}
	assertNoSecret(t, err.Error(), leakProbeToken)
}

// TestSameHostRedirectAllowed is the inverse case: same-host redirects such as adding a trailing slash
// must remain allowed so legitimate workflows still work.
func TestSameHostRedirectAllowed(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/robot/send" {
			// Same host and port redirect.
			http.Redirect(w, r, "/robot/send/", http.StatusTemporaryRedirect)
			return
		}
		_, _ = io.WriteString(w, `{"errcode":0,"errmsg":"ok"}`)
	}))
	defer srv.Close()

	if _, err := (dingTalkChannel{}).Send(context.Background(),
		map[string]any{"webhook": srv.URL + "/robot/send"},
		Message{Items: []Item{{Severity: "high"}}}); err != nil {
		t.Fatalf("Same-host redirect must not be rejected: %v", err)
	}
}

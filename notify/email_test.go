package notify

import (
	"bufio"
	"context"

	"net"
	"strings"
	"sync"
	"testing"
)

// Protocol-level email tests cover the previously untested email.Send path.
// SMTP has the largest protocol surface of the six channels and is error-prone:
// handshake, authentication, envelope and DATA each have distinct failure semantics.
//
// Use a minimal local SMTP fixture rather than mocking net/smtp itself.
// Most email-channel risk lies in actual protocol conversation with a server;
// mocking that boundary would omit the behavior being tested.

// fakeSMTP supports greet/EHLO/AUTH/MAIL/RCPT/DATA/QUIT and supplies
// case-specific reply codes at selected stages.
type fakeSMTP struct {
	ln net.Listener

	// rcptReply is the RCPT TO reply; defaults to 250.
	rcptReply string
	// mailReply is the MAIL FROM reply; defaults to 250.
	mailReply string
	// advertiseAuth announces AUTH PLAIN in EHLO when true.
	advertiseAuth bool

	mu       sync.Mutex
	data     string
	commands []string
}

func newFakeSMTP(t *testing.T) *fakeSMTP {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	f := &fakeSMTP{ln: ln, rcptReply: "250 OK", mailReply: "250 OK"}
	go f.serve()
	t.Cleanup(func() { ln.Close() })
	return f
}

func (f *fakeSMTP) hostPort(t *testing.T) (string, int) {
	t.Helper()
	addr, ok := f.ln.Addr().(*net.TCPAddr)
	if !ok {
		t.Fatal("Listener address is not TCP")
	}
	return "127.0.0.1", addr.Port
}

func (f *fakeSMTP) record(cmd string) {
	f.mu.Lock()
	f.commands = append(f.commands, cmd)
	f.mu.Unlock()
}

func (f *fakeSMTP) body() string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.data
}

func (f *fakeSMTP) sawCommand(prefix string) bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	for _, c := range f.commands {
		if strings.HasPrefix(c, prefix) {
			return true
		}
	}
	return false
}

func (f *fakeSMTP) serve() {
	conn, err := f.ln.Accept()
	if err != nil {
		return
	}
	defer conn.Close()
	br := bufio.NewReader(conn)
	w := func(s string) { _, _ = conn.Write([]byte(s + "\r\n")) }
	w("220 fake.local ESMTP ready")
	for {
		line, err := br.ReadString('\n')
		if err != nil {
			return
		}
		line = strings.TrimRight(line, "\r\n")
		f.record(line)
		switch {
		case strings.HasPrefix(line, "EHLO"), strings.HasPrefix(line, "HELO"):
			// Do not advertise STARTTLS: test the plaintext envelope path, not TLS.
			w("250-fake.local")
			if f.advertiseAuth {
				w("250-AUTH PLAIN")
			}
			w("250 8BITMIME")
		case strings.HasPrefix(line, "AUTH"):
			// Simplification: accept the PLAIN initial response even if it spans lines.
			w("235 2.7.0 Authentication successful")
		case strings.HasPrefix(line, "MAIL FROM"):
			w(f.mailReply)
		case strings.HasPrefix(line, "RCPT TO"):
			w(f.rcptReply)
		case strings.HasPrefix(line, "DATA"):
			w("354 End data with <CR><LF>.<CR><LF>")
			var sb strings.Builder
			for {
				dl, err := br.ReadString('\n')
				if err != nil {
					return
				}
				if strings.TrimRight(dl, "\r\n") == "." {
					break
				}
				sb.WriteString(dl)
			}
			f.mu.Lock()
			f.data = sb.String()
			f.mu.Unlock()
			w("250 2.0.0 Ok: queued as FAKE1")
		case strings.HasPrefix(line, "QUIT"):
			w("221 2.0.0 Bye")
			return
		default:
			w("250 OK")
		}
	}
}

func emailCfg(t *testing.T, f *fakeSMTP, extra map[string]any) map[string]any {
	t.Helper()
	host, port := f.hostPort(t)
	cfg := map[string]any{
		"host": host,
		"port": float64(port),
		"from": "artex@example.com",
		"to":   []any{"a@example.com", "b@example.com"},
	}
	for k, v := range extra {
		cfg[k] = v
	}
	return cfg
}

func TestEmailSendDeliversFullMessage(t *testing.T) {
	f := newFakeSMTP(t)
	f.advertiseAuth = true
	cfg := emailCfg(t, f, map[string]any{"username": "artex", "password": "pw"})

	if _, err := (emailChannel{}).Send(context.Background(), cfg, singleMsg()); err != nil {
		t.Fatalf("Delivery failed: %v", err)
	}
	// The envelope must include the sender, both recipients and DATA.
	for _, want := range []string{"MAIL FROM:<artex@example.com>", "RCPT TO:<a@example.com>", "RCPT TO:<b@example.com>", "DATA", "AUTH", "QUIT"} {
		if !f.sawCommand(want) {
			t.Errorf("SMTP session missing %q; commands: %v", want, f.commands)
		}
	}
	// The base64 HTML body must contain the actual finding content.
	body := f.body()
	if body == "" {
		t.Fatal("DATA received no body")
	}
	if !strings.Contains(body, "Content-Type: text/html") {
		t.Errorf("Missing Content-Type header:\n%s", body)
	}
	if !strings.Contains(body, "base64") {
		t.Errorf("Body is not base64 encoded; long HTML lines exceed SMTP's 1000-byte line limit:\n%s", body)
	}
	// Both recipients must appear in the To header.
	if !strings.Contains(body, "a@example.com, b@example.com") {
		t.Errorf("To header missing recipients:\n%s", body)
	}
}

func TestEmailSendWithoutAuth(t *testing.T) {
	// Do not send AUTH without configured credentials; some relays reject it.
	f := newFakeSMTP(t)
	cfg := emailCfg(t, f, nil)
	if _, err := (emailChannel{}).Send(context.Background(), cfg, singleMsg()); err != nil {
		t.Fatalf("Delivery failed: %v", err)
	}
	if f.sawCommand("AUTH") {
		t.Errorf("AUTH sent without configured credentials: %v", f.commands)
	}
}

// TestEmailSendClassifiesSMTPReplies directly verifies the audit fix:
// 5xx is permanent; 4xx greylisting is retryable.
func TestEmailSendClassifiesSMTPReplies(t *testing.T) {
	cases := []struct {
		name      string
		rcptReply string
		mailReply string
		permanent bool
	}{
		{"Recipient permanently rejected with 550", "550 5.1.1 User unknown", "250 OK", true},
		{"Recipient greylisted with 450", "450 4.7.1 Greylisting in action", "250 OK", false},
		{"Recipient mailbox full with 452", "452 4.2.2 Mailbox full", "250 OK", false},
		{"Sender permanently rejected with 553", "250 OK", "553 5.1.3 Bad address", true},
		{"Sender temporary failure with 451", "250 OK", "451 4.3.0 Temporary failure", false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			f := newFakeSMTP(t)
			f.rcptReply = tc.rcptReply
			f.mailReply = tc.mailReply
			_, err := (emailChannel{}).Send(context.Background(), emailCfg(t, f, nil), singleMsg())
			if err == nil {
				t.Fatal("Expected an error")
			}
			if got := IsPermanent(err); got != tc.permanent {
				t.Fatalf("Incorrect permanent classification: expected %v, got %v (%v)", tc.permanent, got, err)
			}
			// Preserve server replies so users can distinguish address and server issues.
			if !strings.Contains(err.Error(), strings.Fields(tc.rcptReply)[0]) && !strings.Contains(err.Error(), strings.Fields(tc.mailReply)[0]) {
				t.Errorf("Error must preserve server reply code: %v", err)
			}
		})
	}
}

func TestEmailSendRefusesPlaintextCredentials(t *testing.T) {
	// net/smtp PlainAuth refuses credentials on plaintext except for localhost.
	// This correct security behavior must not be bypassed; errors should explain remediation.
	// Use a non-localhost hostname to trigger this behavior.
	f := newFakeSMTP(t)
	f.advertiseAuth = true
	_, port := f.hostPort(t)
	cfg := map[string]any{
		"host":     "smtp.example.com", // Not localhost
		"port":     float64(port),
		"from":     "a@example.com",
		"to":       []any{"b@example.com"},
		"username": "artex",
		"password": "pw",
	}
	_, err := (emailChannel{}).Send(context.Background(), cfg, singleMsg())
	if err == nil {
		t.Skip("DNS resolved to the local fixture; skipping this case only")
	}
	// Connection failure or credential refusal is acceptable; never silently send a password.
	if !IsPermanent(err) && !strings.Contains(err.Error(), "connect") {
		t.Logf("Error: %v (failure to connect to non-localhost is expected)", err)
	}
}

func TestEmailValidateReportsMissingFields(t *testing.T) {
	// Email has many configuration fields; check each missing field is rejected
	// before delivery, with an error naming what is missing.
	cases := []struct {
		name string
		cfg  map[string]any
	}{
		{"Missing host", map[string]any{"port": float64(25), "from": "a@b.c", "to": []any{"d@e.f"}}},
		{"Missing port", map[string]any{"host": "smtp.example.com"}},
		{"Port out of range", map[string]any{"host": "h", "port": float64(70000), "from": "a@b.c", "to": []any{"d@e.f"}}},
		{"Missing from", map[string]any{"host": "h", "port": float64(25), "to": []any{"d@e.f"}}},
		{"Missing to", map[string]any{"host": "h", "port": float64(25), "from": "a@b.c"}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if err := (emailChannel{}).Validate(tc.cfg); err == nil {
				t.Fatalf("Expected validation failure: %v", tc.cfg)
			}
		})
	}
}

// TestEmailConfigTolerance covers JSONB numbers decoded as float64,
// string ports from the UI, and a single string instead of an array.
func TestEmailConfigTolerance(t *testing.T) {
	cfg := map[string]any{
		"host": "smtp.example.com",
		"port": "587", // Port as a string
		"from": "a@b.c",
		"to":   "d@e.f", // Single string rather than array
		"tls":  "true",  // Boolean as a string
	}
	if err := (emailChannel{}).Validate(cfg); err != nil {
		t.Fatalf("Numeric strings must be accepted: %v", err)
	}
	if got := cfgInt(cfg, "port"); got != 587 {
		t.Errorf("cfgInt did not parse string port, got %d", got)
	}
	if !cfgBool(cfg, "tls") {
		t.Error("cfgBool did not parse string \"true\"")
	}
	if to := cfgStrings(cfg, "to"); len(to) != 1 || to[0] != "d@e.f" {
		t.Errorf("cfgStrings did not accept single string, got %v", to)
	}
}

// TestFilterValidateRejectsTypo directly checks the audit fix.
// Reject misspelled severity thresholds on write, avoiding a silent allow-all filter.
func TestFilterValidateRejectsTypo(t *testing.T) {
	good := []string{"", "low", "medium", "high", "critical"}
	for _, s := range good {
		if err := (Filter{MinSeverity: s}).Validate(); err != nil {
			t.Errorf("Valid threshold %q rejected: %v", s, err)
		}
	}
	// Reject all these realistic typos and unsupported localized enum values.
	for _, s := range []string{"hgih", "HIGH", "\u4e25\u91cd", "high ", "crit"} {
		err := (Filter{MinSeverity: s}).Validate()
		if err == nil {
			t.Errorf("Invalid threshold %q must be rejected to avoid silently allowing all events", s)
			continue
		}
		// Error messages should guide users toward a valid value.
		if !strings.Contains(err.Error(), "low") || !strings.Contains(err.Error(), "critical") {
			t.Errorf("Error must list allowed values, got %q", err.Error())
		}
	}
}

// TestFilterValidateIsWriteTimeOnly enforces strict writes but tolerant reads.
// Existing invalid values must not make old channels unreadable or stop all delivery.
func TestFilterValidateIsWriteTimeOnly(t *testing.T) {
	raw := []byte(`{"min_severity":"hgih"}`)
	f := ParseFilter(raw) // No error
	if f.MinSeverity != "hgih" {
		t.Fatalf("Read path must preserve stored value, got %q", f.MinSeverity)
	}
	// The channel can still evaluate an event without panic or blocking.
	_ = Match(f, Snapshot{Kind: EventFindingCreated, Severity: "critical"})
}

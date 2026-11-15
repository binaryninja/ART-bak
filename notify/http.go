package notify

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"strings"
	"syscall"
	"time"
)

// allowLocalTargets controls delivery to loopback/link-local addresses.
//
// Deny by default: public IM bots and mail servers do not use these ranges, which can expose local admin ports and cloud metadata (169.254.169.254, including instance credentials). Although administrators configure destinations, an XSS/CSRF-hijacked admin session or another user sharing the JWT could change the address and read responses: doJSON records the first 200 bytes of 4xx/5xx responses in last_error, and delivery history exposes them, creating a semi-blind read primitive.
//
// Local SMTP relays such as postfix at 127.0.0.1:25 are legitimate self-hosted configurations, so provide an explicit opt-in instead of a blanket exception: ARTEX_NOTIFY_ALLOW_LOCAL=1.
//
// AllowLocalTargetsEnv is exported so tests can explicitly enable it. Many tests here and in server use httptest receivers on 127.0.0.1, otherwise blocked by the guard.
const AllowLocalTargetsEnv = "ARTEX_NOTIFY_ALLOW_LOCAL"

func allowLocalTargets() bool {
	v := strings.TrimSpace(os.Getenv(AllowLocalTargetsEnv))
	return v == "1" || strings.EqualFold(v, "true")
}

// isBlockedDialIP reports whether an IP is in a range denied for delivery by default.
//
// Reject only loopback, link-local (including metadata 169.254.169.254), unspecified, and multicast addresses. Deliberately do not reject RFC1918 private networks: internal Mattermost/SMTP relays are legitimate common deployments. Protect sensitive destinations without disabling normal deployments.
func isBlockedDialIP(ip net.IP) bool {
	if ip == nil {
		return true
	}
	// Normalize IPv4-mapped IPv6 (::ffff:127.0.0.1) to IPv4 before checking to prevent bypass.
	if v4 := ip.To4(); v4 != nil {
		ip = v4
	}
	return ip.IsLoopback() || ip.IsLinkLocalUnicast() || ip.IsLinkLocalMulticast() ||
		ip.IsInterfaceLocalMulticast() || ip.IsUnspecified() || ip.IsMulticast()
}

// blockInternalDial is the http.Transport dialer Control hook; it checks the target when the connection is established.
//
// Dial-time checking is the final enforcement point, unlike configuration-time validation. It covers DNS rebinding (public IP during validation, internal IP at connection) and redirects (cross-host redirects are denied, but same-host redirects can still change paths).
func blockInternalDial(_, address string, _ syscall.RawConn) error {
	if allowLocalTargets() {
		return nil
	}
	host, _, err := net.SplitHostPort(address)
	if err != nil {
		return err
	}
	ip := net.ParseIP(host)
	if ip == nil {
		return fmt.Errorf("cannot resolve destination address %q", host)
	}
	if isBlockedDialIP(ip) {
		return fmt.Errorf("delivery to loopback/link-local address %s denied (set %s=1 if delivery to a local service is required)", ip, AllowLocalTargetsEnv)
	}
	return nil
}

// notifyTransport adds only a dial guard to the default Transport. Clone retains connection pooling, HTTP/2, timeouts, proxies, and other tuning, avoiding unrelated behavior changes.
var notifyTransport = func() *http.Transport {
	t, ok := http.DefaultTransport.(*http.Transport)
	if !ok {
		return &http.Transport{}
	}
	clone := t.Clone()
	clone.DialContext = (&net.Dialer{Timeout: 10 * time.Second, Control: blockInternalDial}).DialContext
	return clone
}()

// httpClient is shared by all delivery channels.
//
// Do not reuse the global outbound proxy (server GlobalProxy): it handles penetration-test traffic, often through unstable tunnels. Notification availability should not depend on target-network instability. IM delivery connects directly, with a 15-second timeout; slower peers are effectively unhealthy.
//
// Reject cross-host redirects. Destinations are fixed endpoints and normally do not need them; credentials such as DingTalk access_token, WeCom key, and Telegram bot token are in URLs, so following redirects would disclose them. Same-host redirects, such as adding a trailing slash, remain allowed.
var httpClient = &http.Client{
	Timeout:   15 * time.Second,
	Transport: notifyTransport,
	CheckRedirect: func(req *http.Request, via []*http.Request) error {
		if len(via) >= 5 {
			return errors.New("too many redirects")
		}
		if len(via) > 0 && req.URL.Host != via[0].URL.Host {
			return fmt.Errorf("cross-host redirect denied (%s → %s)", via[0].URL.Host, req.URL.Host)
		}
		return nil
	},
}

// respBodyLimit bounds response reads. Unhealthy peers can return huge bodies; delivery history needs only an error code and short description.
const respBodyLimit = 8 << 10

// doJSON sends a request and returns its size-limited response body.
//
// A nil payload sends an empty body for GET or platforms requiring none. Append headers verbatim for generic webhook custom headers.
//
// Classifying errors is central: network failures and 5xx/408/429 are retryable; other 4xx failures are permanent. Retrying 403 would only log the same error three times.
func doJSON(ctx context.Context, method, url string, headers map[string]string, payload any) ([]byte, error) {
	var body io.Reader
	if payload != nil {
		raw, err := json.Marshal(payload)
		if err != nil {
			// Serialization failure is a local bug, such as an invalid configuration field type; retries will not help.
			return nil, Permanent(fmt.Errorf("failed to build request body: %w", err))
		}
		body = bytes.NewReader(raw)
	}
	req, err := http.NewRequestWithContext(ctx, method, url, body)
	if err != nil {
		// Invalid URL, usually a configuration typo, is permanent. Do not expose err: url.Parse errors contain the complete address.
		return nil, Permanent(fmt.Errorf("invalid request URL: %s", redactRequestTarget(url)))
	}
	if payload != nil {
		req.Header.Set("Content-Type", "application/json; charset=utf-8")
	}
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	resp, err := httpClient.Do(req)
	if err != nil {
		// Connection refusal, DNS errors, and timeouts are usually transient; use backoff retries.
		//
		// Redact errors before returning them. http.Client.Do returns *url.Error, whose Error() includes the complete URL and underlying error. Credentials live in URLs: DingTalk access_token, WeCom key, Feishu hook ID, and Telegram /bot<token>/. Without redaction they leak into notification_deliveries.last_error in plaintext, delivery-history responses bypassing configuration masking, server logs, and the test-send endpoint's frontend 502 response.
		return nil, fmt.Errorf("request failed: %s", redactTransportError(err))
	}
	defer resp.Body.Close()
	raw, readErr := io.ReadAll(io.LimitReader(resp.Body, respBodyLimit))
	if readErr != nil {
		return nil, fmt.Errorf("failed to read response: %w", readErr)
	}
	if resp.StatusCode >= 200 && resp.StatusCode < 300 {
		return raw, nil
	}
	// 429 (rate limiting) and 408 (timeout) warrant retries; other 4xx errors indicate configuration or permissions.
	if resp.StatusCode == http.StatusTooManyRequests || resp.StatusCode == http.StatusRequestTimeout {
		return nil, fmt.Errorf("remote rate limit or timeout (HTTP %d): %s", resp.StatusCode, snippet(raw))
	}
	if resp.StatusCode >= 500 {
		return nil, fmt.Errorf("remote service error (HTTP %d): %s", resp.StatusCode, snippet(raw))
	}
	return nil, Permanent(fmt.Errorf("remote request rejected (HTTP %d): %s", resp.StatusCode, snippet(raw)))
}

// snippet reduces a response to a short single line. Raw newlines and whitespace in last_error would disrupt delivery-history layout.
func snippet(raw []byte) string {
	return OneLine(string(raw), 200)
}

// redactRequestTarget reduces a destination to scheme://host/... for error messages.
//
// This is the sole address-redaction policy, deliberately discarding everything except scheme and host. No generic safe rule identifies credential segments:
//
//	DingTalk: query /robot/send?access_token=xxx
//	WeCom: query /cgi-bin/webhook/send?key=xxx
//	Feishu: final path segment /open-apis/bot/v2/hook/<hook_id>
//	Telegram: middle path segment /bot<token>/sendMessage
//
// Keeping other useful parts requires per-channel exceptions, and missing one leaks credentials. Host alone suffices for DNS, connectivity, and certificate diagnosis; masked suffixes in configuration identify the bot. Return a fixed placeholder on parse failure, never the raw string.
func redactRequestTarget(raw string) string {
	u, err := url.Parse(raw)
	if err != nil || u.Host == "" {
		return "(unparseable address)"
	}
	return u.Scheme + "://" + u.Host + "/…"
}

// redactTransportError removes addresses from transport errors, retaining the underlying cause.
//
// *url.Error contains {Op, URL, Err}; Error() includes URL. Read Err directly rather than replacing text afterward, which can miss encoded/escaped URL variants.
func redactTransportError(err error) string {
	var uerr *url.Error
	if errors.As(err, &uerr) {
		host := ""
		if u, parseErr := url.Parse(uerr.URL); parseErr == nil {
			host = u.Host
		}
		if uerr.Err != nil {
			return fmt.Sprintf("%s %s: %s", uerr.Op, host, uerr.Err)
		}
		return fmt.Sprintf("%s %s: unknown error", uerr.Op, host)
	}
	// Non-*url.Error errors, including redirect-policy errors, may also contain addresses; redact them consistently.
	return redactURLsInText(err.Error())
}

// redactURLsInText replaces HTTP(S) addresses in text with redacted forms. This handles errors without structured fields, such as redirect-policy or third-party-library errors. Recognize only http/https prefixes and delimit with whitespace/quotes, which addresses cannot contain.
func redactURLsInText(s string) string {
	var b strings.Builder
	for i := 0; i < len(s); {
		rest := s[i:]
		if strings.HasPrefix(rest, "http://") || strings.HasPrefix(rest, "https://") {
			end := len(rest)
			if j := strings.IndexAny(rest, " \t\n\"'"); j >= 0 {
				end = j
			}
			b.WriteString(redactRequestTarget(rest[:end]))
			i += end
			continue
		}
		b.WriteByte(s[i])
		i++
	}
	return b.String()
}

package notify

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/url"
	"strconv"
	"time"
)

// dingTalkChannel implements DingTalk custom bots.
//
// Platform properties shaping this implementation:
// -20 messages/minute per bot; excess may be silently dropped even with HTTP200,
//
//	so DefaultRatePerMin enforces client-side limits.
//
// -Security options are signature, custom keyword or IP allowlist. Only signing
//
//	is independent of message content, so support it (or an unsecured Webhook).
//
// -Success and failure both use HTTP200; inspect errcode or failed sends
//
//	would be recorded as successful.
type dingTalkChannel struct{}

func (dingTalkChannel) Kind() string { return KindDingTalk }

func (dingTalkChannel) DefaultRatePerMin() int { return 20 }

// DingTalk Webhook URLs contain access_token, so mask the entire credential URL.
func (dingTalkChannel) SecretKeys() []string { return []string{"webhook", "secret"} }

// Changing the Webhook destination requires reaffirming its signing secret.
func (dingTalkChannel) DestinationKeys() []string { return []string{"webhook"} }

func (dingTalkChannel) Validate(cfg map[string]any) error {
	hook := cfgString(cfg, "webhook")
	if hook == "" {
		return errors.New("Missing Webhook URL")
	}
	if err := validateHTTPURL(hook); err != nil {
		return fmt.Errorf("Invalid Webhook URL: %w", err)
	}
	return nil
}

// Send uses ActionCard with button for one item with detail URL; otherwise Markdown.
func (c dingTalkChannel) Send(ctx context.Context, cfg map[string]any, m Message) (int, error) {
	hook := cfgString(cfg, "webhook")
	if err := c.Validate(cfg); err != nil {
		return 0, Permanent(err)
	}
	endpoint, err := dingTalkSignedURL(hook, cfgString(cfg, "secret"), time.Now())
	if err != nil {
		return 0, Permanent(err)
	}

	title := markdownTitle(m)
	// Although no explicit Markdown byte limit is given, cap abnormal evidence growth.
	text, kept := markdownBody(m, 20000)

	var payload any
	if !m.Batch && len(m.Items) == 1 && m.Items[0].DetailURL != "" {
		payload = map[string]any{
			"msgtype": "actionCard",
			"actionCard": map[string]any{
				"title":          title,
				"text":           text,
				"btnOrientation": "0",
				"singleTitle":    "View details",
				"singleURL":      m.Items[0].DetailURL,
			},
		}
	} else {
		payload = map[string]any{
			"msgtype":  "markdown",
			"markdown": map[string]any{"title": title, "text": text},
		}
	}

	raw, err := doJSON(ctx, "POST", endpoint, nil, payload)
	if err != nil {
		return 0, err
	}
	// DingTalk embeds business errors in HTTP200 responses.
	var res struct {
		ErrCode int    `json:"errcode"`
		ErrMsg  string `json:"errmsg"`
	}
	if err := json.Unmarshal(raw, &res); err != nil {
		return 0, fmt.Errorf("Failed to parse DingTalk response: %w (%s)", err, snippet(raw))
	}
	if res.ErrCode != 0 {
		// 301000 is signature failure;310000 is keyword mismatch. Both are
		// configuration failures, not recoverable by retry.
		return 0, Permanent(fmt.Errorf("DingTalk returned error %d: %s", res.ErrCode, res.ErrMsg))
	}
	return kept, nil
}

// dingTalkSignedURL appends timestamp and sign under official signing rules.
//
// Sign timestamp + "\n" + secret with HMAC-SHA256 using secret as key,
// then base64 and URL encode. Timestamp is milliseconds. Empty secret leaves
// the URL unchanged for bots without signing enabled.
func dingTalkSignedURL(hook, secret string, now time.Time) (string, error) {
	if secret == "" {
		return hook, nil
	}
	ts := strconv.FormatInt(now.UnixMilli(), 10)
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(ts + "\n" + secret))
	sign := base64.StdEncoding.EncodeToString(mac.Sum(nil))

	u, err := url.Parse(hook)
	if err != nil {
		// Do not expose err: url.Parse includes the complete URL and access_token.
		return "", fmt.Errorf("Failed to parse Webhook URL: %s", redactRequestTarget(hook))
	}
	q := u.Query()
	q.Set("timestamp", ts)
	q.Set("sign", sign)
	u.RawQuery = q.Encode()
	return u.String(), nil
}

// validateHTTPURL checks supported schemes and local-address restrictions on literal IPs.
//
// Two important safeguards:
//
// 1. Redact errors: url.Parse returns *url.Error containing the complete
// original URL, which embeds access_token, WeCom key, Telegram token or
// Feishu hook ID. Returning err directly previously leaked credentials
// through400 test responses, persisted last_error, server logs and
// delivery history.
//
// 2. Check literal IPs immediately; DNS names are checked at dial time by
// blockInternalDial, which also covers DNS rebinding. Early validation gives
// configuration feedback before the first failed delivery.
//
// Restrict schemes defensively: file:///gopher:// would cause unexpected
// http.Client behavior; there is no reason to expand that surface.
func validateHTTPURL(raw string) error {
	u, err := url.Parse(raw)
	if err != nil {
		return fmt.Errorf("Unable to parse URL (%s)", redactRequestTarget(raw))
	}
	if u.Scheme != "http" && u.Scheme != "https" {
		return fmt.Errorf("Only http/https supported, got %q", u.Scheme)
	}
	if u.Host == "" {
		return errors.New("Missing hostname")
	}
	if ip := net.ParseIP(u.Hostname()); ip != nil && isBlockedDialIP(ip) && !allowLocalTargets() {
		return fmt.Errorf("Delivery to loopback/link-local address %s refused (set %s=1 if local delivery is required)", ip, AllowLocalTargetsEnv)
	}
	return nil
}

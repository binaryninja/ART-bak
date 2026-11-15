package notify

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
)

// weComMarkdownLimit is the hard markdown-content byte limit, not characters.
// It is the tightest of the six channels and the main reason for TruncateBytes.
const weComMarkdownLimit = 4096

// weComChannel implements WeCom group bots.
//
// Platform properties:
//   - Authentication is the URL key alone, with no signature; the URL is the credential.
//   - markdown content exceeding 4096 bytes is rejected, not truncated. CJK characters
//     use three bytes, so client-side truncation is necessary.
//   - Rate limit is 20/minute, enforced by the client.
type weComChannel struct{}

func (weComChannel) Kind() string { return KindWeCom }

func (weComChannel) DefaultRatePerMin() int { return 20 }

// WeCom has one credential, the Webhook URL key, and no signing secret;
// mask the entire URL, with no other credential fields.
func (weComChannel) SecretKeys() []string { return []string{"webhook"} }

// Webhook is both destination and credential; changing it cannot leave a separate old credential.
func (weComChannel) DestinationKeys() []string { return []string{"webhook"} }

func (weComChannel) Validate(cfg map[string]any) error {
	hook := cfgString(cfg, "webhook")
	if hook == "" {
		return errors.New("Missing Webhook URL")
	}
	if err := validateHTTPURL(hook); err != nil {
		return fmt.Errorf("Invalid Webhook URL: %w", err)
	}
	return nil
}

func (c weComChannel) Send(ctx context.Context, cfg map[string]any, m Message) (int, error) {
	if err := c.Validate(cfg); err != nil {
		return 0, Permanent(err)
	}
	// A digest of 50 lines plus prefix easily exceeds 4096 bytes. Truncate here
	// rather than lose the entire rejected batch; at least included items are delivered.
	content, kept := markdownBody(m, weComMarkdownLimit)
	payload := map[string]any{
		"msgtype":  "markdown",
		"markdown": map[string]any{"content": content},
	}
	raw, err := doJSON(ctx, "POST", cfgString(cfg, "webhook"), nil, payload)
	if err != nil {
		return 0, err
	}
	var res struct {
		ErrCode int    `json:"errcode"`
		ErrMsg  string `json:"errmsg"`
	}
	if err := json.Unmarshal(raw, &res); err != nil {
		return 0, fmt.Errorf("Failed to parse WeCom response: %w (%s)", err, snippet(raw))
	}
	if res.ErrCode != 0 {
		// 45009 indicates rate limiting; the platform window advances, so backoff may
		// recover. Classify it as retryable, but excessive rate_per_min should be lowered;
		// retry is only a fallback.
		if res.ErrCode == 45009 {
			return 0, fmt.Errorf("WeCom rate limited %d: %s", res.ErrCode, res.ErrMsg)
		}
		// 93000 is an invalid Webhook key: permanent, not recoverable by retry.
		return 0, Permanent(fmt.Errorf("WeCom returned error %d: %s", res.ErrCode, res.ErrMsg))
	}
	return kept, nil
}

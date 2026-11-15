package notify

import (
	"context"
	"errors"
	"sort"
	"strconv"
	"strings"
)

// Channel adapts a notification destination. Implementations must be stateless: multiple channel configurations reuse the same instance concurrently, with credentials supplied through cfg.
type Channel interface {
	// Kind returns the channel identifier, matching its registry key.
	Kind() string
	// Validate checks required fields and formats on save. Errors are shown directly to the operator, so name missing fields rather than saying only "invalid configuration".
	Validate(cfg map[string]any) error
	// Send delivers once and returns the actual number of delivered items plus an error. Platform length limits can truncate digests; marking the whole batch delivered would silently lose omitted findings while history reported success. The caller marks only the first kept items and leaves the rest for the next batch.
	//
	// An error means delivery failed; *PermanentError means do not retry. Ignore kept on failure.
	Send(ctx context.Context, cfg map[string]any, m Message) (int, error)
	// DefaultRatePerMin returns the official recommended per-minute delivery limit as the default for new channels; 0 means no known limit.
	DefaultRatePerMin() int
	// SecretKeys identifies credential fields, masked in API responses and retained when updates return masked values. Only the adapter knows what is secret (the whole WeCom webhook URL versus DingTalk's secret), so higher layers must not guess.
	SecretKeys() []string
	// DestinationKeys identifies fields controlling where messages go. Like SecretKeys, this is security-sensitive: changing destinations while retaining stored credentials would let any configuration editor send secrets to their own server, defeating masking. See PrepareConfigUpdate.
	DestinationKeys() []string
}

// registry is explicit rather than init()-driven registration, showing all channels in one place and exposing omissions at compile time instead of depending on runtime side effects.
var registry = map[string]Channel{
	KindDingTalk: dingTalkChannel{},
	KindFeishu:   feishuChannel{},
	KindWeCom:    weComChannel{},
	KindWebhook:  webhookChannel{},
	KindTelegram: telegramChannel{},
	KindEmail:    emailChannel{},
}

// Get returns the implementation for a channel kind.
func Get(kind string) (Channel, bool) {
	c, ok := registry[kind]
	return c, ok
}

// ValidKind reports whether a channel kind is supported.
func ValidKind(kind string) bool {
	_, ok := registry[kind]
	return ok
}

// Kinds returns supported kinds in lexical order for stable UI dropdowns.
func Kinds() []string {
	out := make([]string, 0, len(registry))
	for k := range registry {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// PermanentError marks non-retryable failures such as invalid credentials, rejected destinations, or invalid bodies. Only transient network/rate-limit/5xx failures warrant retries; repeating permanent failures cannot succeed and obscures the real cause in retry logs.
type PermanentError struct{ Err error }

func (e *PermanentError) Error() string { return e.Err.Error() }
func (e *PermanentError) Unwrap() error { return e.Err }

// Permanent marks err as permanent, returning nil for nil input so callers can write return Permanent(someCheck()).
func Permanent(err error) error {
	if err == nil {
		return nil
	}
	return &PermanentError{Err: err}
}

// IsPermanent checks the error chain for a permanent-failure marker.
func IsPermanent(err error) bool {
	var pe *PermanentError
	return errors.As(err, &pe)
}

// Configuration-reading helpers.
//
// JSONB configuration decoded by encoding/json becomes map[string]any, with float64 numbers and []any arrays. Helpers normalize these types and tolerate UI-induced differences, such as string-valued ports or blank fields.

// cfgString reads a string and trims whitespace often introduced by copy/paste from web forms.
func cfgString(cfg map[string]any, key string) string {
	v, ok := cfg[key]
	if !ok {
		return ""
	}
	s, ok := v.(string)
	if !ok {
		return ""
	}
	return strings.TrimSpace(s)
}

// cfgInt reads an integer from JSON-default float64 or string values.
func cfgInt(cfg map[string]any, key string) int {
	switch v := cfg[key].(type) {
	case float64:
		return int(v)
	case int:
		return v
	case string:
		n, err := strconv.Atoi(strings.TrimSpace(v))
		if err != nil {
			return 0
		}
		return n
	default:
		return 0
	}
}

// cfgBool reads a boolean, also accepting strings "true" and "1".
func cfgBool(cfg map[string]any, key string) bool {
	switch v := cfg[key].(type) {
	case bool:
		return v
	case string:
		s := strings.ToLower(strings.TrimSpace(v))
		return s == "true" || s == "1" || s == "yes"
	default:
		return false
	}
}

// cfgStrings reads a string array, trims whitespace, and discards empty entries.
func cfgStrings(cfg map[string]any, key string) []string {
	raw, ok := cfg[key].([]any)
	if !ok {
		// Also accept a single string for convenient one-value form submission.
		if s := cfgString(cfg, key); s != "" {
			return []string{s}
		}
		return nil
	}
	out := make([]string, 0, len(raw))
	for _, v := range raw {
		s, ok := v.(string)
		if !ok {
			continue
		}
		if s = strings.TrimSpace(s); s != "" {
			out = append(out, s)
		}
	}
	return out
}

// cfgMap reads a string map, such as HTTP headers; trim keys/values and discard empty keys.
func cfgMap(cfg map[string]any, key string) map[string]string {
	raw, ok := cfg[key].(map[string]any)
	if !ok {
		return nil
	}
	out := make(map[string]string, len(raw))
	for k, v := range raw {
		k = strings.TrimSpace(k)
		if k == "" {
			continue
		}
		s, ok := v.(string)
		if !ok {
			continue
		}
		out[k] = s
	}
	return out
}

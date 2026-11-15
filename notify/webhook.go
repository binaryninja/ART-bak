package notify

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"text/template"
	"time"
)

// webhookChannel provides configurable URL, method, headers and JSON template.
// This single adapter can cover Slack, Mattermost, Discord and custom systems
// without a separate implementation for each.
type webhookChannel struct{}

func (webhookChannel) Kind() string { return KindWebhook }

// No universal Webhook rate limit:0 means unlimited; users choose the receiver limit.
func (webhookChannel) DefaultRatePerMin() int { return 0 }

// Mask url and headers: URLs often contain tokens and custom headers carry
// authentication, and both otherwise appear in API output. Editing a header
// requires resupplying the whole set because masked values mean keep stored.
// This deliberate inconvenience avoids returning credentials to browsers.
func (webhookChannel) SecretKeys() []string { return []string{"url", "headers"} }

// Changing destination url requires reaffirming headers; otherwise existing
// Authorization would be forwarded to the new destination, bypassing masking.
func (webhookChannel) DestinationKeys() []string { return []string{"url"} }

// webhookDefaultTemplate is a simple JSON fallback for receivers that store
// one incoming JSON object when no explicit template is supplied.
const webhookDefaultTemplate = `{
  "title": {{json .Title}},
  "batch": {{.Batch}},
  "count": {{.Count}},
  "items": [
{{- range $i, $it := .Items}}
{{- if $i}},{{end}}
    {
      "finding_id": {{$it.FindingID}},
      "name": {{json $it.Name}},
      "vulnclass": {{json $it.VulnClass}},
      "severity": {{json $it.Severity}},
      "summary": {{json $it.Summary}},
      "assets": {{json $it.Assets}},
      "detail_url": {{json $it.DetailURL}}
    }
{{- end}}
  ]
}`

// webhookTemplateData is the user-template context.
type webhookTemplateData struct {
	Title   string
	Batch   bool
	Count   int
	Items   []webhookItem
	HomeURL string
	// SentAt is the delivery timestamp (RFC3339) for receiver records.
	SentAt string
}

type webhookItem struct {
	FindingID     int64
	Name          string
	VulnClass     string
	Severity      string
	SeverityLabel string
	Summary       string
	Assets        []string
	DetailURL     string
	FromStatus    string
	ToStatus      string
	// StatusLabel is a readable transition (Pending -> Fixed), empty for other events.
	StatusLabel string
}

func (webhookChannel) Validate(cfg map[string]any) error {
	raw := cfgString(cfg, "url")
	if raw == "" {
		return errors.New("Missing destination URL")
	}
	if err := validateHTTPURL(raw); err != nil {
		return fmt.Errorf("Invalid destination URL: %w", err)
	}
	if m := strings.ToUpper(cfgString(cfg, "method")); m != "" && m != http.MethodGet && m != http.MethodPost && m != http.MethodPut && m != http.MethodPatch {
		return fmt.Errorf("Unsupported method %s (use GET/POST/PUT/PATCH)", m)
	}
	if tpl := cfgString(cfg, "body_template"); tpl != "" {
		if _, err := parseWebhookTemplate(tpl); err != nil {
			return fmt.Errorf("Request body template syntax error: %w", err)
		}
	}
	return nil
}

func (c webhookChannel) Send(ctx context.Context, cfg map[string]any, m Message) (int, error) {
	if err := c.Validate(cfg); err != nil {
		return 0, Permanent(err)
	}
	method := strings.ToUpper(cfgString(cfg, "method"))
	if method == "" {
		method = http.MethodPost
	}

	// GET has no body: moving it to query parameters exceeds template capability
	// and GET semantics. GET suits receivers triggered by simply hitting the hook.
	var payload any
	if method != http.MethodGet {
		body, err := renderWebhookBody(cfgString(cfg, "body_template"), m)
		if err != nil {
			return 0, Permanent(err)
		}
		// Render JSON text as json.RawMessage so it is sent unchanged, not escaped
		// again into a JSON string that changes the intended request structure.
		if !json.Valid([]byte(body)) {
			return 0, Permanent(errors.New("Rendered request body template is not valid JSON"))
		}
		payload = json.RawMessage(body)
	}

	headers := cfgMap(cfg, "headers")
	if ct := cfgString(cfg, "content_type"); ct != "" {
		// Apply after custom headers so explicit configuration takes precedence.
		if headers == nil {
			headers = map[string]string{}
		}
		headers["Content-Type"] = ct
	}
	if _, err := doJSON(ctx, method, cfgString(cfg, "url"), headers, payload); err != nil {
		return 0, err
	}
	// Generic Webhooks do not truncate: the user controls the receiver and body
	// template size, so the entire batch counts as delivered.
	return len(m.Items), nil
}

// renderWebhookBody uses the supplied template or default to render a body.
func renderWebhookBody(tpl string, m Message) (string, error) {
	if strings.TrimSpace(tpl) == "" {
		tpl = webhookDefaultTemplate
	}
	t, err := parseWebhookTemplate(tpl)
	if err != nil {
		return "", fmt.Errorf("Request body template syntax error: %w", err)
	}
	var buf bytes.Buffer
	if err := t.Execute(&buf, newWebhookTemplateData(m)); err != nil {
		return "", fmt.Errorf("Failed to render request body template: %w", err)
	}
	return buf.String(), nil
}

// parseWebhookTemplate parses the template.
//
// missingkey=zero supplies zero values for absent map keys; our context is a
// struct, and ranges over empty Items must remain safe, particularly nil Items.
func parseWebhookTemplate(tpl string) (*template.Template, error) {
	return template.New("body").Funcs(webhookTemplateFuncs).Option("missingkey=zero").Parse(tpl)
}

// webhookTemplateFuncs exposes the template helper functions.
var webhookTemplateFuncs = template.FuncMap{
	// json serializes an arbitrary value as JSON.
	//
	// This helper is essential: direct {{.Title}} interpolation would let quotes
	// or newlines in finding titles invalidate the whole JSON body. Receiver JSON
	// errors would misleadingly obscure that the cause was a quote in a title.
	"json": func(v any) (string, error) {
		raw, err := json.Marshal(v)
		if err != nil {
			return "", err
		}
		return string(raw), nil
	},
	// jsons embeds a JSON fragment inside another JSON string with one escaping layer.
	"jsons": func(v any) (string, error) {
		raw, err := json.Marshal(v)
		if err != nil {
			return "", err
		}
		quoted, err := json.Marshal(string(raw))
		if err != nil {
			return "", err
		}
		// Remove outer quotes; the caller decides whether to add them.
		return string(quoted[1 : len(quoted)-1]), nil
	},
}

func newWebhookTemplateData(m Message) webhookTemplateData {
	d := webhookTemplateData{
		Title:   markdownTitle(m),
		Batch:   m.Batch,
		Count:   len(m.Items),
		HomeURL: m.HomeURL,
		SentAt:  time.Now().Format(time.RFC3339),
		Items:   make([]webhookItem, 0, len(m.Items)),
	}
	for _, it := range m.Items {
		wi := webhookItem{
			FindingID:     it.FindingID,
			Name:          it.Name,
			VulnClass:     it.VulnClass,
			Severity:      it.Severity,
			SeverityLabel: SeverityLabel(it.Severity),
			Summary:       it.Summary,
			Assets:        append([]string{}, it.Assets...),
			DetailURL:     it.DetailURL,
			FromStatus:    it.FromStatus,
			ToStatus:      it.ToStatus,
		}
		if it.IsStatusChange() {
			wi.StatusLabel = StatusLabel(it.FromStatus) + " → " + StatusLabel(it.ToStatus)
		}
		d.Items = append(d.Items, wi)
	}
	return d
}

package notify

import (
	"context"
	"reflect"
	"sort"
	"strings"
	"testing"
)

// This file fixes the capability boundary of generic Webhook templates.
//
// This is the only place where user-provided strings are evaluated as code. Explicitly document and test
// what templates can and cannot do; otherwise adding a method to the template context
// or readFile to FuncMap could silently expand capabilities while appearing in the diff
// as just a harmless helper function.

// TestTemplateContextHasNoMethods is the central assertion.
//
// text/template calls exported methods ({{.Foo}} can read a field or call a method). Any type
// with exported methods reachable from the context exposes those methods to template authors.
// The context deliberately contains only data: exported fields and no methods.
//
// Failure means a method was added to webhookTemplateData or webhookItem.
// Before allowing it, determine whether templates could use it to read data that should remain private.
func TestTemplateContextHasNoMethods(t *testing.T) {
	for _, v := range []any{webhookTemplateData{}, webhookItem{}} {
		typ := reflect.TypeOf(v)
		if n := typ.NumMethod(); n != 0 {
			var names []string
			for i := 0; i < n; i++ {
				names = append(names, typ.Method(i).Name)
			}
			t.Fatalf("%s exposes %d methods (%s): text/template can call them,"+
				" exposing their capabilities to template authors", typ.Name(), n, strings.Join(names, ", "))
		}
	}
}

// TestTemplateFuncsAreMinimal fixes the set of functions exposed to templates.
//
// Each additional FuncMap entry grants a capability. Currently only json/jsons serialize values
// to JSON fragments; they cannot read files, send requests, or execute commands.
func TestTemplateFuncsAreMinimal(t *testing.T) {
	var got []string
	for name := range webhookTemplateFuncs {
		got = append(got, name)
	}
	sort.Strings(got)
	want := []string{"json", "jsons"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("Template function set changed: got %v, want %v. Ensure new functions do not expand capabilities"+
			" (no file access, network requests, or command execution)", got, want)
	}
}

// TestTemplateCannotReachUnknownData covers out-of-bounds template access:
// access to nonexistent data must fail, not echo content; errors must not expose internal data.
func TestTemplateCannotReachUnknownData(t *testing.T) {
	_, err := renderWebhookBody(`{"x": {{.Environment}}, "y": {{.Env}}}`, singleMsg())
	if err == nil {
		t.Fatal("Accessing a nonexistent field should return an error")
	}
	// Errors must not contain real context content (finding titles/summaries).
	for _, leak := range []string{"SQL injection", "Parameter id"} {
		if strings.Contains(err.Error(), leak) {
			t.Errorf("Template error leaked message content %q: %v", leak, err)
		}
	}
}

// TestTemplateRenderFailsPermanently: malformed templates are configuration errors that retries cannot fix.
// Classifying them as retryable wastes three backoff rounds on every delivery.
func TestTemplateRenderFailsPermanently(t *testing.T) {
	cfg := map[string]any{
		"url":           "https://example.com/hook",
		"body_template": `{{.Items.`,
	}
	if err := (webhookChannel{}).Validate(cfg); err == nil {
		t.Fatal("Template syntax errors should be rejected on save")
	}
	// Even delivery bypassing validation must fail permanently rather than retrying repeatedly.
	_, err := (webhookChannel{}).Send(context.Background(), cfg, singleMsg())
	if err == nil || !IsPermanent(err) {
		t.Fatalf("Malformed template should fail permanently, got %v", err)
	}
}

// TestTemplateCanOnlyProduceJSON enforces valid JSON output from templates.
// This also blocks generating plaintext to trigger another protocol.
func TestTemplateCanOnlyProduceJSON(t *testing.T) {
	// Valid templates should pass.
	ok := map[string]any{"url": "https://example.com/hook", "body_template": `{"t":{{json .Title}}}`}
	if err := (webhookChannel{}).Validate(ok); err != nil {
		t.Fatalf("Valid template should pass validation: %v", err)
	}
	// Reject non-JSON output rather than sending it unchanged.
	bad := map[string]any{"url": "http://127.0.0.1:1/hook", "body_template": `not json {{.Count}}`}
	_, err := (webhookChannel{}).Send(context.Background(), bad, singleMsg())
	if err == nil || !IsPermanent(err) {
		t.Fatalf("Non-JSON output should fail permanently, got %v", err)
	}
	if !strings.Contains(err.Error(), "valid JSON") {
		t.Errorf("Error should explain the JSON problem, got %v", err)
	}
}

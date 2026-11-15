package notify

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
)

// Tests for doJSON HTTP-layer error classification.
//
// Channel adapters handle platform business errors such as DingTalk errcode,
// Feishu code and Telegram ok; doJSON independently classifies HTTP errors.
// Without this second boundary, a gateway 503 could become a permanent failure
// while a 403 could unnecessarily retry through three backoff rounds.

func replyServer(t *testing.T, status int, body string) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_, _ = w.Write([]byte(body))
	}))
	t.Cleanup(srv.Close)
	return srv
}

func TestDoJSONClassifiesHTTPStatus(t *testing.T) {
	cases := []struct {
		name      string
		status    int
		permanent bool
	}{
		{"200 success is not an error", 200, false},
		{"429 rate limit is retryable", 429, false},
		{"408 request timeout is retryable", 408, false},
		{"500 server error is retryable", 500, false},
		{"502 gateway error is retryable", 502, false},
		{"503 unavailable is retryable", 503, false},
		{"400 invalid request is permanent", 400, true},
		{"401 authentication failure is permanent", 401, true},
		{"403 forbidden is permanent", 403, true},
		{"404 not found is permanent", 404, true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := replyServer(t, tc.status, `{"detail":"upstream says no"}`)
			_, err := doJSON(context.Background(), "GET", srv.URL, nil, nil)
			if tc.status < 300 {
				if err != nil {
					t.Fatalf("2xx must not return an error: %v", err)
				}
				return
			}
			if err == nil {
				t.Fatal("Non-2xx must return an error")
			}
			if got := IsPermanent(err); got != tc.permanent {
				t.Fatalf("HTTP %d permanent classification: expected %v, got %v (%v)",
					tc.status, tc.permanent, got, err)
			}
			// Errors must include the HTTP status to distinguish configuration and server faults.
			// Assert the numeric status rather than Go StatusText or localized wording:
			// status numbers provide a stable, language-independent contract.
			if !strings.Contains(err.Error(), strconv.Itoa(tc.status)) {
				t.Errorf("Error must include HTTP status %d, got %v", tc.status, err)
			}
		})
	}
}

// TestDoJSONIncludesResponseSnippet checks that the remote error explanation returns,
// so users know why the receiver rejected the request.
func TestDoJSONIncludesResponseSnippet(t *testing.T) {
	srv := replyServer(t, 400, `{"error":"invalid webhook token"}`)
	_, err := doJSON(context.Background(), "GET", srv.URL, nil, nil)
	if err == nil {
		t.Fatal("Expected an error")
	}
	if !strings.Contains(err.Error(), "invalid webhook token") {
		t.Errorf("Error must include remote explanation, got %v", err)
	}
}

// TestDoJSONSnippetIsSingleLineAndBounded constrains snippet formatting.
// It enters last_error and UI tables; multiline or huge content breaks presentation.
func TestDoJSONSnippetIsSingleLineAndBounded(t *testing.T) {
	// Response containing newlines, tabs and 5000 characters of content.
	long := strings.Repeat("x", 5000)
	srv := replyServer(t, 500, "line1\nline2\r\n\tline3 "+long)
	_, err := doJSON(context.Background(), "GET", srv.URL, nil, nil)
	if err == nil {
		t.Fatal("Expected an error")
	}
	msg := err.Error()
	if strings.ContainsAny(msg, "\r\n\t") {
		t.Errorf("Error must be a single line, got %q", msg)
	}
	// The 200-character snippet plus prefix must be much smaller than the response.
	if len(msg) > 400 {
		t.Errorf("Error too long (%d bytes), snippet must truncate: %q", len(msg), msg)
	}
}

// TestDoJSONRejectsOversizedResponse verifies bounded reads of unexpectedly large
// responses rather than loading everything into memory for each last_error record.
func TestDoJSONRejectsOversizedResponse(t *testing.T) {
	huge := strings.Repeat("A", 1<<20) // 1 MiB
	srv := replyServer(t, 400, huge)
	_, err := doJSON(context.Background(), "GET", srv.URL, nil, nil)
	if err == nil {
		t.Fatal("Expected an error")
	}
	if len(err.Error()) > 400 {
		t.Errorf("Oversized response must be bounded and truncated; error length %d", len(err.Error()))
	}
}

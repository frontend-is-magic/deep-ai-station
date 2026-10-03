package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
)

type fixture struct {
	Name   string          `json:"name"`
	Body   json.RawMessage `json:"body"`
	Status int             `json:"status"`
	Mode   string          `json:"mode"`
	Source string          `json:"source"`
}

func TestSharedContract(t *testing.T) {
	data, err := os.ReadFile("contract-cases.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []fixture
	if err := json.Unmarshal(data, &cases); err != nil {
		t.Fatal(err)
	}
	app := router(&http.Client{}, func(string) string { return "" }, time.Now)
	for _, test := range cases {
		t.Run(test.Name, func(t *testing.T) {
			response := httptest.NewRecorder()
			request := httptest.NewRequest(http.MethodPost, "/api/ask", bytes.NewReader(test.Body))
			request.Header.Set("Content-Type", "application/json")
			app.ServeHTTP(response, request)
			if response.Code != test.Status {
				t.Fatalf("status %d, expected %d", response.Code, test.Status)
			}
			if test.Mode != "" {
				var result Answer
				if err := json.Unmarshal(response.Body.Bytes(), &result); err != nil {
					t.Fatal(err)
				}
				if result.Mode != test.Mode || result.RunID == "" || result.Usage != nil {
					t.Fatal("result contract")
				}
				if test.Source == "" {
					if len(result.Sources) != 0 {
						t.Fatal("expected no sources")
					}
				} else {
					found := false
					for _, source := range result.Sources {
						found = found || source.ID == test.Source
					}
					if !found {
						t.Fatal("missing actual source")
					}
				}
			}
		})
	}
}
func TestBoundedWireInput(t *testing.T) {
	app := router(&http.Client{}, func(string) string { return "" }, time.Now)
	for _, body := range []string{"{", "[]", `"bad"`, `{"prompt":"` + strings.Repeat("x", 17000) + `"}`} {
		result := httptest.NewRecorder()
		request := httptest.NewRequest(http.MethodPost, "/api/ask", strings.NewReader(body))
		app.ServeHTTP(result, request)
		expected := 422
		if len(body) > 16384 {
			expected = 413
		}
		if result.Code != expected {
			t.Fatalf("status %d", result.Code)
		}
	}
}

type transport func(*http.Request) (*http.Response, error)

func (fn transport) RoundTrip(request *http.Request) (*http.Response, error) { return fn(request) }
func testEnv(key string) string {
	if key == "OPENAI_API_KEY" {
		return "test-only"
	}
	if key == "PLAYGROUND_ACCESS_TOKEN" {
		return "test-access"
	}
	return ""
}
func TestLiveMockAndQuota(t *testing.T) {
	calls := 0
	client := &http.Client{Transport: transport(func(request *http.Request) (*http.Response, error) {
		calls++
		if request.URL.String() != "https://api.openai.com/v1/chat/completions" {
			t.Fatal("unexpected endpoint")
		}
		var body map[string]any
		if err := json.NewDecoder(request.Body).Decode(&body); err != nil {
			t.Fatal(err)
		}
		if body["max_completion_tokens"] != float64(800) {
			t.Fatal("token budget")
		}
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(`{"choices":[{"finish_reason":"stop","message":{"content":"实际 API 资料"}}],"usage":{"total_tokens":12,"unexpected":"discard"}}`))}, nil
	})}
	app := router(client, testEnv, time.Now)
	for step := 0; step < 11; step++ {
		response := httptest.NewRecorder()
		request := httptest.NewRequest(http.MethodPost, "/api/ask", strings.NewReader(`{"prompt":"API","mode":"openai"}`))
		request.Header.Set("X-Playground-Token", "test-access")
		app.ServeHTTP(response, request)
		expected := 200
		if step == 10 {
			expected = 429
		}
		if response.Code != expected {
			t.Fatalf("status %d", response.Code)
		}
		if step < 10 {
			var result Answer
			json.Unmarshal(response.Body.Bytes(), &result)
			if len(result.Usage) != 1 || result.Usage["total_tokens"] != 12 {
				t.Fatal("usage whitelist")
			}
		}
	}
	if calls != 10 {
		t.Fatal("quota did not count actual provider requests")
	}
}
func TestInvalidProviderResponses(t *testing.T) {
	for _, body := range []string{`{}`, `{"choices":[]}`, `{"choices":[{"finish_reason":"length","message":{"content":"partial"}}]}`, strings.Repeat("x", 1000001)} {
		client := &http.Client{Transport: transport(func(*http.Request) (*http.Response, error) {
			return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(body))}, nil
		})}
		_, _, err := generate(context.Background(), client, testEnv, "API", nil)
		if err == nil || err.(APIError).Status != 502 || err.Error() != "provider_invalid_response" {
			t.Fatal("unsafe provider response accepted")
		}
	}
}

type closeReader struct {
	*strings.Reader
	closed bool
}

func (reader *closeReader) Close() error { reader.closed = true; return nil }
func TestProviderBodyClosedAfterOversizedResponse(t *testing.T) {
	body := &closeReader{Reader: strings.NewReader(strings.Repeat("x", 1000001))}
	client := &http.Client{Transport: transport(func(*http.Request) (*http.Response, error) { return &http.Response{StatusCode: 200, Body: body}, nil })}
	_, _, err := generate(context.Background(), client, testEnv, "API", nil)
	if err == nil || !body.closed {
		t.Fatal("provider body not closed")
	}
}

func TestCancellationPropagatesToProvider(t *testing.T) {
	waiting := make(chan struct{})
	client := &http.Client{Transport: transport(func(request *http.Request) (*http.Response, error) {
		close(waiting)
		<-request.Context().Done()
		return nil, request.Context().Err()
	})}
	ctx, cancel := context.WithCancel(context.Background())
	result := make(chan error, 1)
	go func() { _, _, err := generate(ctx, client, testEnv, "API", nil); result <- err }()
	<-waiting
	cancel()
	err := <-result
	if err == nil || err.(APIError).Status != 499 {
		t.Fatal("cancel did not stop provider")
	}
}

func TestNullUsageIsUnknown(t *testing.T) {
	client := &http.Client{Transport: transport(func(*http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(`{"choices":[{"finish_reason":"stop","message":{"content":"API 资料"}}],"usage":{"total_tokens":null}}`))}, nil
	})}
	_, usage, err := generate(context.Background(), client, testEnv, "API", nil)
	if err != nil || usage != nil {
		t.Fatal("null usage must not become zero")
	}
}

func TestPanicDoesNotExposeRequestOrProviderDetails(t *testing.T) {
	var logs bytes.Buffer
	original := gin.DefaultErrorWriter
	gin.DefaultErrorWriter = &logs
	t.Cleanup(func() { gin.DefaultErrorWriter = original })
	client := &http.Client{Transport: transport(func(*http.Request) (*http.Response, error) {
		panic("test-only upstream detail")
	})}
	app := router(client, testEnv, time.Now)
	response := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodPost, "/api/ask", strings.NewReader(`{"prompt":"API","mode":"openai"}`))
	request.Header.Set("X-Playground-Token", "test-access")
	app.ServeHTTP(response, request)
	if response.Code != 500 || strings.TrimSpace(response.Body.String()) != `{"error":"request_failed"}` || logs.Len() != 0 {
		t.Fatal("panic leaked request headers or upstream details")
	}
}

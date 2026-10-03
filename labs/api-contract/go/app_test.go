package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
)

type contractCase struct {
	ID          string          `json:"id"`
	Method      string          `json:"method"`
	Path        string          `json:"path"`
	Status      int             `json:"status"`
	Expected    json.RawMessage `json:"expected"`
	JSON        json.RawMessage `json:"json"`
	Raw         *string         `json:"raw"`
	ContentType string          `json:"content_type"`
	RepeatBody  *struct {
		Character string `json:"character"`
		Count     int    `json:"count"`
	} `json:"repeat_body"`
}

func loadCases(t *testing.T) []contractCase {
	t.Helper()
	data, err := readLabData("contract-cases.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []contractCase
	if err := json.Unmarshal(data, &cases); err != nil {
		t.Fatal(err)
	}
	if len(cases) < 24 {
		t.Fatalf("expected at least the original 24 shared cases, received %d", len(cases))
	}
	return cases
}

func caseRequest(test contractCase) *http.Request {
	var body []byte
	contentType := test.ContentType
	switch {
	case test.JSON != nil:
		// json.RawMessage distinguishes explicit null from an absent field.
		body = test.JSON
		if contentType == "" {
			contentType = "application/json"
		}
	case test.Raw != nil:
		body = []byte(*test.Raw)
	case test.RepeatBody != nil:
		body = []byte(strings.Repeat(test.RepeatBody.Character, test.RepeatBody.Count))
	}
	request := httptest.NewRequest(test.Method, test.Path, bytes.NewReader(body))
	if contentType != "" {
		request.Header.Set("Content-Type", contentType)
	}
	return request
}

func assertResponse(t *testing.T, response *httptest.ResponseRecorder, status int, expected []byte) {
	t.Helper()
	if response.Code != status {
		t.Fatalf("status %d, expected %d; response %s", response.Code, status, response.Body)
	}
	if response.Header().Get("Content-Type") != "application/json; charset=utf-8" {
		t.Fatalf("unexpected response content type %q", response.Header().Get("Content-Type"))
	}
	var actualValue, expectedValue any
	if err := json.Unmarshal(response.Body.Bytes(), &actualValue); err != nil {
		t.Fatal("response is not valid JSON")
	}
	if err := json.Unmarshal(expected, &expectedValue); err != nil {
		t.Fatal("invalid expected JSON")
	}
	if !reflect.DeepEqual(actualValue, expectedValue) {
		t.Fatalf("response %s, expected %s", response.Body, expected)
	}
}

func TestSharedContract(t *testing.T) {
	repository, err := LoadRepository()
	if err != nil {
		t.Fatal(err)
	}
	router := NewRouter(repository)
	for _, test := range loadCases(t) {
		t.Run(test.ID, func(t *testing.T) {
			response := httptest.NewRecorder()
			router.ServeHTTP(response, caseRequest(test))
			assertResponse(t, response, test.Status, test.Expected)
		})
	}
}

type stubRepository struct {
	findCalls   int
	searchCalls int
	err         error
	panicValue  any
}

func (repository *stubRepository) Find(string) (*Lesson, error) {
	repository.findCalls++
	if repository.panicValue != nil {
		panic(repository.panicValue)
	}
	return nil, repository.err
}

func (repository *stubRepository) Search(string) ([]Lesson, error) {
	repository.searchCalls++
	if repository.panicValue != nil {
		panic(repository.panicValue)
	}
	return nil, repository.err
}

func TestRepositoryCanBeReplaced(t *testing.T) {
	for _, test := range []struct {
		name         string
		repository   *stubRepository
		findStatus   int
		searchStatus int
		findBody     string
		searchBody   string
	}{
		{"empty", &stubRepository{}, 404, 200, `{"error":"lesson_not_found"}`, `{"question":"工具","items":[]}`},
		{"failure", &stubRepository{err: errors.New("private database internal detail")}, 503, 503, `{"error":"repository_unavailable"}`, `{"error":"repository_unavailable"}`},
	} {
		t.Run(test.name, func(t *testing.T) {
			router := NewRouter(test.repository)
			found := httptest.NewRecorder()
			router.ServeHTTP(found, httptest.NewRequest(http.MethodGet, "/lessons/tools", nil))
			assertResponse(t, found, test.findStatus, []byte(test.findBody))
			search := httptest.NewRecorder()
			request := httptest.NewRequest(http.MethodPost, "/search", strings.NewReader(`{"question":"工具"}`))
			request.Header.Set("Content-Type", "application/json")
			router.ServeHTTP(search, request)
			assertResponse(t, search, test.searchStatus, []byte(test.searchBody))
			if test.repository.findCalls != 1 || test.repository.searchCalls != 1 {
				t.Fatal("requests did not reach the injected repository")
			}
		})
	}
}

func TestInvalidSharedInputsDoNotQueryRepository(t *testing.T) {
	for _, test := range loadCases(t) {
		if test.Method != http.MethodPost || test.Status < 400 {
			continue
		}
		t.Run(test.ID, func(t *testing.T) {
			repository := &stubRepository{}
			response := httptest.NewRecorder()
			NewRouter(repository).ServeHTTP(response, caseRequest(test))
			assertResponse(t, response, test.Status, test.Expected)
			if repository.findCalls != 0 || repository.searchCalls != 0 {
				t.Fatal("invalid input queried repository")
			}
		})
	}
}

func TestMalformedUTF8AndExactFieldNames(t *testing.T) {
	for _, body := range [][]byte{
		append([]byte(`{"question":"`), 0xff, '"', '}'),
		[]byte(`{"Question":"工具"}`),
		[]byte(`{"question":{"text":"工具"}}`),
	} {
		repository := &stubRepository{}
		response := httptest.NewRecorder()
		request := httptest.NewRequest(http.MethodPost, "/search", bytes.NewReader(body))
		request.Header.Set("Content-Type", "application/json")
		NewRouter(repository).ServeHTTP(response, request)
		assertResponse(t, response, 422, []byte(`{"error":"invalid_input"}`))
		if repository.searchCalls != 0 {
			t.Fatal("invalid input queried repository")
		}
	}
}

type chunkedReader struct {
	data []byte
}

func (reader *chunkedReader) Read(destination []byte) (int, error) {
	if len(reader.data) == 0 {
		return 0, io.EOF
	}
	size := min(len(destination), len(reader.data), 17)
	copy(destination, reader.data[:size])
	reader.data = reader.data[size:]
	return size, nil
}

func TestActualBodyLimitPrecedesMediaAndJSON(t *testing.T) {
	for _, contentLength := range []int64{-1, 1} {
		for _, contentType := range []string{"application/json", "text/plain", ""} {
			repository := &stubRepository{}
			stream := &chunkedReader{data: bytes.Repeat([]byte("x"), 9000)}
			request := httptest.NewRequest(http.MethodPost, "/search", stream)
			request.ContentLength = contentLength
			request.TransferEncoding = []string{"chunked"}
			if contentType != "" {
				request.Header.Set("Content-Type", contentType)
			}
			response := httptest.NewRecorder()
			NewRouter(repository).ServeHTTP(response, request)
			assertResponse(t, response, 413, []byte(`{"error":"request_too_large"}`))
			if repository.searchCalls != 0 {
				t.Fatal("oversized input queried repository")
			}
			if len(stream.data) != 9000-4097 {
				t.Fatal("body read was not bounded to limit plus one byte")
			}
		}
	}
}

func TestExactByteLimitAndTrimBeforeCodepointLimit(t *testing.T) {
	prefix := `{"question":"工具"}`
	body := prefix + strings.Repeat(" ", 4096-len(prefix))
	for _, input := range []string{body, `{"question":"   ` + strings.Repeat("🙂", 500) + `   "}`} {
		repository := &stubRepository{}
		request := httptest.NewRequest(http.MethodPost, "/search", strings.NewReader(input))
		request.Header.Set("Content-Type", "application/json")
		response := httptest.NewRecorder()
		NewRouter(repository).ServeHTTP(response, request)
		if response.Code != 200 || repository.searchCalls != 1 {
			t.Fatalf("valid boundary input rejected with status %d", response.Code)
		}
	}
}

type failedReader struct{}

func (failedReader) Read([]byte) (int, error) {
	return 0, errors.New("internal body read detail")
}

func TestBodyReadFailureIsSanitized(t *testing.T) {
	repository := &stubRepository{}
	request := httptest.NewRequest(http.MethodPost, "/search", failedReader{})
	request.Header.Set("Content-Type", "application/json")
	response := httptest.NewRecorder()
	NewRouter(repository).ServeHTTP(response, request)
	assertResponse(t, response, 422, []byte(`{"error":"invalid_input"}`))
	if repository.searchCalls != 0 {
		t.Fatal("unreadable request reached repository")
	}
}

func TestUnexpectedPanicDoesNotLeakDetails(t *testing.T) {
	var logs bytes.Buffer
	previous := gin.DefaultErrorWriter
	gin.DefaultErrorWriter = &logs
	t.Cleanup(func() { gin.DefaultErrorWriter = previous })
	response := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/lessons/tools", nil)
	request.Header.Set("X-Test-Private", "test-only private header")
	NewRouter(&stubRepository{panicValue: "private panic detail"}).ServeHTTP(response, request)
	assertResponse(t, response, 500, []byte(`{"error":"request_failed"}`))
	if logs.Len() != 0 {
		t.Fatal("panic handler logged private request or internal details")
	}
}

func TestSearchPreservesOrderAndSeparatesTitleFromBody(t *testing.T) {
	repository := NewMemoryRepository([]Lesson{
		{ID: "first", Title: "Title", Body: "HTTP reference"},
		{ID: "second", Title: "HTTP API", Body: "Details"},
		{ID: "boundary", Title: "ab", Body: "cd"},
	})
	results, err := repository.Search("http")
	if err != nil || len(results) != 2 || results[0].ID != "first" || results[1].ID != "second" {
		t.Fatal("search changed fixed document order or case-insensitive behavior")
	}
	results, err = repository.Search("bc")
	if err != nil || len(results) != 0 {
		t.Fatal("search incorrectly matched across title/body boundary")
	}
}

func TestSourceAndArchiveDataLocations(t *testing.T) {
	root := t.TempDir()
	source := filepath.Join(root, "go")
	shared := filepath.Join(root, "shared")
	for _, directory := range []string{source, shared} {
		if err := os.Mkdir(directory, 0700); err != nil {
			t.Fatal(err)
		}
	}
	fallback := []byte(`[{"id":"shared","title":"Shared","body":"fixed"}]`)
	if err := os.WriteFile(filepath.Join(shared, "lessons.json"), fallback, 0600); err != nil {
		t.Fatal(err)
	}
	t.Chdir(source)
	repository, err := LoadRepository()
	if err != nil || repository.lessons[0].ID != "shared" {
		t.Fatal("source directory did not read shared data")
	}
	local := []byte(`[{"id":"archive","title":"Archive","body":"fixed"}]`)
	if err := os.WriteFile("lessons.json", local, 0600); err != nil {
		t.Fatal(err)
	}
	repository, err = LoadRepository()
	if err != nil || repository.lessons[0].ID != "archive" {
		t.Fatal("archive-root data did not take precedence")
	}
	if err := os.WriteFile("lessons.json", []byte("{"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadRepository(); err == nil {
		t.Fatal("invalid local data silently fell back to shared data")
	}
}

func TestListenAddressStaysOnLoopback(t *testing.T) {
	for _, test := range []struct{ port, expected string }{
		{"", "127.0.0.1:8020"}, {"8137", "127.0.0.1:8137"},
		{"1", "127.0.0.1:1"}, {"65535", "127.0.0.1:65535"},
	} {
		address, err := listenAddress(test.port)
		if err != nil || address != test.expected {
			t.Fatal("port configuration changed the loopback binding")
		}
	}
	for _, port := range []string{"0", "65536", "-1", "1.5", "0.0.0.0:8020", " 8020 "} {
		if _, err := listenAddress(port); err == nil {
			t.Fatal("invalid port accepted")
		}
	}
}

func TestUnicodeEscapeValidation(t *testing.T) {
	for _, test := range []struct {
		name     string
		body     string
		valid    bool
		expected string
	}{
		{"high-alone", `{"question":"\ud800"}`, false, ""},
		{"low-alone", `{"question":"\udfff"}`, false, ""},
		{"reversed-pair", `{"question":"\udc00\ud800"}`, false, ""},
		{"non-adjacent", `{"question":"\ud800x\udc00"}`, false, ""},
		{"high-plus-bmp", `{"question":"\ud800\u0041"}`, false, ""},
		{"high-plus-high", `{"question":"\ud800\ud800"}`, false, ""},
		{"escaped-emoji", `{"question":"\ud83d\uDE42"}`, true, "🙂"},
		{"lowest-pair", `{"question":"\ud800\udc00"}`, true, "\U00010000"},
		{"highest-pair", `{"question":"\udbff\udfff"}`, true, "\U0010ffff"},
		{"literal-backslash", `{"question":"\\ud800"}`, true, `\ud800`},
		{"literal-low-backslash", `{"question":"\\udfff"}`, true, `\udfff`},
		{"bmp", `{"question":"\u5de5\u5177"}`, true, "工具"},
	} {
		t.Run(test.name, func(t *testing.T) {
			repository := &stubRepository{}
			response := httptest.NewRecorder()
			request := httptest.NewRequest(http.MethodPost, "/search", strings.NewReader(test.body))
			request.Header.Set("Content-Type", "application/json")
			NewRouter(repository).ServeHTTP(response, request)
			if !test.valid {
				assertResponse(t, response, 422, []byte(`{"error":"invalid_input"}`))
				if repository.searchCalls != 0 {
					t.Fatal("invalid unicode escape queried repository")
				}
				return
			}
			expected, err := json.Marshal(SearchResponse{Question: test.expected, Items: []LessonSummary{}})
			if err != nil {
				t.Fatal(err)
			}
			assertResponse(t, response, 200, expected)
			if repository.searchCalls != 1 {
				t.Fatal("valid unicode escape did not query repository")
			}
		})
	}
}

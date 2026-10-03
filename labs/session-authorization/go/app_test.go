package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"github.com/gin-gonic/gin"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func testRouter(t *testing.T, config Config) *gin.Engine {
	t.Helper()
	router, err := NewRouter(config)
	if err != nil {
		t.Fatal(err)
	}
	return router
}
func request(method, path, token, body string) *http.Request {
	r := httptest.NewRequest(method, path, strings.NewReader(body))
	if token != "" {
		r.Header.Set("Authorization", "Bearer "+token)
	}
	if method == "PATCH" || method == "POST" {
		r.Header.Set("Content-Type", "application/json")
	}
	return r
}
func perform(router http.Handler, r *http.Request) *httptest.ResponseRecorder {
	response := httptest.NewRecorder()
	router.ServeHTTP(response, r)
	return response
}
func assertResponse(t *testing.T, r *httptest.ResponseRecorder, status int, expected string) {
	t.Helper()
	if r.Code != status {
		t.Fatalf("status %d expected %d response %s", r.Code, status, r.Body)
	}
	if r.Header().Get("Cache-Control") != "no-store" || r.Header().Get("Content-Type") != "application/json; charset=utf-8" {
		t.Fatal("response cache/JSON headers differ")
	}
	var a, b any
	if json.Unmarshal(r.Body.Bytes(), &a) != nil || json.Unmarshal([]byte(expected), &b) != nil || !reflect.DeepEqual(a, b) {
		t.Fatalf("response %s expected %s", r.Body, expected)
	}
	if status == 401 && r.Header().Get("WWW-Authenticate") != `Bearer realm="session-authorization"` {
		t.Fatal("missing challenge")
	}
}
func cookieRequest(method, path, body string) *http.Request {
	r := request(method, path, "", body)
	r.Header.Set("Cookie", sessionCookieName+"=lab-alice-session")
	r.Header.Set("Origin", "https://lab.example.test")
	r.Header.Set("X-CSRF-Token", "lab-csrf-a")
	return r
}

type contractCase struct {
	ID              string            `json:"id"`
	Method          string            `json:"method"`
	Path            string            `json:"path"`
	Status          int               `json:"status"`
	Expected        json.RawMessage   `json:"expected"`
	JSON            json.RawMessage   `json:"json"`
	Raw             *string           `json:"raw"`
	Headers         map[string]string `json:"headers"`
	HeaderPairs     [][2]string       `json:"header_pairs"`
	ContentType     string            `json:"content_type"`
	ResponseHeaders map[string]string `json:"response_headers"`
	SetCookie       *bool             `json:"set_cookie"`
	RepeatBody      *struct {
		Character string `json:"character"`
		Count     int    `json:"count"`
	} `json:"repeat_body"`
}

func TestSharedContract(t *testing.T) {
	data, err := readLabData("contract-cases.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []contractCase
	if json.Unmarshal(data, &cases) != nil || len(cases) == 0 {
		t.Fatal("missing or invalid shared cases")
	}
	router := testRouter(t, Config{})
	for _, test := range cases {
		t.Run(test.ID, func(t *testing.T) {
			var body []byte
			media := test.ContentType
			switch {
			case test.JSON != nil:
				body = test.JSON
				if media == "" {
					media = "application/json"
				}
			case test.Raw != nil:
				body = []byte(*test.Raw)
			case test.RepeatBody != nil:
				body = []byte(strings.Repeat(test.RepeatBody.Character, test.RepeatBody.Count))
			}
			r := httptest.NewRequest(test.Method, test.Path, bytes.NewReader(body))
			if media != "" {
				r.Header.Set("Content-Type", media)
			}
			for k, v := range test.Headers {
				r.Header.Set(k, v)
			}
			for _, pair := range test.HeaderPairs {
				r.Header.Add(pair[0], pair[1])
			}
			response := perform(router, r)
			assertResponse(t, response, test.Status, string(test.Expected))
			for k, v := range test.ResponseHeaders {
				if response.Header().Get(k) != v {
					t.Fatalf("header %s differs", k)
				}
			}
			if test.SetCookie != nil {
				if *test.SetCookie {
					assertCookie(t, response.Result().Cookies(), true)
				} else if len(response.Header().Values("Set-Cookie")) != 0 {
					t.Fatal("response unexpectedly sets a cookie")
				}
			}
		})
	}
}
func TestCredentialParsing(t *testing.T) {
	cases := []struct {
		name    string
		headers [][2]string
		allow   bool
		status  int
		code    string
	}{
		{"missing", nil, true, 401, "authentication_required"}, {"spoofed owner", [][2]string{{"X-User-Id", "alice"}}, true, 401, "authentication_required"},
		{"scheme case and spaces", [][2]string{{"Authorization", "bEaReR   lab-alice-session"}}, true, 200, ""}, {"scheme tab", [][2]string{{"Authorization", "Bearer\tlab-alice-session"}}, true, 401, "authentication_required"},
		{"quoted token", [][2]string{{"Authorization", `Bearer "lab-alice-session"`}}, true, 401, "authentication_required"}, {"unicode scheme", [][2]string{{"Authorization", "Béarer lab-alice-session"}}, true, 401, "authentication_required"},
		{"overlong", [][2]string{{"Authorization", "Bearer " + strings.Repeat("a", 129)}}, true, 401, "authentication_required"},
		{"duplicate", [][2]string{{"Authorization", "Bearer lab-alice-session"}, {"Authorization", "Bearer lab-alice-session"}}, true, 400, "ambiguous_credentials"},
		{"combined", [][2]string{{"Authorization", "Bearer lab-alice-session, Bearer lab-bob-session"}}, true, 400, "ambiguous_credentials"},
		{"empty bearer plus cookie", [][2]string{{"Authorization", ""}, {"Cookie", sessionCookieName + "=lab-alice-session"}}, true, 400, "ambiguous_credentials"},
		{"bearer plus empty cookie", [][2]string{{"Authorization", "Bearer lab-alice-session"}, {"Cookie", sessionCookieName + "="}}, true, 400, "ambiguous_credentials"},
		{"duplicate cookie lines", [][2]string{{"Cookie", sessionCookieName + "=lab-alice-session"}, {"Cookie", sessionCookieName + "=lab-alice-session"}}, true, 400, "ambiguous_credentials"},
		{"duplicate cookie pairs", [][2]string{{"Cookie", sessionCookieName + "=lab-alice-session; " + sessionCookieName + "="}}, true, 400, "ambiguous_credentials"},
		{"cookie spaces", [][2]string{{"Cookie", "other=value; \t" + sessionCookieName + " \t=\tlab-alice-session "}}, true, 200, ""},
		{"cookie not decoded", [][2]string{{"Cookie", sessionCookieName + "=lab%2Dalice%2Dsession"}}, true, 401, "authentication_required"},
		{"cookie quotes", [][2]string{{"Cookie", sessionCookieName + `="lab-alice-session"`}}, true, 401, "authentication_required"},
		{"disabled cookie", [][2]string{{"Cookie", sessionCookieName + "=lab-alice-session"}}, false, 401, "authentication_required"},
		{"disabled mixed", [][2]string{{"Authorization", "Bearer lab-alice-session"}, {"Cookie", sessionCookieName + "=invalid"}}, false, 400, "ambiguous_credentials"},
		{"unrelated cookie", [][2]string{{"Authorization", "Bearer lab-alice-session"}, {"Cookie", "other=value"}}, false, 200, ""},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			r := request("GET", "/me", "", "")
			for _, pair := range test.headers {
				r.Header.Add(pair[0], pair[1])
			}
			expected := `{"user_id":"alice"}`
			if test.code != "" {
				expected = `{"error":"` + test.code + `"}`
			}
			assertResponse(t, perform(testRouter(t, Config{AllowCookie: &test.allow}), r), test.status, expected)
		})
	}
}
func TestClockExpiryBoundary(t *testing.T) {
	now := time.Date(2026, 10, 4, 0, 0, 0, 0, time.UTC)
	router := testRouter(t, Config{Clock: func() time.Time { return now }})
	now = now.Add(time.Hour - time.Nanosecond)
	assertResponse(t, perform(router, request("GET", "/me", "lab-alice-session", "")), 200, `{"user_id":"alice"}`)
	now = now.Add(time.Nanosecond)
	assertResponse(t, perform(router, request("GET", "/me", "lab-alice-session", "")), 401, `{"error":"authentication_required"}`)
}

type spyRepository struct {
	calls      int
	owners     []string
	err        error
	panicValue any
}

func (r *spyRepository) touch(owner string) error {
	r.calls++
	r.owners = append(r.owners, owner)
	if r.panicValue != nil {
		panic(r.panicValue)
	}
	return r.err
}
func (r *spyRepository) List(owner string) ([]Document, error) { return []Document{}, r.touch(owner) }
func (r *spyRepository) Find(owner, id string) (*Document, error) {
	return &Document{ID: id}, r.touch(owner)
}
func (r *spyRepository) SetArchived(owner, id string, canWrite, archived bool) (*Document, error) {
	return &Document{ID: id, Archived: archived}, r.touch(owner)
}

type faultySessions struct {
	store                                 SessionStore
	resolveError, revokeError, panicValue bool
}

func (s faultySessions) Resolve(token string, now time.Time) (*Principal, error) {
	if s.resolveError {
		if s.panicValue {
			panic("private session source")
		}
		return nil, errors.New("private session source")
	}
	return s.store.Resolve(token, now)
}
func (s faultySessions) Revoke(token string) error {
	if s.revokeError {
		if s.panicValue {
			panic("private session source")
		}
		return errors.New("private session source")
	}
	return s.store.Revoke(token)
}
func TestSessionFailuresAreOpaque(t *testing.T) {
	fixtures, err := LoadFixtures()
	if err != nil {
		t.Fatal(err)
	}
	for _, panicValue := range []bool{false, true} {
		for _, revoke := range []bool{false, true} {
			repository := &spyRepository{}
			store := faultySessions{store: NewMemorySessionStore(fixtures.Sessions, time.Now()), resolveError: !revoke, revokeError: revoke, panicValue: panicValue}
			router := testRouter(t, Config{Sessions: store, Repository: repository})
			r := request("GET", "/documents", "lab-alice-session", "")
			if revoke {
				r = cookieRequest("POST", "/logout", "{}")
			}
			response := perform(router, r)
			assertResponse(t, response, 503, `{"error":"session_store_unavailable"}`)
			if repository.calls != 0 || response.Header().Get("Set-Cookie") != "" {
				t.Fatal("failed session operation had side effects")
			}
		}
	}
}
func TestRepositoryFailuresAndTrustedOwners(t *testing.T) {
	for _, panicValue := range []bool{false, true} {
		repository := &spyRepository{err: errors.New("private SQL and path")}
		if panicValue {
			repository.panicValue = "private SQL and path"
		}
		router := testRouter(t, Config{Repository: repository})
		for _, r := range []*http.Request{request("GET", "/documents", "lab-alice-session", ""), request("GET", "/documents/alice-notes", "lab-alice-session", ""), request("PATCH", "/documents/alice-notes", "lab-alice-session", `{"archived":true}`)} {
			r.Header.Set("X-User-Id", "bob")
			assertResponse(t, perform(router, r), 503, `{"error":"repository_unavailable"}`)
		}
		if !reflect.DeepEqual(repository.owners, []string{"alice", "alice", "alice"}) {
			t.Fatal("untrusted Repository owner")
		}
	}
}
func TestRejectedInputsDoNotAccessRepository(t *testing.T) {
	cases := []struct {
		name, token, body, media, query, origin, csrf string
		status                                        int
		code                                          string
		cookie                                        bool
	}{
		{name: "unknown auth before body", token: "unknown", body: strings.Repeat("x", 5000), status: 401, code: "authentication_required"},
		{name: "query", query: "?owner=bob", body: "invalid", status: 422, code: "invalid_input"},
		{name: "oversized before media", body: strings.Repeat("x", 4097), media: "text/plain", status: 413, code: "request_too_large"},
		{name: "media before JSON", body: "invalid", media: "text/plain", status: 415, code: "unsupported_media_type"},
		{name: "duplicate JSON", body: `{"archived":true,"archived":false}`, status: 422, code: "invalid_input"},
		{name: "encoded duplicate JSON", body: `{"archived":true,"\u0061rchived":false}`, status: 422, code: "invalid_input"},
		{name: "wrong bool", body: `{"archived":1}`, status: 422, code: "invalid_input"},
		{name: "invalid UTF8", body: string([]byte{0xff}), status: 422, code: "invalid_input"},
		{name: "BOM", body: "\ufeff{\"archived\":true}", status: 422, code: "invalid_input"},
		{name: "JSON before CSRF", body: "invalid", cookie: true, status: 422, code: "invalid_input"},
		{name: "missing CSRF", body: `{"archived":true}`, cookie: true, origin: "https://lab.example.test", status: 403, code: "csrf_failed"},
		{name: "cross-session CSRF", body: `{"archived":true}`, cookie: true, origin: "https://lab.example.test", csrf: "lab-csrf-a2", status: 403, code: "csrf_failed"},
		{name: "similar origin", body: `{"archived":true}`, cookie: true, origin: "https://lab.example.test.evil.test", csrf: "lab-csrf-a", status: 403, code: "csrf_failed"},
		{name: "null origin", body: `{"archived":true}`, cookie: true, origin: "null", csrf: "lab-csrf-a", status: 403, code: "csrf_failed"},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			repository := &spyRepository{}
			token := test.token
			if token == "" {
				token = "lab-alice-session"
			}
			r := request("PATCH", "/documents/alice-notes"+test.query, token, test.body)
			if test.cookie {
				r.Header.Del("Authorization")
				r.Header.Set("Cookie", sessionCookieName+"=lab-alice-session")
				if test.origin != "" {
					r.Header.Set("Origin", test.origin)
				}
				if test.csrf != "" {
					r.Header.Set("X-CSRF-Token", test.csrf)
				}
			}
			if test.media != "" {
				r.Header.Set("Content-Type", test.media)
			}
			assertResponse(t, perform(testRouter(t, Config{Repository: repository}), r), test.status, `{"error":"`+test.code+`"}`)
			if repository.calls != 0 {
				t.Fatal("rejected input reached Repository")
			}
		})
	}
}
func TestRawDuplicateHeadersOverHTTP(t *testing.T) {
	server := httptest.NewServer(testRouter(t, Config{}))
	defer server.Close()
	client := server.Client()
	defer client.CloseIdleConnections()
	for _, test := range []struct {
		header, value string
		status        int
		code          string
	}{{"Authorization", "Bearer lab-alice-session", 400, "ambiguous_credentials"}, {"Origin", "https://lab.example.test", 403, "csrf_failed"}, {"X-CSRF-Token", "lab-csrf-a", 403, "csrf_failed"}} {
		t.Run(test.header, func(t *testing.T) {
			r, _ := http.NewRequest("PATCH", server.URL+"/documents/alice-notes", strings.NewReader(`{"archived":true}`))
			r.Header = cookieRequest("PATCH", "/documents/alice-notes", "").Header
			if test.header == "Authorization" {
				r.Header.Del("Cookie")
			}
			r.Header.Del(test.header)
			r.Header.Add(test.header, test.value)
			r.Header.Add(test.header, test.value)
			response, err := client.Do(r)
			if err != nil {
				t.Fatal(err)
			}
			body, err := io.ReadAll(response.Body)
			response.Body.Close()
			if err != nil {
				t.Fatal(err)
			}
			if response.StatusCode != test.status || string(body) != `{"error":"`+test.code+`"}` || response.Header.Get("Cache-Control") != "no-store" {
				t.Fatalf("raw duplicate accepted: %d %s", response.StatusCode, body)
			}
		})
	}
}

type chunkedReader struct{ remaining, read int }

func (r *chunkedReader) Read(p []byte) (int, error) {
	if r.remaining == 0 {
		return 0, io.EOF
	}
	n := min(len(p), 7, r.remaining)
	for i := 0; i < n; i++ {
		p[i] = 'x'
	}
	r.remaining -= n
	r.read += n
	return n, nil
}
func TestChunkedBodyLimit(t *testing.T) {
	reader := &chunkedReader{remaining: 5000}
	r := request("PATCH", "/documents/alice-notes", "lab-alice-session", "")
	r.Body = io.NopCloser(reader)
	r.ContentLength = -1
	r.TransferEncoding = []string{"chunked"}
	repository := &spyRepository{}
	assertResponse(t, perform(testRouter(t, Config{Repository: repository}), r), 413, `{"error":"request_too_large"}`)
	if reader.read != 4097 || repository.calls != 0 {
		t.Fatal("body read unbounded or reached Repository")
	}
}
func TestExactSizeAndStrictJSON(t *testing.T) {
	router := testRouter(t, Config{})
	body := `{"archived":true}`
	body += strings.Repeat(" ", 4096-len(body))
	assertResponse(t, perform(router, request("PATCH", "/documents/alice-notes", "lab-alice-session", body)), 200, `{"id":"alice-notes","title":"Alice 的笔记","archived":true}`)
	for _, raw := range []string{"null", "[]", "true", "{}", `{"archived":null}`, `{"archived":"true"}`, `{"Archived":true}`, `{"archived":true,"owner":"bob"}`, `{"archived":true} {}`, "\u00a0{\"archived\":true}", `{"archived":true,}`} {
		assertResponse(t, perform(router, request("PATCH", "/documents/alice-notes", "lab-alice-session", raw)), 422, `{"error":"invalid_input"}`)
	}
	for _, raw := range []string{"null", "[]", `{"ignored":true}`, `{} {}`, "\ufeff{}"} {
		assertResponse(t, perform(router, request("POST", "/logout", "lab-alice-session", raw)), 422, `{"error":"invalid_input"}`)
	}
}
func assertCookie(t *testing.T, cookies []*http.Cookie, clear bool) {
	t.Helper()
	if len(cookies) != 1 {
		t.Fatal("expected exactly one session cookie")
	}
	c := cookies[0]
	if c.Name != sessionCookieName || c.Path != "/" || c.Domain != "" || !c.Secure || !c.HttpOnly || c.SameSite != http.SameSiteStrictMode {
		t.Fatal("incorrect cookie attributes")
	}
	if clear && (c.MaxAge != -1 || c.Value != "" || !strings.Contains(c.String(), "Max-Age=0")) {
		t.Fatal("cookie not cleared at same name/path")
	}
}
func TestCookieAttributes(t *testing.T) {
	cookie, err := SessionCookie("lab-alice-session")
	if err != nil {
		t.Fatal(err)
	}
	assertCookie(t, []*http.Cookie{cookie}, false)
	if cookie.Value != "lab-alice-session" {
		t.Fatal("cookie value changed")
	}
	if _, err := SessionCookie("invalid; injected=value"); err == nil {
		t.Fatal("cookie injection accepted")
	}
	assertCookie(t, []*http.Cookie{ClearSessionCookie()}, true)
	router := testRouter(t, Config{})
	response := perform(router, cookieRequest("POST", "/logout", "{}"))
	assertResponse(t, response, 200, `{"ok":true}`)
	assertCookie(t, response.Result().Cookies(), true)
	assertResponse(t, perform(router, request("GET", "/me", "lab-alice-session", "")), 401, `{"error":"authentication_required"}`)
	assertResponse(t, perform(router, request("GET", "/me", "lab-alice-second-session", "")), 200, `{"user_id":"alice"}`)
	assertResponse(t, perform(router, request("GET", "/me", "lab-bob-session", "")), 200, `{"user_id":"bob"}`)
}
func TestLogoutFailureDoesNotRevoke(t *testing.T) {
	router := testRouter(t, Config{})
	r := cookieRequest("POST", "/logout", "{}")
	r.Header.Set("X-CSRF-Token", "wrong")
	assertResponse(t, perform(router, r), 403, `{"error":"csrf_failed"}`)
	assertResponse(t, perform(router, request("GET", "/me", "lab-alice-session", "")), 200, `{"user_id":"alice"}`)
	response := perform(router, request("POST", "/logout", "lab-alice-readonly-session", "{}"))
	assertResponse(t, response, 200, `{"ok":true}`)
	if response.Header().Get("Set-Cookie") != "" {
		t.Fatal("Bearer logout cleared cookie")
	}
	assertResponse(t, perform(router, request("GET", "/me", "lab-alice-readonly-session", "")), 401, `{"error":"authentication_required"}`)
}
func TestOwnerIsolationAndIndependentApplications(t *testing.T) {
	router := testRouter(t, Config{})
	assertResponse(t, perform(router, request("GET", "/documents", "lab-bob-session", "")), 200, `{"items":[{"id":"bob-notes","title":"Bob 的笔记","archived":false}]}`)
	for _, id := range []string{"alice-notes", "does-not-exist"} {
		assertResponse(t, perform(router, request("GET", "/documents/"+id, "lab-bob-session", "")), 404, `{"error":"document_not_found"}`)
		assertResponse(t, perform(router, request("PATCH", "/documents/"+id, "lab-bob-session", `{"archived":true}`)), 404, `{"error":"document_not_found"}`)
	}
	assertResponse(t, perform(router, request("PATCH", "/documents/alice-notes", "lab-alice-readonly-session", `{"archived":true}`)), 403, `{"error":"forbidden"}`)
	assertResponse(t, perform(router, request("PATCH", "/documents/bob-notes", "lab-alice-readonly-session", `{"archived":true}`)), 404, `{"error":"document_not_found"}`)
	assertResponse(t, perform(router, request("PATCH", "/documents/alice-notes", "lab-alice-session", `{"archived":true}`)), 200, `{"id":"alice-notes","title":"Alice 的笔记","archived":true}`)
	assertResponse(t, perform(router, request("POST", "/logout", "lab-alice-session", `{}`)), 200, `{"ok":true}`)
	fresh := testRouter(t, Config{})
	assertResponse(t, perform(fresh, request("GET", "/documents/alice-notes", "lab-alice-session", "")), 200, `{"id":"alice-notes","title":"Alice 的笔记","archived":false}`)
}
func TestCustomOriginAndInternalFailure(t *testing.T) {
	router := testRouter(t, Config{AllowedOrigin: "https://other.example.test"})
	r := cookieRequest("PATCH", "/documents/alice-notes", `{"archived":true}`)
	assertResponse(t, perform(router, r), 403, `{"error":"csrf_failed"}`)
	r = cookieRequest("PATCH", "/documents/alice-notes", `{"archived":true}`)
	r.Header.Set("Origin", "https://other.example.test")
	assertResponse(t, perform(router, r), 200, `{"id":"alice-notes","title":"Alice 的笔记","archived":true}`)
	fixtures, _ := LoadFixtures()
	router = testRouter(t, Config{Clock: func() time.Time { panic("private clock path") }, Sessions: NewMemorySessionStore(fixtures.Sessions, time.Now()), Repository: &spyRepository{}})
	assertResponse(t, perform(router, request("GET", "/me", "lab-alice-session", "")), 500, `{"error":"request_failed"}`)
}
func TestRoutesAndListenAddress(t *testing.T) {
	router := testRouter(t, Config{})
	for _, test := range []struct {
		method, path string
		status       int
		code         string
	}{{"GET", "/missing", 404, "route_not_found"}, {"GET", "/documents/", 404, "route_not_found"}, {"POST", "/me", 405, "method_not_allowed"}, {"GET", "/logout", 405, "method_not_allowed"}, {"OPTIONS", "/documents", 405, "method_not_allowed"}} {
		assertResponse(t, perform(router, request(test.method, test.path, "", "")), test.status, `{"error":"`+test.code+`"}`)
	}
	assertResponse(t, perform(router, request("GET", "/health", "", "")), 200, `{"status":"ok","lab":"session-authorization-v1"}`)
	for _, port := range []string{"0", "65536", "-1", "all", "127.0.0.1:8022", " 8022"} {
		if _, err := listenAddress(port); err == nil {
			t.Fatal("invalid PORT accepted")
		}
	}
	if address, err := listenAddress(""); err != nil || address != "127.0.0.1:8022" {
		t.Fatal("default not loopback")
	}
	if address, err := listenAddress("12345"); err != nil || address != "127.0.0.1:12345" {
		t.Fatal("PORT override failed")
	}
}

type blockingRepository struct {
	Repository
	entered, release chan struct{}
	calls            atomic.Int32
}

func (r *blockingRepository) SetArchived(owner, id string, canWrite, archived bool) (*Document, error) {
	r.calls.Add(1)
	close(r.entered)
	<-r.release
	return r.Repository.SetArchived(owner, id, canWrite, archived)
}
func TestMutationAndRevocationAreSerialized(t *testing.T) {
	fixtures, _ := LoadFixtures()
	repo := &blockingRepository{Repository: NewMemoryRepository(fixtures.Documents), entered: make(chan struct{}), release: make(chan struct{})}
	router := testRouter(t, Config{Repository: repo})
	patched := make(chan *httptest.ResponseRecorder, 1)
	loggedOut := make(chan *httptest.ResponseRecorder, 1)
	go func() {
		patched <- perform(router, request("PATCH", "/documents/alice-notes", "lab-alice-session", `{"archived":true}`))
	}()
	select {
	case <-repo.entered:
	case <-time.After(time.Second):
		t.Fatal("mutation never reached Repository")
	}
	go func() { loggedOut <- perform(router, request("POST", "/logout", "lab-alice-session", "{}")) }()
	select {
	case <-loggedOut:
		t.Fatal("revocation overtook mutation")
	case <-time.After(20 * time.Millisecond):
	}
	close(repo.release)
	assertResponse(t, <-patched, 200, `{"id":"alice-notes","title":"Alice 的笔记","archived":true}`)
	assertResponse(t, <-loggedOut, 200, `{"ok":true}`)
	assertResponse(t, perform(router, request("PATCH", "/documents/alice-notes", "lab-alice-session", `{"archived":false}`)), 401, `{"error":"authentication_required"}`)
	if repo.calls.Load() != 1 {
		t.Fatal("revoked session reached Repository")
	}
}

// The first Read pauses with no Content-Length, so another authenticated request
// can revoke or the injected clock can expire the session before body completion.
type pausedBody struct {
	data             []byte
	entered, release chan struct{}
	once             sync.Once
}

func (body *pausedBody) Read(destination []byte) (int, error) {
	body.once.Do(func() { close(body.entered); <-body.release })
	if len(body.data) == 0 {
		return 0, io.EOF
	}
	count := copy(destination, body.data)
	body.data = body.data[count:]
	return count, nil
}
func startPausedWrite(t *testing.T, router http.Handler) (chan *httptest.ResponseRecorder, func()) {
	t.Helper()
	body := &pausedBody{data: []byte(`{"archived":true}`), entered: make(chan struct{}), release: make(chan struct{})}
	var releaseOnce sync.Once
	release := func() { releaseOnce.Do(func() { close(body.release) }) }
	t.Cleanup(release)
	r := request("PATCH", "/documents/alice-notes", "lab-alice-session", "")
	r.Body = io.NopCloser(body)
	r.ContentLength = -1
	r.TransferEncoding = []string{"chunked"}
	result := make(chan *httptest.ResponseRecorder, 1)
	go func() { result <- perform(router, r) }()
	select {
	case <-body.entered:
	case <-time.After(time.Second):
		t.Fatal("body read did not start")
	}
	return result, release
}
func TestLogoutDuringSlowBodyRevalidatesSession(t *testing.T) {
	repository := &spyRepository{}
	router := testRouter(t, Config{Repository: repository})
	result, release := startPausedWrite(t, router)
	loggedOut := make(chan *httptest.ResponseRecorder, 1)
	go func() { loggedOut <- perform(router, request("POST", "/logout", "lab-alice-session", "{}")) }()
	select {
	case response := <-loggedOut:
		assertResponse(t, response, 200, `{"ok":true}`)
	case <-time.After(time.Second):
		t.Fatal("slow body blocked independent logout")
	}
	release()
	select {
	case response := <-result:
		assertResponse(t, response, 401, `{"error":"authentication_required"}`)
	case <-time.After(time.Second):
		t.Fatal("revoked write did not return")
	}
	if repository.calls != 0 {
		t.Fatal("revoked slow write reached Repository")
	}
}
func TestExpiryDuringSlowBodyRevalidatesSession(t *testing.T) {
	var now atomic.Int64
	start := time.Date(2026, 10, 4, 0, 0, 0, 0, time.UTC)
	now.Store(start.UnixNano())
	repository := &spyRepository{}
	router := testRouter(t, Config{Repository: repository, Clock: func() time.Time { return time.Unix(0, now.Load()) }})
	result, release := startPausedWrite(t, router)
	now.Store(start.Add(time.Hour).UnixNano())
	release()
	select {
	case response := <-result:
		assertResponse(t, response, 401, `{"error":"authentication_required"}`)
	case <-time.After(time.Second):
		t.Fatal("expired write did not return")
	}
	if repository.calls != 0 {
		t.Fatal("expired slow write reached Repository")
	}
}

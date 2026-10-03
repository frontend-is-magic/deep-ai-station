package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
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

func testRouter(t *testing.T, config Config) http.Handler {
	t.Helper()
	router, err := NewRouter(config)
	if err != nil {
		t.Fatal(err)
	}
	return router
}
func request(method, path, token string, body []byte) *http.Request {
	r := httptest.NewRequest(method, path, bytes.NewReader(body))
	if token != "" {
		r.Header.Set("Authorization", "Bearer "+token)
	}
	if method == "POST" {
		r.Header.Set("Content-Type", "text/plain")
		r.Header.Set("X-Filename", "notes.txt")
	}
	return r
}
func perform(router http.Handler, r *http.Request) *httptest.ResponseRecorder {
	response := httptest.NewRecorder()
	router.ServeHTTP(response, r)
	return response
}
func assertHeaders(t *testing.T, response *httptest.ResponseRecorder) {
	t.Helper()
	if response.Header().Get("Cache-Control") != "no-store" || response.Header().Get("X-Content-Type-Options") != "nosniff" {
		t.Fatal("missing private download headers")
	}
	if len(response.Header().Values("Set-Cookie")) != 0 || response.Header().Get("Access-Control-Allow-Origin") != "" {
		t.Fatal("unexpected cookie or CORS grant")
	}
}
func assertJSON(t *testing.T, response *httptest.ResponseRecorder, status int, expected string) {
	t.Helper()
	assertHeaders(t, response)
	if response.Code != status {
		t.Fatalf("status %d expected %d: %s", response.Code, status, response.Body)
	}
	if response.Header().Get("Content-Type") != "application/json; charset=utf-8" {
		t.Fatal("response must be JSON")
	}
	var actualValue, expectedValue any
	if json.Unmarshal(response.Body.Bytes(), &actualValue) != nil || json.Unmarshal([]byte(expected), &expectedValue) != nil || !reflect.DeepEqual(actualValue, expectedValue) {
		t.Fatalf("response %s expected %s", response.Body, expected)
	}
	if status == 401 && response.Header().Get("WWW-Authenticate") != `Bearer realm="text-upload"` {
		t.Fatal("missing authentication challenge")
	}
}
func assertError(t *testing.T, response *httptest.ResponseRecorder, status int, code string) {
	t.Helper()
	assertJSON(t, response, status, `{"error":"`+code+`"}`)
}
func metadata(t *testing.T, response *httptest.ResponseRecorder, status int) Metadata {
	t.Helper()
	assertHeaders(t, response)
	if response.Code != status {
		t.Fatalf("unexpected status %d", response.Code)
	}
	var result Metadata
	if json.Unmarshal(response.Body.Bytes(), &result) != nil {
		t.Fatal("invalid metadata")
	}
	return result
}

type contractCase struct {
	ID                 string            `json:"id"`
	Method             string            `json:"method"`
	Path               string            `json:"path"`
	Status             int               `json:"status"`
	Expected           json.RawMessage   `json:"expected"`
	ExpectedBodyBase64 *string           `json:"expected_body_base64"`
	Raw                *string           `json:"raw"`
	BodyBase64         *string           `json:"body_base64"`
	Headers            map[string]string `json:"headers"`
	HeaderPairs        [][2]string       `json:"header_pairs"`
	ResponseHeaders    map[string]string `json:"response_headers"`
	RepeatBody         *struct {
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
		t.Fatal("invalid shared cases")
	}
	router := testRouter(t, Config{})
	for _, test := range cases {
		t.Run(test.ID, func(t *testing.T) {
			var body []byte
			switch {
			case test.Raw != nil:
				body = []byte(*test.Raw)
			case test.BodyBase64 != nil:
				var err error
				body, err = base64.StdEncoding.DecodeString(*test.BodyBase64)
				if err != nil {
					t.Fatal(err)
				}
			case test.RepeatBody != nil:
				body = []byte(strings.Repeat(test.RepeatBody.Character, test.RepeatBody.Count))
			}
			r := httptest.NewRequest(test.Method, test.Path, bytes.NewReader(body))
			for key, value := range test.Headers {
				r.Header.Set(key, value)
			}
			// Add retains each original header field, including empty/duplicate values.
			for _, pair := range test.HeaderPairs {
				r.Header.Add(pair[0], pair[1])
			}
			response := perform(router, r)
			if test.ExpectedBodyBase64 != nil {
				expected, err := base64.StdEncoding.DecodeString(*test.ExpectedBodyBase64)
				if err != nil {
					t.Fatal(err)
				}
				assertHeaders(t, response)
				if response.Code != test.Status || !bytes.Equal(response.Body.Bytes(), expected) {
					t.Fatal("download bytes/status differ")
				}
			} else {
				assertJSON(t, response, test.Status, string(test.Expected))
			}
			for key, value := range test.ResponseHeaders {
				if response.Header().Get(key) != value {
					t.Fatalf("header %s differs", key)
				}
			}
		})
	}
}

type unreadBody struct{ read atomic.Bool }

func (body *unreadBody) Read([]byte) (int, error) {
	body.read.Store(true)
	return 0, errors.New("body must not be read")
}
func TestAuthenticationQueryAndPermissionPrecedeBody(t *testing.T) {
	cases := []struct {
		name, token, path string
		status            int
		code              string
	}{
		{"missing", "", "/documents", 401, "authentication_required"},
		{"unknown", "unknown", "/documents", 401, "authentication_required"},
		{"expired", "lab-expired-session", "/documents", 401, "authentication_required"},
		{"revoked", "lab-revoked-session", "/documents", 401, "authentication_required"},
		{"readonly", "lab-alice-readonly-session", "/documents", 403, "forbidden"},
		{"query-before-permission", "lab-alice-readonly-session", "/documents?owner=bob", 422, "invalid_input"},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			body := &unreadBody{}
			r := request("POST", test.path, test.token, nil)
			r.Body = io.NopCloser(body)
			repository := &countingRepository{Repository: NewMemoryRepository()}
			assertError(t, perform(testRouter(t, Config{Repository: repository}), r), test.status, test.code)
			if body.read.Load() || repository.commits.Load() != 0 {
				t.Fatal("rejected request read body or reached Repository")
			}
		})
	}
}
func TestGETNeverReadsBody(t *testing.T) {
	router := testRouter(t, Config{})
	for _, path := range []string{"/health", "/documents", "/documents/missing", "/documents/missing/content"} {
		body := &unreadBody{}
		r := request("GET", path, "lab-alice-session", nil)
		r.Body = io.NopCloser(body)
		response := perform(router, r)
		assertHeaders(t, response)
		if body.read.Load() {
			t.Fatal("GET consumed body")
		}
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
	}{
		{"Authorization", "Bearer lab-alice-session", 400, "ambiguous_credentials"},
		{"Cookie", "__Host-lab_session=lab-alice-session", 400, "ambiguous_credentials"},
		{"Content-Type", "text/plain", 400, "ambiguous_upload_headers"},
		{"X-Filename", "notes.txt", 400, "ambiguous_upload_headers"},
	} {
		t.Run(test.header, func(t *testing.T) {
			r, _ := http.NewRequest("POST", server.URL+"/documents", strings.NewReader("public teaching text"))
			r.Header = request("POST", "/documents", "lab-alice-session", nil).Header
			if test.header == "Cookie" {
				r.Header.Del("Authorization")
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
			if response.StatusCode != test.status || string(body) != `{"error":"`+test.code+`"}` {
				t.Fatalf("duplicate header accepted: %d %s", response.StatusCode, body)
			}
		})
	}
}

type chunkedReader struct{ remaining, read int }

func (reader *chunkedReader) Read(destination []byte) (int, error) {
	if reader.remaining == 0 {
		return 0, io.EOF
	}
	count := min(len(destination), 7, reader.remaining)
	for index := 0; index < count; index++ {
		destination[index] = 'a'
	}
	reader.remaining -= count
	reader.read += count
	return count, nil
}
func TestChunkedBodyLimitAndFailedRead(t *testing.T) {
	repository := &countingRepository{Repository: NewMemoryRepository()}
	router := testRouter(t, Config{Repository: repository})
	for _, length := range []int64{-1, 1, 100000} {
		body := &chunkedReader{remaining: 100000}
		r := request("POST", "/documents", "lab-alice-session", nil)
		r.Body = io.NopCloser(body)
		r.ContentLength = length
		r.TransferEncoding = []string{"chunked"}
		r.Header.Set("Content-Type", "application/json")
		assertError(t, perform(router, r), 413, "request_too_large")
		if body.read != 4097 {
			t.Fatal("reader did not stop at 4097 bytes")
		}
	}
	body := &unreadBody{}
	r := request("POST", "/documents", "lab-alice-session", nil)
	r.Body = io.NopCloser(body)
	assertError(t, perform(router, r), 500, "request_failed")
	if repository.commits.Load() != 0 {
		t.Fatal("bad stream committed")
	}
}
func TestMediaFilenameAndTextPolicies(t *testing.T) {
	for _, media := range []string{"text/plain", " TEXT/PLAIN ; CHARSET = \"UTF-8\"\t", "text/plain;charset=utf-8"} {
		r := request("POST", "/documents", "lab-alice-session", []byte("public"))
		r.Header.Set("Content-Type", media)
		r.Header.Set("X-Filename", " \tReport.TXT\t")
		item := metadata(t, perform(testRouter(t, Config{}), r), 201)
		if item.Filename != "Report.TXT" || item.MediaType != "text/plain" {
			t.Fatal("canonical media or display filename changed")
		}
	}
	for _, media := range []string{"text/plain; charset=utf8", "text/plain; charset=utf-8; charset=utf-8", "text/plain; q=1", "text/plain,text/plain", "multipart/form-data", "text/markdown"} {
		r := request("POST", "/documents", "lab-alice-session", []byte("public"))
		r.Header.Set("Content-Type", media)
		status, code := 415, "unsupported_media_type"
		if media == "text/markdown" {
			status, code = 422, "invalid_filename"
		}
		assertError(t, perform(testRouter(t, Config{}), r), status, code)
	}
	for _, name := range []string{"../notes.txt", "notes.txt.exe", "a%2etxt", "文档.txt", "name space.txt", ".txt", strings.Repeat("x", 61) + ".txt", "C:notes.txt", "notes.txt\n"} {
		r := request("POST", "/documents", "lab-alice-session", []byte("public"))
		r.Header.Set("X-Filename", name)
		assertError(t, perform(testRouter(t, Config{}), r), 422, "invalid_filename")
	}
	for r := rune(0); r <= 0x9f; r++ {
		if (r <= 0x1f && r != '\t' && r != '\n' && r != '\r') || (r >= 0x7f && r <= 0x9f) {
			if validText([]byte("a" + string(r))) {
				t.Fatalf("forbidden control U+%04X accepted", r)
			}
		}
	}
	for _, text := range [][]byte{nil, []byte(" \t\r\n"), {0xff}, {0xc0, 0xaf}, []byte("a\ufeffb")} {
		if validText(text) {
			t.Fatal("invalid text accepted")
		}
	}
	if !validText([]byte("\u00a0")) {
		t.Fatal("policy only rejects ASCII whitespace-only text")
	}
}
func TestBytesAndAttachmentArePreserved(t *testing.T) {
	content := []byte("<script>alert(1)</script>\r\n[参考](https://example.test)\t你好")
	router := testRouter(t, Config{})
	r := request("POST", "/documents", "lab-alice-session", content)
	r.Header.Set("Content-Type", "text/markdown; charset=UTF-8")
	r.Header.Set("X-Filename", "notes.MD")
	item := metadata(t, perform(router, r), 201)
	digest := sha256.Sum256(content)
	if item.SizeBytes != len(content) || item.SHA256 != hex.EncodeToString(digest[:]) {
		t.Fatal("metadata is not based on original bytes")
	}
	response := perform(router, request("GET", "/documents/"+item.ID+"/content", "lab-alice-session", nil))
	assertHeaders(t, response)
	if !bytes.Equal(response.Body.Bytes(), content) || response.Header().Get("Content-Type") != "application/octet-stream" || response.Header().Get("Content-Disposition") != `attachment; filename="upload-doc-000001.md"` {
		t.Fatal("private attachment changed bytes or filename")
	}
	assertError(t, perform(router, request("GET", "/documents/"+item.ID+"/content", "lab-bob-session", nil)), 404, "document_not_found")
}
func TestExactLimitUsesBytes(t *testing.T) {
	router := testRouter(t, Config{})
	item := metadata(t, perform(router, request("POST", "/documents", "lab-alice-session", []byte(strings.Repeat("界", 1365)+"a"))), 201)
	if item.SizeBytes != 4096 {
		t.Fatal("4096-byte upload failed")
	}
	assertError(t, perform(router, request("POST", "/documents", "lab-alice-session", []byte(strings.Repeat("界", 1365)+"ab"))), 413, "request_too_large")
}

type countingRepository struct {
	Repository
	commits atomic.Int32
}

func (repository *countingRepository) Commit(owner, filename, media string, content []byte) (*Metadata, error) {
	repository.commits.Add(1)
	return repository.Repository.Commit(owner, filename, media, content)
}

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
func awaitResponse(t *testing.T, result <-chan *httptest.ResponseRecorder) *httptest.ResponseRecorder {
	t.Helper()
	select {
	case response := <-result:
		return response
	case <-time.After(2 * time.Second):
		t.Fatal("controlled request did not finish")
		return nil
	}
}
func TestAuthorizationIsRecheckedAfterPausedBody(t *testing.T) {
	for _, change := range []string{"revoked", "expired", "readonly"} {
		t.Run(change, func(t *testing.T) {
			var now atomic.Int64
			now.Store(time.Date(2026, 10, 4, 0, 0, 0, 0, time.UTC).UnixNano())
			clock := func() time.Time { return time.Unix(0, now.Load()) }
			sessions, err := LoadSessions()
			if err != nil {
				t.Fatal(err)
			}
			store := NewMemorySessionStore(sessions, clock())
			repository := &countingRepository{Repository: NewMemoryRepository()}
			router := testRouter(t, Config{Clock: clock, Sessions: store, Repository: repository})
			body := &pausedBody{data: []byte("public"), entered: make(chan struct{}), release: make(chan struct{})}
			var once sync.Once
			release := func() { once.Do(func() { close(body.release) }) }
			t.Cleanup(release)
			r := request("POST", "/documents", "lab-alice-session", nil)
			r.Body = io.NopCloser(body)
			r.ContentLength = -1
			result := make(chan *httptest.ResponseRecorder, 1)
			go func() { result <- perform(router, r) }()
			select {
			case <-body.entered:
			case <-time.After(2 * time.Second):
				t.Fatal("body did not pause")
			}
			changed := make(chan struct{})
			go func() {
				switch change {
				case "revoked":
					_ = store.Revoke("lab-alice-session")
				case "expired":
					now.Add(int64(time.Hour))
				case "readonly":
					store.SetWritePermission("lab-alice-session", false)
				}
				close(changed)
			}()
			select {
			case <-changed:
			case <-time.After(2 * time.Second):
				t.Fatal("body read held the session lock")
			}
			// A concurrent ordinary read remains responsive while the upload is paused.
			inspected := make(chan *httptest.ResponseRecorder, 1)
			go func() { inspected <- perform(router, request("GET", "/documents", "lab-bob-session", nil)) }()
			read := awaitResponse(t, inspected)
			if change == "expired" {
				assertError(t, read, 401, "authentication_required")
			} else {
				assertJSON(t, read, 200, `{"documents":[]}`)
			}
			release()
			status, code := 401, "authentication_required"
			if change == "readonly" {
				status, code = 403, "forbidden"
			}
			assertError(t, awaitResponse(t, result), status, code)
			if repository.commits.Load() != 0 {
				t.Fatal("stale authorization reached commit")
			}
		})
	}
}
func TestRepositoryFailurePreservesRowsUsageAndIDs(t *testing.T) {
	for _, panicFailure := range []bool{false, true} {
		repository := NewMemoryRepository()
		first, err := repository.Commit("alice", "first.txt", "text/plain", []byte("base"))
		if err != nil {
			t.Fatal(err)
		}
		repository.beforePublish = func() error {
			if panicFailure {
				panic("private transaction diagnostic")
			}
			return errors.New("private transaction diagnostic")
		}
		router := testRouter(t, Config{Repository: repository})
		assertError(t, perform(router, request("POST", "/documents", "lab-alice-session", []byte("new"))), 503, "repository_unavailable")
		documents, _ := repository.List("alice")
		stored, _ := repository.Content("alice", first.ID)
		if len(documents) != 1 || string(stored.Content) != "base" || repository.ownerUsage["alice"] != (usage{count: 1, bytes: 4}) || repository.nextID != 2 {
			t.Fatal("failure changed rows, content, usage or next ID")
		}
		repository.beforePublish = nil
		next := metadata(t, perform(router, request("POST", "/documents", "lab-alice-session", []byte("new"))), 201)
		if next.ID != "doc-000002" {
			t.Fatal("failed transaction consumed ID")
		}
	}
}

type failingRepository struct {
	Repository
	panicFailure bool
}

func (repository failingRepository) fail() error {
	if repository.panicFailure {
		panic("private repository path")
	}
	return errors.New("private repository path")
}
func (repository failingRepository) List(string) ([]Metadata, error) { return nil, repository.fail() }
func (repository failingRepository) Find(string, string) (*Metadata, error) {
	return nil, repository.fail()
}
func (repository failingRepository) Content(string, string) (*Download, error) {
	return nil, repository.fail()
}
func TestRepositoryReadFailuresAreOpaque(t *testing.T) {
	for _, panicFailure := range []bool{false, true} {
		router := testRouter(t, Config{Repository: failingRepository{Repository: NewMemoryRepository(), panicFailure: panicFailure}})
		for _, path := range []string{"/documents", "/documents/doc-000001", "/documents/doc-000001/content"} {
			assertError(t, perform(router, request("GET", path, "lab-alice-session", nil)), 503, "repository_unavailable")
		}
	}
}

type faultySessionStore struct {
	SessionStore
	calls        int
	failAt       int
	panicFailure bool
}

func (store *faultySessionStore) Resolve(token string, now time.Time) (*Principal, error) {
	store.calls++
	if store.calls == store.failAt {
		if store.panicFailure {
			panic("private session diagnostic")
		}
		return nil, errors.New("private session diagnostic")
	}
	return store.SessionStore.Resolve(token, now)
}
func TestSessionFailuresBeforeAndAfterBodyAreOpaque(t *testing.T) {
	sessions, err := LoadSessions()
	if err != nil {
		t.Fatal(err)
	}
	for _, failAt := range []int{1, 2} {
		for _, panicFailure := range []bool{false, true} {
			store := &faultySessionStore{SessionStore: NewMemorySessionStore(sessions, time.Now()), failAt: failAt, panicFailure: panicFailure}
			repository := &countingRepository{Repository: NewMemoryRepository()}
			assertError(t, perform(testRouter(t, Config{Sessions: store, Repository: repository}), request("POST", "/documents", "lab-alice-session", []byte("public"))), 503, "session_store_unavailable")
			if repository.commits.Load() != 0 {
				t.Fatal("failed session committed data")
			}
		}
	}
}

type commitBarrier struct {
	Repository
	arrived chan struct{}
	release chan struct{}
}

func (repository *commitBarrier) Commit(owner, filename, media string, content []byte) (*Metadata, error) {
	repository.arrived <- struct{}{}
	<-repository.release
	return repository.Repository.Commit(owner, filename, media, content)
}
func TestConcurrentLastSlotAndLastByteBudget(t *testing.T) {
	for _, dimension := range []string{"count", "bytes"} {
		t.Run(dimension, func(t *testing.T) {
			repository := NewMemoryRepository()
			body := []byte("a")
			seeds := 2
			if dimension == "bytes" {
				body = []byte(strings.Repeat("a", 4096))
				seeds = 1
			}
			for range seeds {
				if _, err := repository.Commit("alice", "same.txt", "text/plain", body); err != nil {
					t.Fatal(err)
				}
			}
			barrier := &commitBarrier{Repository: repository, arrived: make(chan struct{}, 2), release: make(chan struct{})}
			router := testRouter(t, Config{Repository: barrier})
			result := make(chan *httptest.ResponseRecorder, 2)
			for range 2 {
				go func() { result <- perform(router, request("POST", "/documents", "lab-alice-session", body)) }()
			}
			var once sync.Once
			release := func() { once.Do(func() { close(barrier.release) }) }
			t.Cleanup(release)
			for range 2 {
				select {
				case <-barrier.arrived:
				case <-time.After(2 * time.Second):
					t.Fatal("uploads did not both reach commit barrier")
				}
			}
			release()
			success, rejected := 0, 0
			for range 2 {
				response := awaitResponse(t, result)
				if response.Code == 201 {
					success++
					metadata(t, response, 201)
				} else {
					assertError(t, response, 409, "quota_exceeded")
					rejected++
				}
			}
			documents, _ := repository.List("alice")
			expectedBytes := 3
			if dimension == "bytes" {
				expectedBytes = 8192
			}
			if success != 1 || rejected != 1 || len(documents) != seeds+1 || repository.ownerUsage["alice"] != (usage{count: seeds + 1, bytes: expectedBytes}) || repository.nextID != seeds+2 {
				t.Fatal("atomic quota violated or failed commit consumed ID")
			}
		})
	}
}
func TestCopiesAndSameNamesDoNotOverwrite(t *testing.T) {
	repository := NewMemoryRepository()
	input := []byte("original")
	first, err := repository.Commit("alice", "same.txt", "text/plain", input)
	if err != nil {
		t.Fatal(err)
	}
	firstID := first.ID
	input[0] = 'X'
	first.Filename = "mutated.txt"
	first.ID = "mutated"
	listing, _ := repository.List("alice")
	listing[0].Filename = "mutated-list.txt"
	found, _ := repository.Find("alice", firstID)
	found.Filename = "mutated-find.txt"
	download, _ := repository.Content("alice", firstID)
	download.Content[0] = 'Y'
	download.Metadata.Filename = "mutated-download.txt"
	second, err := repository.Commit("alice", "same.txt", "text/plain", []byte("second"))
	if err != nil {
		t.Fatal(err)
	}
	stored, _ := repository.Content("alice", firstID)
	items, _ := repository.List("alice")
	if firstID == second.ID || len(items) != 2 || stored.Metadata.Filename != "same.txt" || string(stored.Content) != "original" {
		t.Fatal("caller mutation or repeated name changed stored object")
	}
	if other, _ := repository.Find("bob", firstID); other != nil {
		t.Fatal("metadata escaped owner scope")
	}
	if other, _ := repository.Content("bob", firstID); other != nil {
		t.Fatal("bytes escaped owner scope")
	}
}
func TestApplicationRestartAndUnexpectedFailure(t *testing.T) {
	router := testRouter(t, Config{})
	metadata(t, perform(router, request("POST", "/documents", "lab-alice-session", []byte("public"))), 201)
	assertJSON(t, perform(testRouter(t, Config{}), request("GET", "/documents", "lab-alice-session", nil)), 200, `{"documents":[]}`)
	sessions, _ := LoadSessions()
	router = testRouter(t, Config{Sessions: NewMemorySessionStore(sessions, time.Now()), Clock: func() time.Time { panic("private internal clock") }})
	assertError(t, perform(router, request("GET", "/documents", "lab-alice-session", nil)), 500, "request_failed")
}
func TestRoutesAndLoopbackAddress(t *testing.T) {
	router := testRouter(t, Config{})
	for _, test := range []struct {
		method, path string
		status       int
		code         string
	}{{"GET", "/missing", 404, "route_not_found"}, {"POST", "/documents/", 404, "route_not_found"}, {"PATCH", "/documents", 405, "method_not_allowed"}, {"POST", "/health", 405, "method_not_allowed"}} {
		assertError(t, perform(router, request(test.method, test.path, "", nil)), test.status, test.code)
	}
	for _, port := range []string{"0", "65536", "-1", "all", "127.0.0.1:8023", " 8023"} {
		if _, err := listenAddress(port); err == nil {
			t.Fatal("invalid PORT accepted")
		}
	}
	if address, err := listenAddress(""); err != nil || address != "127.0.0.1:8023" {
		t.Fatal("default listener must use loopback")
	}
	if address, err := listenAddress("12345"); err != nil || address != "127.0.0.1:12345" {
		t.Fatal("PORT override failed")
	}
}

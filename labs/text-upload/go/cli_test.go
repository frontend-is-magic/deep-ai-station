package main

import (
	"bufio"
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

// This entry point exists only in the native test binary. The public CLI has no
// fault option, executable selection, path override, or environment switch.
func TestSQLiteNativeChild(t *testing.T) {
	mode := os.Getenv("UPLOAD_NATIVE_CHILD")
	if mode == "" {
		t.Skip("fixed helper is invoked by process tests")
	}
	if mode != "serve" && mode != "before-commit" && mode != "after-commit" {
		t.Fatal("invalid test mode")
	}
	repository, err := NewSQLiteRepository(storageRelativePath)
	if err != nil {
		t.Fatal("test database unavailable")
	}
	if mode == "before-commit" {
		repository.hooks.afterInsert = func(*sql.Conn) error { os.Exit(72); return nil }
	}
	if mode == "after-commit" {
		repository.hooks.afterCommit = func() error { os.Exit(73); return nil }
	}
	server := httptest.NewServer(testRouter(t, Config{Repository: repository}))
	defer server.Close()
	if _, err := io.WriteString(os.Stdout, server.URL+"\n"); err != nil {
		t.Fatal("test readiness unavailable")
	}
	_, _ = io.Copy(io.Discard, os.Stdin)
}

type uploadChild struct {
	command *exec.Cmd
	input   io.WriteCloser
	url     string
	stderr  bytes.Buffer
	wait    chan error
	once    sync.Once
	err     error
}

func processAssets(t *testing.T, directory string) {
	t.Helper()
	for _, name := range []string{"fixtures.json", "schema.sql"} {
		data, err := readLabData(name)
		if err != nil {
			t.Fatal(err)
		}
		if err = os.WriteFile(filepath.Join(directory, name), data, 0600); err != nil {
			t.Fatal(err)
		}
	}
}
func startUploadChild(t *testing.T, directory, mode string) *uploadChild {
	t.Helper()
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	child := &uploadChild{command: exec.Command(executable, "-test.run=^TestSQLiteNativeChild$"), wait: make(chan error, 1)}
	child.command.Dir = directory
	child.command.Env = []string{"UPLOAD_NATIVE_CHILD=" + mode, "TZ=UTC", "LANG=C", "GOTRACEBACK=none"}
	child.command.Stderr = &child.stderr
	child.input, err = child.command.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	output, err := child.command.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err = child.command.Start(); err != nil {
		t.Fatal(err)
	}
	go func() { child.wait <- child.command.Wait() }()
	t.Cleanup(func() { child.finish(t, false) })
	ready := make(chan string, 1)
	go func() {
		reader := bufio.NewReader(output)
		line, _ := reader.ReadString('\n')
		ready <- strings.TrimSpace(line)
		_, _ = io.Copy(io.Discard, reader)
	}()
	select {
	case child.url = <-ready:
		if !strings.HasPrefix(child.url, "http://127.0.0.1:") {
			t.Fatal("child did not become ready")
		}
	case <-time.After(10 * time.Second):
		t.Fatal("child readiness exceeded deadline")
	}
	return child
}
func (child *uploadChild) finish(t *testing.T, crashed bool) error {
	t.Helper()
	child.once.Do(func() {
		_ = child.input.Close()
		select {
		case child.err = <-child.wait:
		case <-time.After(5 * time.Second):
			_ = child.command.Process.Kill()
			child.err = <-child.wait
			t.Error("owned child required forced cleanup")
		}
		if child.command.ProcessState == nil {
			t.Error("owned child was not reaped")
		}
		if !crashed && child.err != nil {
			t.Errorf("child failed: %v", child.err)
		}
		if child.stderr.Len() != 0 {
			t.Error("child emitted unexpected diagnostics")
		}
	})
	return child.err
}
func processRequest(endpoint, method, path string, content []byte) (int, []byte, error) {
	request, err := http.NewRequest(method, endpoint+path, bytes.NewReader(content))
	if err != nil {
		return 0, nil, err
	}
	request.Header.Set("Authorization", "Bearer lab-alice-session")
	if method == "POST" {
		request.Header.Set("Content-Type", "text/plain")
		request.Header.Set("X-Filename", "notes.txt")
	}
	client := &http.Client{Timeout: 5 * time.Second}
	defer client.CloseIdleConnections()
	response, err := client.Do(request)
	if err != nil {
		return 0, nil, err
	}
	defer response.Body.Close()
	body, err := io.ReadAll(response.Body)
	if response.Header.Get("Cache-Control") != "no-store" || response.Header.Get("X-Content-Type-Options") != "nosniff" {
		return 0, nil, errors.New("missing fixed headers")
	}
	return response.StatusCode, body, err
}
func TestSQLiteIndependentProcessRestart(t *testing.T) {
	_, path := newSQLite(t)
	directory := filepath.Dir(filepath.Dir(path))
	processAssets(t, directory)
	first := startUploadChild(t, directory, "serve")
	content := []byte("中文\r\n😀e\u0301\n")
	status, body, err := processRequest(first.url, "POST", "/documents", content)
	var saved Metadata
	if err != nil || status != 201 || json.Unmarshal(body, &saved) != nil || saved.ID != "doc-000001" {
		t.Fatalf("first process upload failed: %d %s %v", status, body, err)
	}
	first.finish(t, false)
	second := startUploadChild(t, directory, "serve")
	status, body, err = processRequest(second.url, "GET", "/documents/"+saved.ID+"/content", nil)
	if err != nil || status != 200 || !bytes.Equal(body, content) {
		t.Fatalf("second process failed exact byte read: %d %s %v", status, body, err)
	}
	status, body, err = processRequest(second.url, "GET", "/documents/"+saved.ID, nil)
	var restored Metadata
	if err != nil || status != 200 || json.Unmarshal(body, &restored) != nil || restored != saved {
		t.Fatal("second process changed metadata")
	}
	second.finish(t, false)
	if count, next := rowCount(t, path); count != 1 || next != 2 {
		t.Fatal("restart changed storage")
	}
}
func TestSQLiteProcessCrashBeforeAndAfterCommit(t *testing.T) {
	for _, mode := range []string{"before-commit", "after-commit"} {
		t.Run(mode, func(t *testing.T) {
			_, path := newSQLite(t)
			directory := filepath.Dir(filepath.Dir(path))
			processAssets(t, directory)
			child := startUploadChild(t, directory, mode)
			_, _, err := processRequest(child.url, "POST", "/documents", []byte("saved before lost response"))
			if err == nil {
				t.Fatal("crash unexpectedly returned HTTP success")
			}
			child.finish(t, true)
			wantExit, wantCount := 72, int64(0)
			if mode == "after-commit" {
				wantExit, wantCount = 73, 1
			}
			if child.command.ProcessState.ExitCode() != wantExit {
				t.Fatal("wrong fixed process fault")
			}
			restarted := startUploadChild(t, directory, "serve")
			status, body, err := processRequest(restarted.url, "GET", "/documents", nil)
			var result struct {
				Items []Metadata `json:"documents"`
			}
			if err != nil || status != 200 || json.Unmarshal(body, &result) != nil || int64(len(result.Items)) != wantCount {
				t.Fatalf("crash recovery wrong: %d %s %v", status, body, err)
			}
			restarted.finish(t, false)
			if count, next := rowCount(t, path); count != wantCount || next != wantCount+1 {
				t.Fatal("crash left inconsistent rows or sequence")
			}
		})
	}
}
func TestSQLiteTwoIndependentProcessesCompeteForQuota(t *testing.T) {
	for _, limit := range []string{"count", "bytes"} {
		t.Run(limit, func(t *testing.T) {
			repository, path := newSQLite(t)
			seed, uploads, expected := []byte("first"), 2, int64(3)
			payload := []byte("last")
			if limit == "bytes" {
				seed, uploads, expected = bytes.Repeat([]byte("x"), 4096), 1, 2
				payload = bytes.Repeat([]byte("y"), 4096)
			}
			for index := 0; index < uploads; index++ {
				if _, err := repository.Commit("alice", "notes.txt", "text/plain", seed, allowWrite); err != nil {
					t.Fatal(err)
				}
			}
			directory := filepath.Dir(filepath.Dir(path))
			processAssets(t, directory)
			first, second := startUploadChild(t, directory, "serve"), startUploadChild(t, directory, "serve")
			type result struct {
				status int
				body   []byte
				err    error
			}
			ready, results := make(chan struct{}), make(chan result, 2)
			for _, child := range []*uploadChild{first, second} {
				go func(endpoint string) {
					<-ready
					status, body, err := processRequest(endpoint, "POST", "/documents", payload)
					results <- result{status, body, err}
				}(child.url)
			}
			close(ready)
			successes := 0
			for range 2 {
				result := <-results
				if result.err != nil {
					t.Fatal(result.err)
				}
				if result.status == 201 {
					successes++
					continue
				}
				if result.status == 409 && string(result.body) == `{"error":"quota_exceeded"}` {
					continue
				}
				if result.status == 503 && string(result.body) == `{"error":"repository_unavailable"}` {
					continue
				}
				t.Fatalf("unexpected competing result %d %s", result.status, result.body)
			}
			first.finish(t, false)
			second.finish(t, false)
			if count, next := rowCount(t, path); successes != 1 || count != expected || next != expected+1 {
				t.Fatal("competition exceeded quota or consumed an extra id")
			}
		})
	}
}
func TestStorageCLIExactArgumentsAndNoFilesystemEffects(t *testing.T) {
	directory := t.TempDir()
	t.Chdir(directory)
	for _, args := range [][]string{{"serve"}, {"init", "--help"}, {"--help", "init"}, {"serve", "--storage=sqlite"}, {"serve", "--storage", "memory"}, {"serve", "--storage", "sqlite", "--storage", "sqlite"}, {"init", "--db", "private-path"}} {
		var output bytes.Buffer
		if code := runCLI(args, &output); code != 1 || output.String() != "{\"error\":\"invalid_input\"}\n" {
			t.Fatalf("unexpected CLI error for %v", args)
		}
	}
	var output bytes.Buffer
	if runCLI([]string{"--help"}, &output) != 0 || !strings.Contains(output.String(), "memory") {
		t.Fatal("help unavailable")
	}
	if _, err := os.Stat(".data"); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("invalid arguments or help touched storage")
	}
	if mode, err := cliMode(nil); mode != "memory" || err != nil {
		t.Fatal("default mode changed")
	}
}
func TestStorageCLIInitIsExclusiveAndMissingServeDoesNotCreate(t *testing.T) {
	directory := t.TempDir()
	processAssets(t, directory)
	t.Chdir(directory)
	t.Setenv("PORT", "0")
	var output bytes.Buffer
	if runCLI([]string{"init"}, &output) != 0 || output.String() != "{\"schema_version\":1,\"storage_contract\":\"text-upload-sqlite-v1\"}\n" {
		t.Fatalf("init reads PORT or failed: %s", &output)
	}
	before, _ := os.ReadFile(storageRelativePath)
	output.Reset()
	if runCLI([]string{"init"}, &output) != 1 || output.String() != "{\"error\":\"database_exists\"}\n" {
		t.Fatal("init overwrote existing DB")
	}
	after, _ := os.ReadFile(storageRelativePath)
	if !bytes.Equal(before, after) {
		t.Fatal("repeated init changed DB")
	}
	if err := os.Remove(storageRelativePath); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PORT", "8023")
	output.Reset()
	if runCLI([]string{"serve", "--storage", "sqlite"}, &output) != 1 || output.String() != "{\"error\":\"database_missing\"}\n" {
		t.Fatalf("missing DB classification: %s", &output)
	}
	if _, err := os.Stat(storageRelativePath); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("serve created missing database")
	}
}
func TestStorageRejectsExistingEntriesAndSymlinks(t *testing.T) {
	for _, entry := range []string{"empty", "directory", "symlink"} {
		t.Run(entry, func(t *testing.T) {
			directory := t.TempDir()
			if err := os.Mkdir(filepath.Join(directory, ".data"), 0700); err != nil {
				t.Fatal(err)
			}
			path := filepath.Join(directory, storageRelativePath)
			var err error
			switch entry {
			case "empty":
				err = os.WriteFile(path, nil, 0600)
			case "directory":
				err = os.Mkdir(path, 0700)
			case "symlink":
				err = os.Symlink(filepath.Join(directory, "absent"), path)
			}
			if err != nil {
				t.Fatal(err)
			}
			if !errors.Is(InitializeSQLite(path), errDatabaseExists) {
				t.Fatal("init accepted an existing entry")
			}
		})
	}
	directory, target := t.TempDir(), t.TempDir()
	if err := os.Symlink(target, filepath.Join(directory, ".data")); err != nil {
		t.Fatal(err)
	}
	if !errors.Is(InitializeSQLite(filepath.Join(directory, storageRelativePath)), errRepository) {
		t.Fatal("accepted symlink directory")
	}
	if files, _ := os.ReadDir(target); len(files) != 0 {
		t.Fatal("wrote through symlink")
	}
}

type brokenCLIWriter struct{}

func (brokenCLIWriter) Write([]byte) (int, error) { return 0, io.ErrClosedPipe }
func TestCLIClosedOutputIsBounded(t *testing.T) {
	if runCLI([]string{"invalid"}, brokenCLIWriter{}) != 1 || runCLI([]string{"--help"}, brokenCLIWriter{}) != 1 {
		t.Fatal("closed output did not fail")
	}
}

func TestSQLiteConnectionHasZeroBusyTimeout(t *testing.T) {
	_, path := newSQLite(t)
	db, connection, err := openSQLite(path)
	if err != nil {
		t.Fatal(err)
	}
	defer closeSQLite(db, connection)
	var timeout int64
	if connection.QueryRowContext(context.Background(), "PRAGMA busy_timeout").Scan(&timeout) != nil || timeout != 0 {
		t.Fatal("nonzero busy timeout")
	}
}

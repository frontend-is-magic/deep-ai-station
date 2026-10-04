package main

import (
	"bytes"
	"context"
	"database/sql"
	"errors"
	"fmt"
	"io"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

func newSQLite(t *testing.T) (*SQLiteRepository, string) {
	t.Helper()
	path := filepath.Join(t.TempDir(), ".data", "uploads.sqlite3")
	if err := InitializeSQLite(path); err != nil {
		t.Fatal(err)
	}
	repository, err := NewSQLiteRepository(path)
	if err != nil {
		t.Fatal(err)
	}
	return repository, path
}
func storageSQL(t *testing.T, path string, statements ...string) {
	t.Helper()
	db, connection, err := openSQLite(path)
	if err != nil {
		t.Fatal(err)
	}
	defer closeSQLite(db, connection)
	for _, statement := range statements {
		if _, err := connection.ExecContext(context.Background(), statement); err != nil {
			t.Fatal(err)
		}
	}
}
func rowCount(t *testing.T, path string) (int64, int64) {
	t.Helper()
	db, connection, err := openSQLite(path)
	if err != nil {
		t.Fatal(err)
	}
	defer closeSQLite(db, connection)
	var count, next int64
	if connection.QueryRowContext(context.Background(), "SELECT COUNT(*) FROM documents").Scan(&count) != nil || connection.QueryRowContext(context.Background(), "SELECT next_id FROM storage_meta").Scan(&next) != nil {
		t.Fatal("could not inspect count")
	}
	return count, next
}
func TestSQLiteSharedContract(t *testing.T) {
	repository, _ := newSQLite(t)
	runSharedContract(t, Config{Repository: repository})
}
func TestSQLiteRestartCopiesAndOwnerScope(t *testing.T) {
	repository, path := newSQLite(t)
	original := []byte("中文\r\n😀e\u0301\n")
	first, err := repository.Commit("alice", "same.txt", "text/plain", original, allowWrite)
	if err != nil {
		t.Fatal(err)
	}
	original[0] = 'x'
	restarted, err := NewSQLiteRepository(path)
	if err != nil {
		t.Fatal(err)
	}
	saved, err := restarted.Content("alice", first.ID)
	if err != nil || saved == nil || saved.Metadata != *first || string(saved.Content) != "中文\r\n😀e\u0301\n" {
		t.Fatalf("restart lost content: %v", err)
	}
	saved.Content[0] = 'x'
	saved.Metadata.Filename = "changed.txt"
	again, err := restarted.Content("alice", first.ID)
	if err != nil || string(again.Content) != "中文\r\n😀e\u0301\n" || again.Metadata.Filename != "same.txt" {
		t.Fatal("caller mutated storage")
	}
	for _, id := range []string{first.ID, "doc-1", "doc-000001\n", "doc-999999"} {
		if value, err := restarted.Content("bob", id); err != nil || value != nil {
			t.Fatal("cross owner data")
		}
	}
	second, err := restarted.Commit("alice", "same.txt", "text/plain", []byte("second"), allowWrite)
	if err != nil || second.ID != "doc-000002" {
		t.Fatal("persistent sequence or duplicate filename incorrect")
	}
	items, err := restarted.List("alice")
	if err != nil || len(items) != 2 || items[0].ID != first.ID {
		t.Fatal("wrong sorted list")
	}
}
func TestSQLiteFailpointsRollbackAndUnconfirmed(t *testing.T) {
	for _, stage := range []string{"before-auth", "after-insert", "panic-after-insert", "commit-before", "commit-after", "after-commit", "panic-after-commit"} {
		t.Run(stage, func(t *testing.T) {
			repository, path := newSQLite(t)
			calls := 0
			failure := errors.New("private-path-session-sql")
			switch stage {
			case "before-auth":
				repository.hooks.beforeAuthorize = func() error { return failure }
			case "after-insert":
				repository.hooks.afterInsert = func(*sql.Conn) error { return failure }
			case "panic-after-insert":
				repository.hooks.afterInsert = func(*sql.Conn) error { panic("private-path-session-sql") }
			case "commit-before":
				repository.hooks.commit = func(*sql.Conn) error { calls++; return failure }
			case "commit-after":
				repository.hooks.commit = func(c *sql.Conn) error {
					calls++
					if _, err := c.ExecContext(context.Background(), "COMMIT"); err != nil {
						t.Fatal(err)
					}
					return failure
				}
			case "after-commit":
				repository.hooks.afterCommit = func() error { return failure }
			case "panic-after-commit":
				repository.hooks.afterCommit = func() error { panic("private-path-session-sql") }
			}
			response := perform(testRouter(t, Config{Repository: repository}), request("POST", "/documents", "lab-alice-session", []byte("body")))
			unconfirmed := strings.Contains(stage, "commit")
			code := "repository_unavailable"
			if unconfirmed {
				code = "result_unconfirmed"
			}
			assertError(t, response, 503, code)
			committed := stage == "commit-after" || stage == "after-commit" || stage == "panic-after-commit"
			want := int64(0)
			if committed {
				want = 1
			}
			count, next := rowCount(t, path)
			if count != want || next != want+1 {
				t.Fatal("failed transaction left incorrect count or sequence")
			}
			if strings.HasPrefix(stage, "commit-") && calls != 1 {
				t.Fatal("commit was retried")
			}
			resumed, err := NewSQLiteRepository(path)
			if err != nil {
				t.Fatal(err)
			}
			item, err := resumed.Commit("alice", "retry.txt", "text/plain", []byte("new explicit request"), allowWrite)
			if err != nil || item.ID != fmt.Sprintf("doc-%06d", want+1) {
				t.Fatal("lock leaked or explicit request incorrectly deduplicated")
			}
		})
	}
}
func TestSQLiteBeginAndCommitBusyAreDifferent(t *testing.T) {
	for _, stage := range []string{"begin", "commit"} {
		t.Run(stage, func(t *testing.T) {
			repository, path := newSQLite(t)
			db, reader, err := openSQLite(path)
			if err != nil {
				t.Fatal(err)
			}
			defer closeSQLite(db, reader)
			begin := "BEGIN"
			if stage == "begin" {
				begin = "BEGIN IMMEDIATE"
			}
			if _, err := reader.ExecContext(context.Background(), begin); err != nil {
				t.Fatal(err)
			}
			defer reader.ExecContext(context.Background(), "ROLLBACK")
			var ignored int
			if reader.QueryRowContext(context.Background(), "SELECT COUNT(*) FROM documents").Scan(&ignored) != nil {
				t.Fatal("failed lock setup")
			}
			start := time.Now()
			_, err = repository.Commit("alice", "x.txt", "text/plain", []byte("x"), allowWrite)
			if err == nil || time.Since(start) > time.Second {
				t.Fatal("busy did not fail without waiting")
			}
			if errors.Is(err, ErrResultUnconfirmed) != (stage == "commit") {
				t.Fatalf("wrong uncertainty: %v", err)
			}
			if _, err := reader.ExecContext(context.Background(), "ROLLBACK"); err != nil {
				t.Fatal(err)
			}
			count, next := rowCount(t, path)
			if count != 0 || next != 1 {
				t.Fatal("busy attempt persisted")
			}
			if _, err := repository.Commit("alice", "x.txt", "text/plain", []byte("x"), allowWrite); err != nil {
				t.Fatal("busy connection not released", err)
			}
		})
	}
}
func TestSQLiteConcurrentQuota(t *testing.T) {
	for _, dimension := range []string{"count", "bytes"} {
		t.Run(dimension, func(t *testing.T) {
			repository, path := newSQLite(t)
			body := []byte("x")
			seeds := 2
			if dimension == "bytes" {
				body = bytes.Repeat([]byte("x"), 4096)
				seeds = 1
			}
			for i := 0; i < seeds; i++ {
				if _, err := repository.Commit("alice", "x.txt", "text/plain", body, allowWrite); err != nil {
					t.Fatal(err)
				}
			}
			other, err := NewSQLiteRepository(path)
			if err != nil {
				t.Fatal(err)
			}
			gate := make(chan struct{})
			results := make(chan error, 2)
			for _, repo := range []*SQLiteRepository{repository, other} {
				go func(repo *SQLiteRepository) {
					<-gate
					_, err := repo.Commit("alice", "x.txt", "text/plain", body, allowWrite)
					results <- err
				}(repo)
			}
			close(gate)
			passed := 0
			for i := 0; i < 2; i++ {
				select {
				case err := <-results:
					if err == nil {
						passed++
					} else if !errors.Is(err, ErrQuotaExceeded) && !errors.Is(err, errRepository) && !errors.Is(err, ErrResultUnconfirmed) {
						t.Fatal(err)
					}
				case <-time.After(2 * time.Second):
					t.Fatal("write hung")
				}
			}
			if passed != 1 {
				t.Fatalf("success count %d", passed)
			}
			count, next := rowCount(t, path)
			if count != int64(seeds+1) || next != count+1 {
				t.Fatal("quota or ID overrun")
			}
		})
	}
}
func TestSQLiteCorruptStorageIsRejectedWithoutWrite(t *testing.T) {
	cases := map[string][]string{
		"future": {"PRAGMA user_version=2"}, "legacy": {"PRAGMA user_version=0"}, "foreign": {"PRAGMA application_id=1"},
		"extra-table": {"CREATE TABLE private_data(value TEXT)"}, "extra-trigger": {"CREATE TRIGGER extra AFTER INSERT ON documents BEGIN SELECT 1; END"},
		"extra-index": {"CREATE INDEX extra ON documents(filename)"}, "mode": {"PRAGMA journal_mode=WAL"},
		"meta-version":    {"PRAGMA ignore_check_constraints=ON", "UPDATE storage_meta SET storage_contract='future'"},
		"meta-type":       {"PRAGMA ignore_check_constraints=ON", "UPDATE storage_meta SET next_id='wrong'"},
		"meta-giant-text": {"PRAGMA ignore_check_constraints=ON", "UPDATE storage_meta SET next_id=CAST(zeroblob(1000000) AS TEXT)"},
		"size-giant-text": {"PRAGMA ignore_check_constraints=ON", "UPDATE documents SET size_bytes=CAST(zeroblob(1000000) AS TEXT)"},
		"sequence":        {"UPDATE storage_meta SET next_id=8"}, "sha": {"UPDATE documents SET sha256=printf('%064d',0)"},
		"utf8-filename":    {"PRAGMA ignore_check_constraints=ON", "UPDATE documents SET filename=CAST(X'80' AS TEXT)"},
		"invalid-text":     {"UPDATE documents SET content=X'00',size_bytes=1"},
		"wrong-media":      {"UPDATE documents SET media_type='text/markdown'"},
		"blob-type":        {"PRAGMA ignore_check_constraints=ON", "UPDATE documents SET content='text'"},
		"blob-oversize":    {"PRAGMA ignore_check_constraints=ON", "UPDATE documents SET content=zeroblob(1000000),size_bytes=1000000"},
		"numeric-owner":    {"PRAGMA ignore_check_constraints=ON", "UPDATE documents SET owner_id=1"},
		"id-gap":           {"UPDATE documents SET id=2"},
		"meta-utf8":        {"PRAGMA ignore_check_constraints=ON", "UPDATE storage_meta SET storage_contract=CAST(X'8080808080' AS TEXT)"},
		"meta-real":        {"PRAGMA ignore_check_constraints=ON", "UPDATE storage_meta SET next_id=2.25"},
		"extra-view":       {"CREATE VIEW private_view AS SELECT id FROM documents"},
		"owner-over-quota": {"WITH RECURSIVE ids(n) AS (VALUES(2) UNION ALL SELECT n+1 FROM ids WHERE n<4) INSERT INTO documents SELECT n,owner_id,filename,media_type,size_bytes,sha256,content FROM ids,documents WHERE id=1", "UPDATE storage_meta SET next_id=5"},
		"over-six-rows":    {"WITH RECURSIVE ids(n) AS (VALUES(2) UNION ALL SELECT n+1 FROM ids WHERE n<7) INSERT INTO documents SELECT n,owner_id,filename,media_type,size_bytes,sha256,content FROM ids,documents WHERE id=1", "UPDATE storage_meta SET next_id=8"},
	}
	for name, statements := range cases {
		t.Run(name, func(t *testing.T) {
			repository, path := newSQLite(t)
			if _, err := repository.Commit("alice", "x.txt", "text/plain", []byte("original"), allowWrite); err != nil {
				t.Fatal(err)
			}
			storageSQL(t, path, statements...)
			before, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := NewSQLiteRepository(path); err == nil {
				t.Fatal("corrupt startup accepted")
			}
			router := testRouter(t, Config{Repository: repository})
			for _, route := range []string{"/documents", "/documents/doc-000001", "/documents/doc-000001/content"} {
				assertError(t, perform(router, request("GET", route, "lab-alice-session", nil)), 503, "repository_unavailable")
			}
			assertError(t, perform(router, request("POST", "/documents", "lab-alice-session", []byte("new"))), 503, "repository_unavailable")
			after, err := os.ReadFile(path)
			if err != nil || !bytes.Equal(before, after) {
				t.Fatal("invalid storage was changed")
			}
		})
	}
}

type mutableAuthStore struct {
	SessionStore
	mu      sync.Mutex
	failure string
	calls   int
}

func (store *mutableAuthStore) Resolve(token string, now time.Time) (*Principal, error) {
	store.mu.Lock()
	defer store.mu.Unlock()
	store.calls++
	switch store.failure {
	case "revoke", "expired":
		return nil, nil
	case "owner":
		return &Principal{UserID: "bob", CanWrite: true}, nil
	case "readonly":
		return &Principal{UserID: "alice", CanWrite: false}, nil
	case "error":
		return nil, errors.New("private session value")
	case "panic":
		panic("private session value")
	}
	return store.SessionStore.Resolve(token, now)
}
func TestSQLiteFinalAuthorizationAfterTransactionAcquired(t *testing.T) {
	for _, change := range []string{"revoke", "expired", "owner", "readonly", "error", "panic"} {
		t.Run(change, func(t *testing.T) {
			repository, path := newSQLite(t)
			sessions, err := LoadSessions()
			if err != nil {
				t.Fatal(err)
			}
			store := &mutableAuthStore{SessionStore: NewMemorySessionStore(sessions, time.Now())}
			repository.hooks.beforeAuthorize = func() error { store.mu.Lock(); store.failure = change; store.mu.Unlock(); return nil }
			code, status := "authentication_required", 401
			if change == "readonly" {
				code, status = "forbidden", 403
			}
			if change == "error" || change == "panic" {
				code, status = "session_store_unavailable", 503
			}
			assertError(t, perform(testRouter(t, Config{Repository: repository, Sessions: store}), request("POST", "/documents", "lab-alice-session", []byte("x"))), status, code)
			if store.calls != 3 {
				t.Fatalf("expected initial/body/final checks, got %d", store.calls)
			}
			count, next := rowCount(t, path)
			if count != 0 || next != 1 {
				t.Fatal("authorization failure wrote")
			}
			repository.hooks = sqliteHooks{}
			if _, err := repository.Commit("alice", "x.txt", "text/plain", []byte("x"), allowWrite); err != nil {
				t.Fatal("authorization leaked lock")
			}
		})
	}
}
func TestSQLiteCheckerCalledExactlyOnceInsideTransaction(t *testing.T) {
	repository, path := newSQLite(t)
	calls := 0
	_, err := repository.Commit("alice", "x.txt", "text/plain", []byte("x"), func() error {
		calls++
		db, connection, err := openSQLite(path)
		if err != nil {
			t.Fatal(err)
		}
		defer closeSQLite(db, connection)
		if _, err := connection.ExecContext(context.Background(), "BEGIN IMMEDIATE"); err == nil {
			t.Fatal("checker was not inside the write transaction")
		}
		return nil
	})
	if err != nil || calls != 1 {
		t.Fatal("checker count", calls, err)
	}
}
func TestSQLiteMissingFileNeverRecreated(t *testing.T) {
	repository, path := newSQLite(t)
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if _, err := repository.List("alice"); err == nil {
		t.Fatal("missing store succeeded")
	}
	if _, err := os.Stat(path); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("runtime recreated missing store")
	}
}

func TestSQLiteCloseFailureDoesNotEraseCommitUncertainty(t *testing.T) {
	for _, operation := range []string{"read", "write", "authorization"} {
		t.Run(operation, func(t *testing.T) {
			repository, path := newSQLite(t)
			repository.hooks.close = func(*sql.DB, *sql.Conn) error { return errors.New("private close detail") }
			var err error
			if operation == "read" {
				_, err = repository.List("alice")
			} else if operation == "authorization" {
				sessions, loadErr := LoadSessions()
				if loadErr != nil {
					t.Fatal(loadErr)
				}
				store := &mutableAuthStore{SessionStore: NewMemorySessionStore(sessions, time.Now())}
				repository.hooks.beforeAuthorize = func() error { store.failure = "revoke"; return nil }
				assertError(t, perform(testRouter(t, Config{Repository: repository, Sessions: store}), request("POST", "/documents", "lab-alice-session", []byte("x"))), 401, "authentication_required")
			} else {
				_, err = repository.Commit("alice", "x.txt", "text/plain", []byte("x"), allowWrite)
			}
			if operation == "read" && !errors.Is(err, errRepository) {
				t.Fatal("read close error not fixed")
			}
			if operation == "write" && !errors.Is(err, ErrResultUnconfirmed) {
				t.Fatal("write close error lost uncertainty")
			}
			want := int64(0)
			if operation == "write" {
				want = 1
			}
			if count, next := rowCount(t, path); count != want || next != want+1 {
				t.Fatal("close error changed transaction outcome")
			}
		})
	}
}
func TestSQLiteClockExpiresAtFinalTransactionCheck(t *testing.T) {
	repository, path := newSQLite(t)
	now := time.Unix(1000, 0)
	store := NewMemorySessionStore([]Session{{Token: "final-clock", UserID: "alice", CanWrite: true, ExpiresAfterSeconds: 10}}, now)
	repository.hooks.beforeAuthorize = func() error { now = now.Add(10 * time.Second); return nil }
	router := testRouter(t, Config{Repository: repository, Sessions: store, Clock: func() time.Time { return now }})
	assertError(t, perform(router, request("POST", "/documents", "final-clock", []byte("x"))), 401, "authentication_required")
	if count, next := rowCount(t, path); count != 0 || next != 1 {
		t.Fatal("exact expiry wrote")
	}
}

type repositoryImpersonator struct {
	Repository
	mode string
}

func (repo repositoryImpersonator) Commit(_, _, _ string, _ []byte, check func() error) (*Metadata, error) {
	switch repo.mode {
	case "missing":
		return &Metadata{ID: "doc-000001"}, nil
	case "twice":
		if err := check(); err != nil {
			return nil, err
		}
		if err := check(); err != nil {
			return nil, err
		}
		return &Metadata{ID: "doc-000001"}, nil
	case "panic":
		panic("authentication_required private-path-session")
	}
	return nil, errors.New("authentication_required private-path-session")
}
func TestRepositoryCannotForgePrivateAuthorizationLatch(t *testing.T) {
	for _, mode := range []string{"missing", "twice", "error", "panic"} {
		t.Run(mode, func(t *testing.T) {
			repo := repositoryImpersonator{Repository: NewMemoryRepository(), mode: mode}
			assertError(t, perform(testRouter(t, Config{Repository: repo}), request("POST", "/documents", "lab-alice-session", []byte("x"))), 503, "repository_unavailable")
		})
	}
}

type changedOwnerStore struct {
	SessionStore
	calls int
}

func (store *changedOwnerStore) Resolve(token string, now time.Time) (*Principal, error) {
	store.calls++
	if store.calls == 2 {
		return &Principal{UserID: "bob", CanWrite: true}, nil
	}
	return store.SessionStore.Resolve(token, now)
}
func TestChangedOwnerAfterBodyDoesNotReachRepository(t *testing.T) {
	sessions, err := LoadSessions()
	if err != nil {
		t.Fatal(err)
	}
	store := &changedOwnerStore{SessionStore: NewMemorySessionStore(sessions, time.Now())}
	repository := &countingRepository{Repository: NewMemoryRepository()}
	assertError(t, perform(testRouter(t, Config{Sessions: store, Repository: repository}), request("POST", "/documents", "lab-alice-session", []byte("x"))), 401, "authentication_required")
	if repository.commits.Load() != 0 {
		t.Fatal("changed owner reached repository")
	}
}

func TestSQLitePausedBodyHoldsNoDatabaseLockAndRechecksBeforeRepository(t *testing.T) {
	repository, path := newSQLite(t)
	sessions, err := LoadSessions()
	if err != nil {
		t.Fatal(err)
	}
	store := NewMemorySessionStore(sessions, time.Now())
	counting := &countingRepository{Repository: repository}
	router := testRouter(t, Config{Repository: counting, Sessions: store})
	body := &pausedBody{data: []byte("paused"), entered: make(chan struct{}), release: make(chan struct{})}
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
	// An independent write connection can commit while the HTTP body is paused.
	if _, err := repository.Commit("bob", "other.txt", "text/plain", []byte("other"), allowWrite); err != nil {
		t.Fatal("body held database lock", err)
	}
	if err := store.Revoke("lab-alice-session"); err != nil {
		t.Fatal(err)
	}
	release()
	assertError(t, awaitResponse(t, result), 401, "authentication_required")
	if counting.commits.Load() != 0 {
		t.Fatal("revoked body reached repository")
	}
	if count, next := rowCount(t, path); count != 1 || next != 2 {
		t.Fatal("revoked upload changed storage")
	}
}

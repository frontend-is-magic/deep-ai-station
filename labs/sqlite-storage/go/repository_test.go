package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

const firstTime = "2026-10-03T12:00:00.000Z"
const laterTime = "2026-10-03T13:00:00.001Z"

func testRepository(t *testing.T, path string) *Repository {
	t.Helper()
	repository, err := openRepository(path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { repository.db.Close() })
	return repository
}

func mustResult[T any](t *testing.T, repository *Repository, request Request) T {
	t.Helper()
	result, err := repository.execute(request)
	if err != nil {
		t.Fatal(err)
	}
	typed, ok := result.(T)
	if !ok {
		t.Fatalf("unexpected result type %T", result)
	}
	return typed
}

func migrateTest(t *testing.T, repository *Repository) {
	t.Helper()
	result := mustResult[map[string]int](t, repository, Request{Op: "migrate"})
	if !reflect.DeepEqual(result, map[string]int{"schema_version": 2}) {
		t.Fatalf("unexpected migration result %#v", result)
	}
}

func setRequest(owner, lesson string, completed bool, at string) Request {
	return Request{Op: "set_progress", Owner: owner, Lesson: lesson, Completed: completed, At: at}
}

func listRequest(op, owner string, cursor int64, limit int) Request {
	return Request{Op: op, Owner: owner, Cursor: cursor, Limit: limit}
}

func mustSQL(t *testing.T, repository *Repository, statement string, args ...any) {
	t.Helper()
	if _, err := repository.db.Exec(statement, args...); err != nil {
		t.Fatal(err)
	}
}

func scalar(t *testing.T, repository *Repository, statement string) int {
	t.Helper()
	var value int
	if err := repository.db.QueryRow(statement).Scan(&value); err != nil {
		t.Fatal(err)
	}
	return value
}

func seedVersionOne(t *testing.T, repository *Repository) {
	t.Helper()
	data, err := migration(1)
	if err != nil {
		t.Fatal(err)
	}
	for _, statement := range strings.Split(string(data), ";") {
		if strings.TrimSpace(statement) != "" {
			mustSQL(t, repository, statement)
		}
	}
	mustSQL(t, repository, "INSERT INTO progress (id,owner_id,lesson_id,completed,created_at) VALUES (?,?,?,?,?)", 42, "alice", "legacy", true, firstTime)
	mustSQL(t, repository, "INSERT INTO audit (id,progress_id,completed,at) VALUES (?,?,?,?)", 17, 42, true, firstTime)
	mustSQL(t, repository, "PRAGMA user_version = 1")
}

func databaseBytes(t *testing.T, path string) []byte {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return data
}

func expectCursor(t *testing.T, cursor *int64, expected int64) {
	t.Helper()
	if expected == 0 {
		if cursor != nil {
			t.Fatalf("unexpected next cursor %d", *cursor)
		}
	} else if cursor == nil || *cursor != expected {
		t.Fatalf("expected next cursor %d, received %v", expected, cursor)
	}
}

func assertCLIError(t *testing.T, args []string, input io.Reader, expected string) {
	t.Helper()
	var output bytes.Buffer
	if code := run(args, input, &output); code != 1 {
		t.Fatalf("error exit = %d, expected 1", code)
	}
	if output.String() != "{\"error\":\""+expected+"\"}\n" {
		t.Fatalf("error was not the exact public JSON: %s", output.String())
	}
}

func TestPersistenceIdempotenceAndAuditAfterReopen(t *testing.T) {
	path := filepath.Join(t.TempDir(), "progress.sqlite")
	first := testRepository(t, path)
	migrateTest(t, first)
	created := mustResult[Change](t, first, setRequest("alice", "tools", true, firstTime))
	want := Progress{ID: 1, Owner: "alice", Lesson: "tools", Completed: true, CreatedAt: firstTime, UpdatedAt: firstTime}
	if !created.Changed || created.Item != want {
		t.Fatalf("first write = %#v", created)
	}
	if err := first.db.Close(); err != nil {
		t.Fatal(err)
	}
	second := testRepository(t, path)
	replayed := mustResult[Change](t, second, setRequest("alice", "tools", true, laterTime))
	if replayed.Changed || replayed.Item != want {
		t.Fatalf("idempotent write changed the original row: %#v", replayed)
	}
	audits := mustResult[Page[Audit]](t, second, listRequest("list_audit", "alice", 0, 20))
	if !reflect.DeepEqual(audits.Items, []Audit{{ID: 1, ProgressID: 1, Completed: true, At: firstTime}}) {
		t.Fatalf("idempotent write added an audit: %#v", audits.Items)
	}
	changed := mustResult[Change](t, second, setRequest("alice", "tools", false, laterTime))
	want.Completed, want.UpdatedAt = false, laterTime
	if !changed.Changed || changed.Item != want {
		t.Fatalf("state transition = %#v", changed)
	}
	audits = mustResult[Page[Audit]](t, second, listRequest("list_audit", "alice", 0, 20))
	if !reflect.DeepEqual(audits.Items, []Audit{
		{ID: 1, ProgressID: 1, Completed: true, At: firstTime},
		{ID: 2, ProgressID: 1, Completed: false, At: laterTime},
	}) {
		t.Fatalf("state transition audit = %#v", audits.Items)
	}
	expectCursor(t, audits.NextCursor, 0)
}

func TestVersionOneUpgradePreservesDataAndRepeatedMigrationIsUnchanged(t *testing.T) {
	path := filepath.Join(t.TempDir(), "legacy.sqlite")
	repository := testRepository(t, path)
	seedVersionOne(t, repository)
	if _, err := repository.execute(listRequest("list_progress", "alice", 0, 20)); !errors.Is(err, errSchema) {
		t.Fatalf("ordinary v1 operation was not rejected: %v", err)
	}
	migrateTest(t, repository)
	before := databaseBytes(t, path)
	migrateTest(t, repository)
	if !bytes.Equal(databaseBytes(t, path), before) {
		t.Fatal("idempotent migration rewrote database bytes")
	}
	page := mustResult[Page[Progress]](t, repository, listRequest("list_progress", "alice", 0, 20))
	want := []Progress{{ID: 42, Owner: "alice", Lesson: "legacy", Completed: true, CreatedAt: firstTime, UpdatedAt: firstTime}}
	if !reflect.DeepEqual(page.Items, want) || scalar(t, repository, "PRAGMA user_version") != 2 {
		t.Fatalf("v1 data or version changed incorrectly: %#v", page.Items)
	}
	audit := mustResult[Page[Audit]](t, repository, listRequest("list_audit", "alice", 0, 20))
	if !reflect.DeepEqual(audit.Items, []Audit{{ID: 17, ProgressID: 42, Completed: true, At: firstTime}}) {
		t.Fatal("v1 audit data was not preserved")
	}
}

func TestFutureVersionRejectsAllOperationsWithoutWrites(t *testing.T) {
	path := filepath.Join(t.TempDir(), "future.sqlite")
	repository := testRepository(t, path)
	seedVersionOne(t, repository)
	mustSQL(t, repository, "PRAGMA user_version = 99")
	before := databaseBytes(t, path)
	for _, request := range []Request{
		{Op: "migrate"}, setRequest("alice", "tools", true, laterTime),
		listRequest("list_progress", "alice", 0, 20), listRequest("list_audit", "alice", 0, 20),
	} {
		if _, err := repository.execute(request); !errors.Is(err, errSchema) {
			t.Fatalf("%s: expected unsupported_schema, received %v", request.Op, err)
		}
		if !bytes.Equal(databaseBytes(t, path), before) {
			t.Fatalf("%s changed a future-version database", request.Op)
		}
	}
	if scalar(t, repository, "PRAGMA user_version") != 99 || scalar(t, repository, "SELECT COUNT(*) FROM progress") != 1 {
		t.Fatal("future version or its rows changed")
	}
}

func TestOwnerKeysetPagesAndAuditJoinUseTheirOwnIDs(t *testing.T) {
	repository := testRepository(t, filepath.Join(t.TempDir(), "pagination.sqlite"))
	migrateTest(t, repository)
	for index, owner := range []string{"alice", "bob", "alice", "bob", "alice"} {
		lesson := []string{"one", "one", "two", "two", "three"}[index]
		mustResult[Change](t, repository, setRequest(owner, lesson, true, firstTime))
	}
	mustResult[Change](t, repository, setRequest("alice", "one", false, laterTime))
	first := mustResult[Page[Progress]](t, repository, listRequest("list_progress", "alice", 0, 2))
	if len(first.Items) != 2 || first.Items[0].ID != 1 || first.Items[1].ID != 3 {
		t.Fatalf("first owner page = %#v", first.Items)
	}
	expectCursor(t, first.NextCursor, 3)
	last := mustResult[Page[Progress]](t, repository, listRequest("list_progress", "alice", *first.NextCursor, 2))
	if len(last.Items) != 1 || last.Items[0].ID != 5 {
		t.Fatalf("last owner page = %#v", last.Items)
	}
	expectCursor(t, last.NextCursor, 0)
	other := mustResult[Page[Progress]](t, repository, listRequest("list_progress", "bob", 0, 2))
	if len(other.Items) != 2 || other.Items[0].ID != 2 || other.Items[1].ID != 4 {
		t.Fatalf("other owner page = %#v", other.Items)
	}
	expectCursor(t, other.NextCursor, 0)
	auditFirst := mustResult[Page[Audit]](t, repository, listRequest("list_audit", "alice", 0, 2))
	if len(auditFirst.Items) != 2 || auditFirst.Items[0].ID != 1 || auditFirst.Items[1].ID != 3 {
		t.Fatalf("first audit page = %#v", auditFirst.Items)
	}
	expectCursor(t, auditFirst.NextCursor, 3)
	auditLast := mustResult[Page[Audit]](t, repository, listRequest("list_audit", "alice", 3, 2))
	if !reflect.DeepEqual(auditLast.Items, []Audit{
		{ID: 5, ProgressID: 5, Completed: true, At: firstTime},
		{ID: 6, ProgressID: 1, Completed: false, At: laterTime},
	}) {
		t.Fatalf("audit owner JOIN or cursor used the wrong ID: %#v", auditLast.Items)
	}
	expectCursor(t, auditLast.NextCursor, 0)
	bobAudit := mustResult[Page[Audit]](t, repository, listRequest("list_audit", "bob", 0, 2))
	if len(bobAudit.Items) != 2 || bobAudit.Items[0].ID != 2 || bobAudit.Items[1].ID != 4 {
		t.Fatalf("other owner's audit page = %#v", bobAudit.Items)
	}
	expectCursor(t, bobAudit.NextCursor, 0)
	empty := mustResult[Page[Audit]](t, repository, listRequest("list_audit", "alice", 6, 2))
	if empty.Items == nil || len(empty.Items) != 0 {
		t.Fatal("empty pages must encode [] instead of null")
	}
	expectCursor(t, empty.NextCursor, 0)
}

func TestTrustedRepositoryValuesAreParameterized(t *testing.T) {
	repository := testRepository(t, filepath.Join(t.TempDir(), "binding.sqlite"))
	migrateTest(t, repository)
	mustResult[Change](t, repository, setRequest("alice", "tools", true, firstTime))
	// This trusted internal call deliberately bypasses CLI identifier validation.
	owner, lesson := "alice' OR 1=1 --", "x'); DROP TABLE progress; --"
	inserted := mustResult[Change](t, repository, setRequest(owner, lesson, false, laterTime))
	page := mustResult[Page[Progress]](t, repository, listRequest("list_progress", owner, 0, 20))
	if !reflect.DeepEqual(page.Items, []Progress{inserted.Item}) || inserted.Item.Owner != owner || inserted.Item.Lesson != lesson {
		t.Fatal("quoted values changed SQL semantics instead of being bound")
	}
	audit := mustResult[Page[Audit]](t, repository, listRequest("list_audit", owner, 0, 20))
	if len(audit.Items) != 1 || audit.Items[0].ProgressID != inserted.Item.ID {
		t.Fatal("audit query did not bind owner")
	}
	ordinary := mustResult[Page[Progress]](t, repository, listRequest("list_progress", "alice", 0, 20))
	if len(ordinary.Items) != 1 || scalar(t, repository, "SELECT COUNT(*) FROM progress") != 2 {
		t.Fatal("quoted owner leaked rows or changed the schema")
	}
}

func TestAuditFailureRollsBackInsertedOrUpdatedProgress(t *testing.T) {
	for _, existing := range []bool{false, true} {
		name := "insert"
		if existing {
			name = "update"
		}
		t.Run(name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "rollback.sqlite")
			repository := testRepository(t, path)
			migrateTest(t, repository)
			if existing {
				mustResult[Change](t, repository, setRequest("alice", "tools", true, firstTime))
			}
			beforeProgress := mustResult[Page[Progress]](t, repository, listRequest("list_progress", "alice", 0, 20))
			beforeAudit := mustResult[Page[Audit]](t, repository, listRequest("list_audit", "alice", 0, 20))
			mustSQL(t, repository, "CREATE TRIGGER fail_audit BEFORE INSERT ON audit BEGIN SELECT RAISE(ABORT, 'private audit fixture failure'); END;")
			if _, err := repository.execute(setRequest("alice", "tools", false, laterTime)); err == nil || errorCode(err) != "storage_failure" {
				t.Fatalf("audit fault returned %v", err)
			}
			request := `{"op":"set_progress","owner_id":"alice","lesson_id":"tools","completed":false,"at":"` + laterTime + `"}`
			assertCLIError(t, []string{path}, strings.NewReader(request), "storage_failure")
			afterProgress := mustResult[Page[Progress]](t, repository, listRequest("list_progress", "alice", 0, 20))
			afterAudit := mustResult[Page[Audit]](t, repository, listRequest("list_audit", "alice", 0, 20))
			if !reflect.DeepEqual(afterProgress, beforeProgress) || !reflect.DeepEqual(afterAudit, beforeAudit) {
				t.Fatal("audit failure partially committed progress or audit")
			}
		})
	}
}

func TestFailedBackfillRollsBackColumnDataAndVersion(t *testing.T) {
	repository := testRepository(t, filepath.Join(t.TempDir(), "migration-rollback.sqlite"))
	seedVersionOne(t, repository)
	mustSQL(t, repository, "CREATE TRIGGER fail_backfill BEFORE UPDATE ON progress BEGIN SELECT RAISE(ABORT, 'private backfill fixture failure'); END;")
	if _, err := repository.execute(Request{Op: "migrate"}); err == nil || errorCode(err) != "storage_failure" {
		t.Fatalf("backfill failure returned %v", err)
	}
	if scalar(t, repository, "PRAGMA user_version") != 1 || scalar(t, repository, "SELECT COUNT(*) FROM pragma_table_info('progress') WHERE name = 'updated_at'") != 0 {
		t.Fatal("failed migration committed its new column or version")
	}
	var createdAt string
	if err := repository.db.QueryRow("SELECT created_at FROM progress WHERE id = 42").Scan(&createdAt); err != nil || createdAt != firstTime {
		t.Fatal("failed migration changed legacy data")
	}
	mustSQL(t, repository, "DROP TRIGGER fail_backfill")
	migrateTest(t, repository)
	page := mustResult[Page[Progress]](t, repository, listRequest("list_progress", "alice", 0, 20))
	if len(page.Items) != 1 || page.Items[0].UpdatedAt != firstTime {
		t.Fatal("migration could not recover after the fixture fault was removed")
	}
}

func TestConnectionPragmasAndDatabaseConstraints(t *testing.T) {
	repository := testRepository(t, filepath.Join(t.TempDir(), "constraints.sqlite"))
	migrateTest(t, repository)
	if scalar(t, repository, "PRAGMA foreign_keys") != 1 || scalar(t, repository, "PRAGMA busy_timeout") != 1000 {
		t.Fatal("connection pragmas differ from the contract")
	}
	mustResult[Change](t, repository, setRequest("alice", "tools", true, firstTime))
	for _, query := range []struct {
		statement string
		args      []any
	}{
		{"INSERT INTO audit (progress_id,completed,at) VALUES (?,?,?)", []any{999, 1, firstTime}},
		{"INSERT INTO progress (owner_id,lesson_id,completed,created_at,updated_at) VALUES (?,?,?,?,?)", []any{"alice", "tools", 1, firstTime, firstTime}},
		{"INSERT INTO progress (owner_id,lesson_id,completed,created_at,updated_at) VALUES (?,?,?,?,?)", []any{"alice", "invalid", 2, firstTime, firstTime}},
	} {
		if _, err := repository.db.Exec(query.statement, query.args...); err == nil {
			t.Fatal("database accepted invalid foreign key, duplicate identity or boolean")
		}
	}
	if scalar(t, repository, "SELECT COUNT(*) FROM progress") != 1 || scalar(t, repository, "SELECT COUNT(*) FROM audit") != 1 {
		t.Fatal("constraint failure left an invalid row")
	}
}

func TestTwoConnectionsMapBusyAndLeaveNoPartialWrites(t *testing.T) {
	path := filepath.Join(t.TempDir(), "busy.sqlite")
	holder := testRepository(t, path)
	migrateTest(t, holder)
	writer := testRepository(t, path)
	lock, err := holder.db.BeginTx(context.Background(), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Rollback()
	for _, request := range []Request{{Op: "migrate"}, setRequest("alice", "tools", true, firstTime)} {
		if _, err := writer.execute(request); err == nil || errorCode(err) != "database_busy" {
			t.Fatalf("%s: competing writer returned %v instead of database_busy", request.Op, err)
		}
	}
	if err := lock.Rollback(); err != nil {
		t.Fatal(err)
	}
	if scalar(t, writer, "SELECT COUNT(*) FROM progress") != 0 || scalar(t, writer, "SELECT COUNT(*) FROM audit") != 0 {
		t.Fatal("timed-out write partially committed data")
	}
	changed := mustResult[Change](t, writer, setRequest("alice", "tools", true, firstTime))
	if !changed.Changed || changed.Item.ID != 1 {
		t.Fatal("writer did not recover after releasing the lock")
	}
}

type failedInput struct{}

func (failedInput) Read([]byte) (int, error) { return 0, errors.New("private read fixture failure") }

func TestCLIInvalidInputDoesNotCreateDatabaseOrSidecars(t *testing.T) {
	for name, body := range map[string][]byte{
		"empty":           {},
		"trailing-json":   []byte(`{"op":"migrate"} {}`),
		"wrong-field":     []byte(`{"op":"migrate","extra":true}`),
		"oversized":       bytes.Repeat([]byte(" "), 4097),
		"invalid-utf8":    append([]byte(`{"op":"`), 0xff, '"', '}'),
		"quoted-owner":    []byte(`{"op":"set_progress","owner_id":"alice' OR 1=1--","lesson_id":"tools","completed":true,"at":"` + firstTime + `"}`),
		"numeric-boolean": []byte(`{"op":"set_progress","owner_id":"alice","lesson_id":"tools","completed":1,"at":"` + firstTime + `"}`),
	} {
		t.Run(name, func(t *testing.T) {
			folder := t.TempDir()
			path := filepath.Join(folder, "must-not-exist.sqlite")
			assertCLIError(t, []string{path}, bytes.NewReader(body), "invalid_input")
			entries, err := os.ReadDir(folder)
			if err != nil || len(entries) != 0 {
				t.Fatal("invalid CLI input created a database or sidecar")
			}
		})
	}
	folder := t.TempDir()
	path := filepath.Join(folder, "must-not-exist.sqlite")
	assertCLIError(t, nil, strings.NewReader(`{"op":"migrate"}`), "invalid_input")
	assertCLIError(t, []string{""}, strings.NewReader(`{"op":"migrate"}`), "invalid_input")
	assertCLIError(t, []string{path, "extra"}, strings.NewReader(`{"op":"migrate"}`), "invalid_input")
	assertCLIError(t, []string{path}, failedInput{}, "invalid_input")
	entries, err := os.ReadDir(folder)
	if err != nil || len(entries) != 0 {
		t.Fatal("invalid argv or reader failure created storage")
	}
}

func TestCLIStorageFailureNeverExposesDatabasePath(t *testing.T) {
	path := filepath.Join(t.TempDir(), "missing-parent", "private-location.sqlite")
	assertCLIError(t, []string{path}, strings.NewReader(`{"op":"migrate"}`), "storage_failure")
}

func TestStandaloneLayoutFindsItsOwnMigrations(t *testing.T) {
	folder := t.TempDir()
	if err := os.Mkdir(filepath.Join(folder, "migrations"), 0700); err != nil {
		t.Fatal(err)
	}
	for _, version := range []int{1, 2} {
		data, err := migration(version)
		if err != nil {
			t.Fatal(err)
		}
		name := map[int]string{1: "001.sql", 2: "002.sql"}[version]
		if err := os.WriteFile(filepath.Join(folder, "migrations", name), data, 0600); err != nil {
			t.Fatal(err)
		}
	}
	t.Chdir(folder)
	var output bytes.Buffer
	if code := run([]string{filepath.Join(folder, "local.sqlite")}, strings.NewReader(`{"op":"migrate"}`), &output); code != 0 {
		t.Fatalf("standalone CLI failed: %s", output.String())
	}
	var result map[string]int
	if err := json.Unmarshal(output.Bytes(), &result); err != nil || result["schema_version"] != 2 {
		t.Fatal("standalone CLI did not resolve its packaged migration files")
	}
}

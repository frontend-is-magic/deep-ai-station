package main

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"unicode/utf8"

	_ "modernc.org/sqlite"
)

const storageContract = "text-upload-sqlite-v1"
const storageApplicationID = 1146442545
const storageRelativePath = ".data/uploads.sqlite3"

var ErrResultUnconfirmed = errors.New("result_unconfirmed")
var errRepository = errors.New("repository_unavailable")
var errUnsupportedSchema = errors.New("unsupported_schema")
var errDatabaseMissing = errors.New("database_missing")
var errDatabaseExists = errors.New("database_exists")

type sqliteHooks struct {
	// Trusted native-test seams; never configured from CLI, environment, or HTTP.
	beforeAuthorize func() error
	afterInsert     func(*sql.Conn) error
	commit          func(*sql.Conn) error
	afterCommit     func() error
	close           func(*sql.DB, *sql.Conn) error
}
type SQLiteRepository struct {
	path   string
	schema []string
	hooks  sqliteHooks
}

func fixedSchema() ([]string, error) {
	raw, err := readLabData("schema.sql")
	if err != nil {
		return nil, errRepository
	}
	statements := []string{}
	for _, statement := range strings.Split(string(raw), ";") {
		if trimmed := strings.TrimSpace(statement); trimmed != "" {
			statements = append(statements, trimmed)
		}
	}
	if len(statements) != 6 {
		return nil, errRepository
	}
	return statements, nil
}
func storagePathCheck(path string, create bool) error {
	directory := filepath.Dir(path)
	info, err := os.Lstat(directory)
	if errors.Is(err, os.ErrNotExist) && create {
		if err = os.Mkdir(directory, 0700); err != nil && !errors.Is(err, os.ErrExist) {
			return errRepository
		}
		info, err = os.Lstat(directory)
	}
	if errors.Is(err, os.ErrNotExist) {
		return errDatabaseMissing
	}
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return errRepository
	}
	info, err = os.Lstat(path)
	if create {
		if err == nil {
			return errDatabaseExists
		}
		if errors.Is(err, os.ErrNotExist) {
			return nil
		}
		return errRepository
	}
	if errors.Is(err, os.ErrNotExist) {
		return errDatabaseMissing
	}
	if err != nil || !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 {
		return errRepository
	}
	return nil
}
func openSQLite(path string) (*sql.DB, *sql.Conn, error) {
	if err := storagePathCheck(path, false); err != nil {
		return nil, nil, err
	}
	absolute, err := filepath.Abs(path)
	if err != nil {
		return nil, nil, errRepository
	}
	uri := url.URL{Scheme: "file", Path: absolute}
	uri.RawQuery = url.Values{"mode": {"rw"}, "_pragma": {"busy_timeout(0)", "synchronous(FULL)"}}.Encode()
	db, err := sql.Open("sqlite", uri.String())
	if err != nil {
		return nil, nil, errRepository
	}
	db.SetMaxOpenConns(1)
	connection, err := db.Conn(context.Background())
	if err != nil {
		_ = db.Close()
		return nil, nil, errRepository
	}
	return db, connection, nil
}
func closeSQLite(db *sql.DB, connection *sql.Conn) error {
	connectionError := connection.Close()
	databaseError := db.Close()
	if connectionError != nil || databaseError != nil {
		return errRepository
	}
	return nil
}
func InitializeSQLite(path string) (err error) {
	statements, err := fixedSchema()
	if err != nil {
		return err
	}
	if err = storagePathCheck(path, true); err != nil {
		return err
	}
	file, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if errors.Is(err, os.ErrExist) {
		return errDatabaseExists
	}
	if err != nil {
		return errRepository
	}
	if file.Close() != nil {
		return errRepository
	}
	db, connection, err := openSQLite(path)
	if err != nil {
		return err
	}
	started, commitStarted := false, false
	defer func() {
		if recover() != nil {
			err = errRepository
		}
		if started {
			_, _ = connection.ExecContext(context.Background(), "ROLLBACK")
		}
		closeErr := closeSQLite(db, connection)
		if err == nil {
			err = closeErr
		}
		if err != nil && commitStarted {
			err = ErrResultUnconfirmed
		}
	}()
	if _, err = connection.ExecContext(context.Background(), "PRAGMA journal_mode=DELETE"); err != nil {
		return errRepository
	}
	if _, err = connection.ExecContext(context.Background(), "BEGIN IMMEDIATE"); err != nil {
		return errRepository
	}
	started = true
	for _, statement := range statements {
		if _, err = connection.ExecContext(context.Background(), statement); err != nil {
			return errRepository
		}
	}
	commitStarted = true
	if _, err = connection.ExecContext(context.Background(), "COMMIT"); err != nil {
		return ErrResultUnconfirmed
	}
	started = false
	return nil
}
func NewSQLiteRepository(path string) (*SQLiteRepository, error) {
	statements, err := fixedSchema()
	if err != nil {
		return nil, err
	}
	repository := &SQLiteRepository{path: path, schema: statements}
	if err := repository.operation(false, nil, func(*sql.Conn, int64) error { return nil }); err != nil {
		return nil, err
	}
	return repository, nil
}
func (repository *SQLiteRepository) operation(write bool, beforeWrite func() error, work func(*sql.Conn, int64) error) (err error) {
	db, connection, err := openSQLite(repository.path)
	if err != nil {
		return err
	}
	started, commitStarted := false, false
	defer func() {
		if recover() != nil {
			err = errRepository
		}
		if started {
			_, _ = connection.ExecContext(context.Background(), "ROLLBACK")
		}
		closeErr := closeSQLite(db, connection)
		if repository.hooks.close != nil {
			// The actual owned connection has already been closed; the hook only
			// models lost close confirmation for deterministic native tests.
			if hookErr := repository.hooks.close(db, connection); hookErr != nil {
				closeErr = errRepository
			}
		}
		if err == nil {
			err = closeErr
		}
		if write && commitStarted && err != nil {
			err = ErrResultUnconfirmed
		}
	}()
	begin := "BEGIN"
	if write {
		begin = "BEGIN IMMEDIATE"
	}
	if _, err = connection.ExecContext(context.Background(), begin); err != nil {
		return errRepository
	}
	started = true
	if write {
		if repository.hooks.beforeAuthorize != nil {
			if err = repository.hooks.beforeAuthorize(); err != nil {
				return errRepository
			}
		}
		if beforeWrite == nil {
			return errRepository
		}
		if err = beforeWrite(); err != nil {
			return err
		}
	}
	next, err := repository.validate(connection)
	if err != nil {
		return err
	}
	if err = work(connection, next); err != nil {
		return err
	}
	commitStarted = true
	if write && repository.hooks.commit != nil {
		err = repository.hooks.commit(connection)
	} else {
		_, err = connection.ExecContext(context.Background(), "COMMIT")
	}
	if err != nil {
		return errRepository
	}
	started = false
	if write && repository.hooks.afterCommit != nil {
		if err = repository.hooks.afterCommit(); err != nil {
			return errRepository
		}
	}
	return nil
}
func (repository *SQLiteRepository) validate(connection *sql.Conn) (int64, error) {
	ctx := context.Background()
	var application, version int64
	if connection.QueryRowContext(ctx, "PRAGMA application_id").Scan(&application) != nil || connection.QueryRowContext(ctx, "PRAGMA user_version").Scan(&version) != nil {
		return 0, errRepository
	}
	if application != storageApplicationID || version != 1 {
		return 0, errUnsupportedSchema
	}
	var journal string
	if connection.QueryRowContext(ctx, "PRAGMA journal_mode").Scan(&journal) != nil || journal != "delete" {
		return 0, errRepository
	}
	expected := map[string]string{"storage_meta": repository.schema[0], "documents": repository.schema[1], "documents_owner_id_id": repository.schema[2]}
	rows, err := connection.QueryContext(ctx, "SELECT type,name,tbl_name,CASE WHEN length(CAST(sql AS BLOB)) <= 4096 THEN sql ELSE NULL END FROM sqlite_schema ORDER BY name LIMIT 4")
	if err != nil {
		return 0, errRepository
	}
	count := 0
	for rows.Next() {
		var kind, name, table, statement string
		if rows.Scan(&kind, &name, &table, &statement) != nil {
			_ = rows.Close()
			return 0, errRepository
		}
		wanted, ok := expected[name]
		if !ok || strings.TrimSpace(strings.TrimSuffix(strings.TrimSpace(statement), ";")) != wanted || (name == "documents_owner_id_id" && (kind != "index" || table != "documents")) || (name != "documents_owner_id_id" && (kind != "table" || table != name)) {
			_ = rows.Close()
			return 0, errRepository
		}
		count++
	}
	err = rows.Err()
	_ = rows.Close()
	if err != nil || count != 3 {
		return 0, errRepository
	}
	// Inspect storage classes/byte lengths before asking a driver to allocate content.
	rows, err = connection.QueryContext(ctx, "SELECT typeof(singleton), singleton, typeof(storage_contract), length(CAST(storage_contract AS BLOB)), typeof(next_id), CASE WHEN typeof(next_id)='integer' THEN next_id ELSE NULL END FROM storage_meta LIMIT 2")
	if err != nil {
		return 0, errRepository
	}
	var next int64
	count = 0
	for rows.Next() {
		var singleType, contractType, nextType string
		var singleton, contractBytes int64
		if rows.Scan(&singleType, &singleton, &contractType, &contractBytes, &nextType, &next) != nil || singleType != "integer" || singleton != 1 || contractType != "text" || contractBytes < 1 || contractBytes > 64 || nextType != "integer" || next < 1 || next > 1000000 {
			_ = rows.Close()
			return 0, errRepository
		}
		count++
	}
	err = rows.Err()
	_ = rows.Close()
	if err != nil || count != 1 {
		return 0, errRepository
	}
	var contract string
	if connection.QueryRowContext(ctx, "SELECT storage_contract FROM storage_meta WHERE singleton=1").Scan(&contract) != nil || !utf8.ValidString(contract) {
		return 0, errRepository
	}
	if contract != storageContract {
		return 0, errUnsupportedSchema
	}
	rows, err = connection.QueryContext(ctx, `SELECT id,typeof(id),typeof(owner_id),length(CAST(owner_id AS BLOB)),typeof(filename),length(CAST(filename AS BLOB)),typeof(media_type),length(CAST(media_type AS BLOB)),typeof(size_bytes),CASE WHEN typeof(size_bytes)='integer' THEN size_bytes ELSE NULL END,typeof(sha256),length(CAST(sha256 AS BLOB)),typeof(content),length(content) FROM documents ORDER BY id LIMIT 7`)
	if err != nil {
		return 0, errRepository
	}
	count = 0
	for rows.Next() {
		var id, ownerLen, fileLen, mediaLen, size, hashLen, contentLen int64
		var idType, ownerType, fileType, mediaType, sizeType, hashType, contentType string
		if rows.Scan(&id, &idType, &ownerType, &ownerLen, &fileType, &fileLen, &mediaType, &mediaLen, &sizeType, &size, &hashType, &hashLen, &contentType, &contentLen) != nil {
			_ = rows.Close()
			return 0, errRepository
		}
		count++
		if count > 6 || id != int64(count) || idType != "integer" || ownerType != "text" || ownerLen < 3 || ownerLen > 5 || fileType != "text" || fileLen < 5 || fileLen > 64 || mediaType != "text" || mediaLen < 10 || mediaLen > 13 || sizeType != "integer" || size < 1 || size > 4096 || hashType != "text" || hashLen != 64 || contentType != "blob" || contentLen != size {
			_ = rows.Close()
			return 0, errRepository
		}
	}
	err = rows.Err()
	_ = rows.Close()
	if err != nil || next != int64(count+1) {
		return 0, errRepository
	}
	rows, err = connection.QueryContext(ctx, "SELECT id,owner_id,filename,media_type,size_bytes,sha256,content FROM documents ORDER BY id LIMIT 7")
	if err != nil {
		return 0, errRepository
	}
	totals := map[string]usage{}
	for rows.Next() {
		var id, size int64
		var owner, filename, media, hash string
		var content []byte
		if rows.Scan(&id, &owner, &filename, &media, &size, &hash, &content) != nil {
			_ = rows.Close()
			return 0, errRepository
		}
		digest := sha256.Sum256(content)
		lower := asciiLower(filename)
		if (owner != "alice" && owner != "bob") || !utf8.ValidString(filename) || !filenamePattern.MatchString(lower) || (media != "text/plain" && media != "text/markdown") || (strings.HasSuffix(lower, ".txt") && media != "text/plain") || (strings.HasSuffix(lower, ".md") && media != "text/markdown") || !validText(content) || len(content) != int(size) || hash != hex.EncodeToString(digest[:]) {
			_ = rows.Close()
			return 0, errRepository
		}
		total := totals[owner]
		total.count++
		total.bytes += int(size)
		totals[owner] = total
		if total.count > 3 || total.bytes > 8192 {
			_ = rows.Close()
			return 0, errRepository
		}
	}
	err = rows.Err()
	_ = rows.Close()
	if err != nil {
		return 0, errRepository
	}
	return next, nil
}
func (repository *SQLiteRepository) Commit(owner, filename, media string, content []byte, beforeWrite func() error) (*Metadata, error) {
	copied := append([]byte(nil), content...)
	digest := sha256.Sum256(copied)
	var result *Metadata
	err := repository.operation(true, beforeWrite, func(connection *sql.Conn, next int64) error {
		var count, total int64
		if connection.QueryRowContext(context.Background(), "SELECT COUNT(*), COALESCE(SUM(size_bytes),0) FROM documents WHERE owner_id=?", owner).Scan(&count, &total) != nil {
			return errRepository
		}
		if count >= 3 || int64(len(copied)) > 8192-total {
			return ErrQuotaExceeded
		}
		if next > 999999 {
			return errRepository
		}
		result = &Metadata{ID: fmt.Sprintf("doc-%06d", next), Filename: filename, MediaType: media, SizeBytes: len(copied), SHA256: hex.EncodeToString(digest[:])}
		if _, err := connection.ExecContext(context.Background(), "INSERT INTO documents(id,owner_id,filename,media_type,size_bytes,sha256,content) VALUES(?,?,?,?,?,?,?)", next, owner, filename, media, len(copied), result.SHA256, copied); err != nil {
			return errRepository
		}
		if repository.hooks.afterInsert != nil {
			if err := repository.hooks.afterInsert(connection); err != nil {
				return errRepository
			}
		}
		if _, err := connection.ExecContext(context.Background(), "UPDATE storage_meta SET next_id=? WHERE singleton=1", next+1); err != nil {
			return errRepository
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	return result, nil
}
func (repository *SQLiteRepository) readOwned(owner string) ([]Download, error) {
	result := []Download{}
	err := repository.operation(false, nil, func(connection *sql.Conn, _ int64) error {
		rows, err := connection.QueryContext(context.Background(), "SELECT id,filename,media_type,size_bytes,sha256,content FROM documents WHERE owner_id=? ORDER BY id", owner)
		if err != nil {
			return errRepository
		}
		defer rows.Close()
		for rows.Next() {
			var id, size int64
			var item Download
			if rows.Scan(&id, &item.Metadata.Filename, &item.Metadata.MediaType, &size, &item.Metadata.SHA256, &item.Content) != nil {
				return errRepository
			}
			item.Metadata.ID = fmt.Sprintf("doc-%06d", id)
			item.Metadata.SizeBytes = int(size)
			item.Content = append([]byte(nil), item.Content...)
			result = append(result, item)
		}
		if rows.Err() != nil {
			return errRepository
		}
		return nil
	})
	return result, err
}
func (repository *SQLiteRepository) List(owner string) ([]Metadata, error) {
	documents, err := repository.readOwned(owner)
	if err != nil {
		return nil, err
	}
	result := []Metadata{}
	for _, document := range documents {
		result = append(result, document.Metadata)
	}
	return result, nil
}
func (repository *SQLiteRepository) Find(owner, id string) (*Metadata, error) {
	document, err := repository.Content(owner, id)
	if err != nil || document == nil {
		return nil, err
	}
	return &document.Metadata, nil
}
func (repository *SQLiteRepository) Content(owner, id string) (*Download, error) {
	documents, err := repository.readOwned(owner)
	if err != nil {
		return nil, err
	}
	for _, document := range documents {
		if document.Metadata.ID == id {
			return &document, nil
		}
	}
	return nil, nil
}

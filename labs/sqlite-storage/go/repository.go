package main

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strings"

	"modernc.org/sqlite"
)

type Repository struct{ db *sql.DB }
type Progress struct {
	ID        int64  `json:"id"`
	Owner     string `json:"owner_id"`
	Lesson    string `json:"lesson_id"`
	Completed bool   `json:"completed"`
	CreatedAt string `json:"created_at"`
	UpdatedAt string `json:"updated_at"`
}
type Audit struct {
	ID         int64  `json:"id"`
	ProgressID int64  `json:"progress_id"`
	Completed  bool   `json:"completed"`
	At         string `json:"at"`
}
type Page[T any] struct {
	Items      []T    `json:"items"`
	NextCursor *int64 `json:"next_cursor"`
}
type Change struct {
	Item    Progress `json:"item"`
	Changed bool     `json:"changed"`
}

func openRepository(path string) (*Repository, error) {
	absolute, err := filepath.Abs(path)
	if err != nil {
		return nil, err
	}
	// Build a file URL, so '?' in a filename cannot become driver options.
	uri := url.URL{Scheme: "file", Path: absolute}
	query := url.Values{"_pragma": {"foreign_keys(1)", "busy_timeout(1000)"}, "_txlock": {"immediate"}}
	uri.RawQuery = query.Encode()
	db, err := sql.Open("sqlite", uri.String())
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	if err = db.Ping(); err != nil {
		db.Close()
		return nil, err
	}
	return &Repository{db: db}, nil
}

func errorCode(err error) string {
	if errors.Is(err, errInvalid) {
		return "invalid_input"
	}
	if errors.Is(err, errSchema) {
		return "unsupported_schema"
	}
	var sqliteError *sqlite.Error
	if errors.As(err, &sqliteError) && (sqliteError.Code()&255 == 5 || sqliteError.Code()&255 == 6) {
		return "database_busy"
	}
	return "storage_failure"
}

func migration(version int) ([]byte, error) {
	name := fmt.Sprintf("%03d.sql", version)
	for _, folder := range []string{"migrations", "../shared/migrations"} {
		data, err := os.ReadFile(filepath.Join(folder, name))
		if err == nil {
			return data, nil
		}
		if !os.IsNotExist(err) {
			return nil, err
		}
	}
	return nil, errors.New("migration file missing")
}

func (r *Repository) migrate() (any, error) {
	tx, err := r.db.BeginTx(context.Background(), nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	var version int
	if err = tx.QueryRow("PRAGMA user_version").Scan(&version); err != nil {
		return nil, err
	}
	if version < 0 || version > 2 {
		return nil, errSchema
	}
	for next := version + 1; next <= 2; next++ {
		data, err := migration(next)
		if err != nil {
			return nil, err
		}
		// Fixed maintainer migrations contain plain statements without SQL triggers.
		for _, statement := range strings.Split(string(data), ";") {
			if strings.TrimSpace(statement) == "" {
				continue
			}
			if _, err = tx.Exec(statement); err != nil {
				return nil, err
			}
		}
		// Version is an internal constant, never caller-controlled SQL.
		if _, err = tx.Exec(fmt.Sprintf("PRAGMA user_version = %d", next)); err != nil {
			return nil, err
		}
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	return map[string]int{"schema_version": 2}, nil
}

func scanProgress(row interface{ Scan(...any) error }) (Progress, error) {
	var item Progress
	err := row.Scan(&item.ID, &item.Owner, &item.Lesson, &item.Completed, &item.CreatedAt, &item.UpdatedAt)
	return item, err
}

const progressFields = "id, owner_id, lesson_id, completed, created_at, updated_at"

func (r *Repository) execute(request Request) (any, error) {
	if request.Op == "migrate" {
		return r.migrate()
	}
	if request.Op == "set_progress" {
		return r.setProgress(request)
	}
	var version int
	if err := r.db.QueryRow("PRAGMA user_version").Scan(&version); err != nil {
		return nil, err
	}
	if version != 2 {
		return nil, errSchema
	}
	if request.Op == "list_progress" {
		return r.listProgress(request)
	}
	return r.listAudit(request)
}

func (r *Repository) setProgress(request Request) (any, error) {
	tx, err := r.db.BeginTx(context.Background(), nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	var version int
	if err = tx.QueryRow("PRAGMA user_version").Scan(&version); err != nil {
		return nil, err
	}
	if version != 2 {
		return nil, errSchema
	}
	item, err := scanProgress(tx.QueryRow("SELECT "+progressFields+" FROM progress WHERE owner_id = ? AND lesson_id = ?", request.Owner, request.Lesson))
	if err != nil && !errors.Is(err, sql.ErrNoRows) {
		return nil, err
	}
	changed := errors.Is(err, sql.ErrNoRows) || item.Completed != request.Completed
	if !changed {
		if err = tx.Commit(); err != nil {
			return nil, err
		}
		return Change{Item: item, Changed: false}, nil
	}
	if errors.Is(err, sql.ErrNoRows) {
		_, err = tx.Exec("INSERT INTO progress (owner_id,lesson_id,completed,created_at,updated_at) VALUES (?,?,?,?,?)", request.Owner, request.Lesson, request.Completed, request.At, request.At)
	} else {
		_, err = tx.Exec("UPDATE progress SET completed=?, updated_at=? WHERE id=? AND owner_id=?", request.Completed, request.At, item.ID, request.Owner)
	}
	if err != nil {
		return nil, err
	}
	item, err = scanProgress(tx.QueryRow("SELECT "+progressFields+" FROM progress WHERE owner_id=? AND lesson_id=?", request.Owner, request.Lesson))
	if err != nil {
		return nil, err
	}
	if _, err = tx.Exec("INSERT INTO audit(progress_id,completed,at) VALUES (?,?,?)", item.ID, request.Completed, request.At); err != nil {
		return nil, err
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	return Change{Item: item, Changed: true}, nil
}

func (r *Repository) listProgress(request Request) (any, error) {
	rows, err := r.db.Query("SELECT "+progressFields+" FROM progress WHERE owner_id=? AND id>? ORDER BY id ASC LIMIT ?", request.Owner, request.Cursor, request.Limit+1)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	page := Page[Progress]{Items: []Progress{}}
	for rows.Next() {
		item, err := scanProgress(rows)
		if err != nil {
			return nil, err
		}
		page.Items = append(page.Items, item)
	}
	if err = rows.Err(); err != nil {
		return nil, err
	}
	if len(page.Items) > request.Limit {
		page.Items = page.Items[:request.Limit]
		id := page.Items[len(page.Items)-1].ID
		page.NextCursor = &id
	}
	return page, nil
}

func (r *Repository) listAudit(request Request) (any, error) {
	rows, err := r.db.Query("SELECT a.id,a.progress_id,a.completed,a.at FROM audit a JOIN progress p ON p.id=a.progress_id WHERE p.owner_id=? AND a.id>? ORDER BY a.id ASC LIMIT ?", request.Owner, request.Cursor, request.Limit+1)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	page := Page[Audit]{Items: []Audit{}}
	for rows.Next() {
		var item Audit
		if err = rows.Scan(&item.ID, &item.ProgressID, &item.Completed, &item.At); err != nil {
			return nil, err
		}
		page.Items = append(page.Items, item)
	}
	if err = rows.Err(); err != nil {
		return nil, err
	}
	if len(page.Items) > request.Limit {
		page.Items = page.Items[:request.Limit]
		id := page.Items[len(page.Items)-1].ID
		page.NextCursor = &id
	}
	return page, nil
}

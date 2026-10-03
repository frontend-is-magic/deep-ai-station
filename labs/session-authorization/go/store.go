package main

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"
)

// Principal is produced only by a valid server-side session lookup.
type Principal struct {
	UserID    string
	CanWrite  bool
	CSRFToken string
}

type Session struct {
	Token               string    `json:"token"`
	UserID              string    `json:"user_id"`
	CanWrite            bool      `json:"can_write"`
	ExpiresAfterSeconds int       `json:"expires_after_seconds"`
	Revoked             bool      `json:"revoked"`
	CSRFToken           string    `json:"csrf_token"`
	ExpiresAt           time.Time `json:"-"`
}

type SessionStore interface {
	Resolve(token string, now time.Time) (*Principal, error)
	Revoke(token string) error
}

type MemorySessionStore struct {
	mu       sync.Mutex
	sessions map[string]Session
}

func NewMemorySessionStore(sessions []Session, now time.Time) *MemorySessionStore {
	store := &MemorySessionStore{sessions: make(map[string]Session, len(sessions))}
	for _, session := range sessions {
		session.ExpiresAt = now.Add(time.Duration(session.ExpiresAfterSeconds) * time.Second)
		store.sessions[session.Token] = session
	}
	return store
}

func (store *MemorySessionStore) Resolve(token string, now time.Time) (*Principal, error) {
	store.mu.Lock()
	defer store.mu.Unlock()
	session, found := store.sessions[token]
	if !found || session.Revoked || !now.Before(session.ExpiresAt) {
		return nil, nil
	}
	return &Principal{UserID: session.UserID, CanWrite: session.CanWrite, CSRFToken: session.CSRFToken}, nil
}

func (store *MemorySessionStore) Revoke(token string) error {
	store.mu.Lock()
	defer store.mu.Unlock()
	session, found := store.sessions[token]
	if found {
		session.Revoked = true
		store.sessions[token] = session
	}
	return nil
}

type Document struct {
	ID       string `json:"id"`
	Title    string `json:"title"`
	Archived bool   `json:"archived"`
}

type StoredDocument struct {
	Document
	Owner string `json:"owner"`
}

var (
	ErrNotFound  = errors.New("document_not_found")
	ErrForbidden = errors.New("forbidden")
)

// Every access is owner-scoped. SetArchived checks ownership and write access
// under one repository lock, so callers cannot check and later update a new row.
type Repository interface {
	List(owner string) ([]Document, error)
	Find(owner, id string) (*Document, error)
	SetArchived(owner, id string, canWrite, archived bool) (*Document, error)
}

type MemoryRepository struct {
	mu        sync.Mutex
	documents []StoredDocument
}

func NewMemoryRepository(documents []StoredDocument) *MemoryRepository {
	return &MemoryRepository{documents: append([]StoredDocument(nil), documents...)}
}

func (repository *MemoryRepository) List(owner string) ([]Document, error) {
	repository.mu.Lock()
	defer repository.mu.Unlock()
	items := []Document{}
	for _, document := range repository.documents {
		if document.Owner == owner {
			items = append(items, document.Document)
		}
	}
	sort.Slice(items, func(i, j int) bool { return items[i].ID < items[j].ID })
	return items, nil
}

func (repository *MemoryRepository) Find(owner, id string) (*Document, error) {
	repository.mu.Lock()
	defer repository.mu.Unlock()
	for _, document := range repository.documents {
		if document.Owner == owner && document.ID == id {
			result := document.Document
			return &result, nil
		}
	}
	return nil, ErrNotFound
}

func (repository *MemoryRepository) SetArchived(owner, id string, canWrite, archived bool) (*Document, error) {
	repository.mu.Lock()
	defer repository.mu.Unlock()
	for index, document := range repository.documents {
		if document.Owner == owner && document.ID == id {
			if !canWrite {
				return nil, ErrForbidden
			}
			repository.documents[index].Archived = archived
			result := repository.documents[index].Document
			return &result, nil
		}
	}
	return nil, ErrNotFound
}

type Fixtures struct {
	Sessions  []Session        `json:"sessions"`
	Documents []StoredDocument `json:"documents"`
}

// Source checkouts share ../shared; the downloadable ZIP places data at root.
func readLabData(name string) ([]byte, error) {
	data, err := os.ReadFile(name)
	if errors.Is(err, os.ErrNotExist) {
		return os.ReadFile(filepath.Join("..", "shared", name))
	}
	return data, err
}

func LoadFixtures() (Fixtures, error) {
	data, err := readLabData("fixtures.json")
	var fixtures Fixtures
	if err != nil || json.Unmarshal(data, &fixtures) != nil || len(fixtures.Sessions) == 0 || len(fixtures.Documents) == 0 {
		return Fixtures{}, errors.New("fixed teaching fixtures unavailable")
	}
	return fixtures, nil
}

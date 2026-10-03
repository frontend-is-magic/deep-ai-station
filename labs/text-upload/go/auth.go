package main

import (
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"
)

type Principal struct {
	UserID   string
	CanWrite bool
}
type Session struct {
	Token               string    `json:"token"`
	UserID              string    `json:"user_id"`
	CanWrite            bool      `json:"can_write"`
	ExpiresAfterSeconds int       `json:"expires_after_seconds"`
	Revoked             bool      `json:"revoked"`
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
	return &Principal{UserID: session.UserID, CanWrite: session.CanWrite}, nil
}
func (store *MemorySessionStore) Revoke(token string) error {
	store.mu.Lock()
	defer store.mu.Unlock()
	if session, found := store.sessions[token]; found {
		session.Revoked = true
		store.sessions[token] = session
	}
	return nil
}

// Test/startup injection only; there is no HTTP role or session-control endpoint.
func (store *MemorySessionStore) SetWritePermission(token string, allowed bool) {
	store.mu.Lock()
	defer store.mu.Unlock()
	if session, found := store.sessions[token]; found {
		session.CanWrite = allowed
		store.sessions[token] = session
	}
}
func readLabData(name string) ([]byte, error) {
	data, err := os.ReadFile(name)
	if errors.Is(err, os.ErrNotExist) {
		return os.ReadFile(filepath.Join("..", "shared", name))
	}
	return data, err
}
func LoadSessions() ([]Session, error) {
	data, err := readLabData("fixtures.json")
	var fixtures struct {
		Sessions []Session `json:"sessions"`
	}
	if err != nil || json.Unmarshal(data, &fixtures) != nil || len(fixtures.Sessions) == 0 {
		return nil, errors.New("fixed teaching fixtures unavailable")
	}
	return fixtures.Sessions, nil
}
func asciiLower(value string) string {
	result := []byte(value)
	for index, c := range result {
		if c >= 'A' && c <= 'Z' {
			result[index] = c + ('a' - 'A')
		}
	}
	return string(result)
}
func headerValues(headers http.Header, name string) []string {
	values := []string{}
	for key, items := range headers {
		if asciiLower(key) == asciiLower(name) {
			values = append(values, items...)
		}
	}
	return values
}

var tokenPattern = regexp.MustCompile(`^[A-Za-z0-9._~+/-]+=*$`)

func bearerToken(request *http.Request) (string, string) {
	authorization := headerValues(request.Header, "Authorization")
	cookieCount := 0
	for _, line := range headerValues(request.Header, "Cookie") {
		for _, item := range strings.Split(line, ";") {
			if strings.Trim(strings.SplitN(item, "=", 2)[0], " \t") == "__Host-lab_session" {
				cookieCount++
			}
		}
	}
	if len(authorization) > 1 || cookieCount > 1 || (len(authorization) > 0 && cookieCount > 0) || (len(authorization) == 1 && strings.Contains(authorization[0], ",")) {
		return "", "ambiguous_credentials"
	}
	if len(authorization) != 1 {
		return "", "authentication_required"
	}
	parts := strings.SplitN(authorization[0], " ", 2)
	if len(parts) != 2 || asciiLower(parts[0]) != "bearer" {
		return "", "authentication_required"
	}
	token := strings.TrimLeft(parts[1], " ")
	if len(token) < 1 || len(token) > 128 || !tokenPattern.MatchString(token) {
		return "", "authentication_required"
	}
	return token, ""
}
func resolveSession(store SessionStore, token string, now time.Time) (principal *Principal, err error) {
	defer func() {
		if recover() != nil {
			principal = nil
			err = errors.New("session store failure")
		}
	}()
	return store.Resolve(token, now)
}

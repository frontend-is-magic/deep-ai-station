package main

import (
	"bytes"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"regexp"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/gin-gonic/gin"
)

const maxRequestBytes = 4096
const sessionCookieName = "__Host-lab_session"

var tokenPattern = regexp.MustCompile(`^[A-Za-z0-9._~+/-]+=*$`)

type Config struct {
	Clock         func() time.Time
	Sessions      SessionStore
	Repository    Repository
	AllowCookie   *bool
	AllowedOrigin string
}

type application struct {
	clock         func() time.Time
	sessions      SessionStore
	repository    Repository
	allowCookie   bool
	allowedOrigin string
	// Teaching consistency within this application, not distributed revocation.
	mu sync.Mutex
}

func asciiLower(value string) string {
	result := []byte(value)
	for index, character := range result {
		if character >= 'A' && character <= 'Z' {
			result[index] = character + ('a' - 'A')
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

func validToken(value string) bool {
	return len(value) >= 1 && len(value) <= 128 && tokenPattern.MatchString(value)
}

func credentials(request *http.Request, allowCookie bool) (token string, cookie bool, code string) {
	authorization := headerValues(request.Header, "Authorization")
	cookieValues := []string{}
	for _, line := range headerValues(request.Header, "Cookie") {
		for _, item := range strings.Split(line, ";") {
			parts := strings.SplitN(item, "=", 2)
			if strings.Trim(parts[0], " \t") != sessionCookieName {
				continue
			}
			value := ""
			if len(parts) == 2 {
				value = strings.Trim(parts[1], " \t")
			}
			cookieValues = append(cookieValues, value)
		}
	}
	if len(authorization) > 1 || len(cookieValues) > 1 || (len(authorization) > 0 && len(cookieValues) > 0) || (len(authorization) == 1 && strings.Contains(authorization[0], ",")) {
		return "", false, "ambiguous_credentials"
	}
	if len(authorization) == 1 {
		parts := strings.SplitN(authorization[0], " ", 2)
		if len(parts) != 2 || asciiLower(parts[0]) != "bearer" {
			return "", false, "authentication_required"
		}
		value := strings.TrimLeft(parts[1], " ")
		if !validToken(value) {
			return "", false, "authentication_required"
		}
		return value, false, ""
	}
	if len(cookieValues) == 1 && allowCookie && validToken(cookieValues[0]) {
		return cookieValues[0], true, ""
	}
	return "", false, "authentication_required"
}

func SessionCookie(token string) (*http.Cookie, error) {
	if !validToken(token) {
		return nil, errors.New("invalid teaching session token")
	}
	return &http.Cookie{Name: sessionCookieName, Value: token, Path: "/", Secure: true, HttpOnly: true, SameSite: http.SameSiteStrictMode}, nil
}

func ClearSessionCookie() *http.Cookie {
	return &http.Cookie{Name: sessionCookieName, Value: "", Path: "/", Secure: true, HttpOnly: true, SameSite: http.SameSiteStrictMode, MaxAge: -1}
}

func writeError(context *gin.Context, status int, code string) {
	if status == http.StatusUnauthorized {
		context.Header("WWW-Authenticate", `Bearer realm="session-authorization"`)
	}
	context.AbortWithStatusJSON(status, gin.H{"error": code})
}

// encoding/json normally accepts duplicate keys. Token-by-token parsing retains
// each occurrence, and the exact schema has no string values or nested objects.
func parseBody(raw []byte, patch bool) (bool, bool) {
	if !utf8.Valid(raw) {
		return false, false
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	token, err := decoder.Token()
	if err != nil || token != json.Delim('{') {
		return false, false
	}
	seen := false
	archived := false
	for decoder.More() {
		key, err := decoder.Token()
		if err != nil || !patch || key != "archived" || seen {
			return false, false
		}
		seen = true
		var value any
		if decoder.Decode(&value) != nil {
			return false, false
		}
		var ok bool
		archived, ok = value.(bool)
		if !ok {
			return false, false
		}
	}
	if token, err = decoder.Token(); err != nil || token != json.Delim('}') {
		return false, false
	}
	if _, err = decoder.Token(); err != io.EOF || patch != seen {
		return false, false
	}
	return archived, true
}

func validCSRF(request *http.Request, principal *Principal, origin string) bool {
	origins := headerValues(request.Header, "Origin")
	csrf := headerValues(request.Header, "X-CSRF-Token")
	return len(origins) == 1 && origins[0] == origin && len(csrf) == 1 && len(csrf[0]) <= 128 && subtle.ConstantTimeCompare([]byte(csrf[0]), []byte(principal.CSRFToken)) == 1
}

// Store boundaries deliberately discard internal errors, including panics, so
// injected database failures cannot leak SQL, paths, request data, or credentials.
func resolveSession(store SessionStore, token string, now time.Time) (principal *Principal, err error) {
	defer func() {
		if recover() != nil {
			principal = nil
			err = errors.New("session store failure")
		}
	}()
	return store.Resolve(token, now)
}
func revokeSession(store SessionStore, token string) (err error) {
	defer func() {
		if recover() != nil {
			err = errors.New("session store failure")
		}
	}()
	return store.Revoke(token)
}
func repositoryCall[T any](call func() (T, error)) (result T, err error) {
	defer func() {
		if recover() != nil {
			err = errors.New("repository failure")
		}
	}()
	return call()
}

func (app *application) private(context *gin.Context) {
	token, cookie, code := credentials(context.Request, app.allowCookie)
	if code != "" {
		status := http.StatusUnauthorized
		if code == "ambiguous_credentials" {
			status = http.StatusBadRequest
		}
		writeError(context, status, code)
		return
	}
	principal, err := resolveSession(app.sessions, token, app.clock())
	if err != nil {
		writeError(context, 503, "session_store_unavailable")
		return
	}
	if principal == nil {
		writeError(context, 401, "authentication_required")
		return
	}
	if context.Request.URL.RawQuery != "" {
		writeError(context, 422, "invalid_input")
		return
	}
	write := context.Request.Method == http.MethodPatch || context.Request.Method == http.MethodPost
	archived := false
	if write {
		raw, err := io.ReadAll(io.LimitReader(context.Request.Body, maxRequestBytes+1))
		if len(raw) > maxRequestBytes {
			writeError(context, 413, "request_too_large")
			return
		}
		if err != nil {
			writeError(context, 422, "invalid_input")
			return
		}
		media := asciiLower(strings.Trim(strings.SplitN(context.GetHeader("Content-Type"), ";", 2)[0], " \t"))
		if media != "application/json" {
			writeError(context, 415, "unsupported_media_type")
			return
		}
		var valid bool
		archived, valid = parseBody(raw, context.Request.Method == http.MethodPatch)
		if !valid {
			writeError(context, 422, "invalid_input")
			return
		}
	}
	// Never hold the application/store/repository lock while receiving a body.
	// An independently completed logout must invalidate a previously authenticated
	// slow request. Only the final check and bounded in-process operation serialize.
	app.mu.Lock()
	defer app.mu.Unlock()
	principal, err = resolveSession(app.sessions, token, app.clock())
	if err != nil {
		writeError(context, 503, "session_store_unavailable")
		return
	}
	if principal == nil {
		writeError(context, 401, "authentication_required")
		return
	}
	if write && cookie && !validCSRF(context.Request, principal, app.allowedOrigin) {
		writeError(context, 403, "csrf_failed")
		return
	}
	switch context.FullPath() {
	case "/me":
		context.JSON(200, gin.H{"user_id": principal.UserID})
	case "/csrf":
		context.JSON(200, gin.H{"csrf_token": principal.CSRFToken})
	case "/logout":
		if revokeSession(app.sessions, token) != nil {
			writeError(context, 503, "session_store_unavailable")
			return
		}
		if cookie {
			http.SetCookie(context.Writer, ClearSessionCookie())
		}
		context.JSON(200, gin.H{"ok": true})
	case "/documents":
		items, err := repositoryCall(func() ([]Document, error) { return app.repository.List(principal.UserID) })
		if err != nil {
			writeError(context, 503, "repository_unavailable")
			return
		}
		context.JSON(200, gin.H{"items": items})
	case "/documents/:id":
		var document *Document
		if write {
			document, err = repositoryCall(func() (*Document, error) {
				return app.repository.SetArchived(principal.UserID, context.Param("id"), principal.CanWrite, archived)
			})
		} else {
			document, err = repositoryCall(func() (*Document, error) { return app.repository.Find(principal.UserID, context.Param("id")) })
		}
		switch {
		case errors.Is(err, ErrNotFound):
			writeError(context, 404, "document_not_found")
		case errors.Is(err, ErrForbidden):
			writeError(context, 403, "forbidden")
		case err != nil:
			writeError(context, 503, "repository_unavailable")
		case document == nil:
			writeError(context, 404, "document_not_found")
		default:
			context.JSON(200, document)
		}
	}
}

func NewRouter(config Config) (*gin.Engine, error) {
	clock := config.Clock
	if clock == nil {
		clock = time.Now
	}
	if config.Sessions == nil || config.Repository == nil {
		fixtures, err := LoadFixtures()
		if err != nil {
			return nil, err
		}
		if config.Sessions == nil {
			config.Sessions = NewMemorySessionStore(fixtures.Sessions, clock())
		}
		if config.Repository == nil {
			config.Repository = NewMemoryRepository(fixtures.Documents)
		}
	}
	allowCookie := true
	if config.AllowCookie != nil {
		allowCookie = *config.AllowCookie
	}
	origin := config.AllowedOrigin
	if origin == "" {
		origin = "https://lab.example.test"
	}
	app := &application{clock: clock, sessions: config.Sessions, repository: config.Repository, allowCookie: allowCookie, allowedOrigin: origin}
	gin.SetMode(gin.ReleaseMode)
	router := gin.New()
	router.RedirectTrailingSlash = false
	router.RedirectFixedPath = false
	router.HandleMethodNotAllowed = true
	router.Use(func(context *gin.Context) { context.Header("Cache-Control", "no-store"); context.Next() })
	router.Use(gin.CustomRecoveryWithWriter(io.Discard, func(context *gin.Context, _ any) { writeError(context, 500, "request_failed") }))
	router.GET("/health", func(context *gin.Context) {
		context.JSON(200, gin.H{"status": "ok", "lab": "session-authorization-v1"})
	})
	for _, path := range []string{"/me", "/csrf", "/documents", "/documents/:id"} {
		router.GET(path, app.private)
	}
	router.PATCH("/documents/:id", app.private)
	router.POST("/logout", app.private)
	router.NoRoute(func(context *gin.Context) { writeError(context, 404, "route_not_found") })
	router.NoMethod(func(context *gin.Context) { writeError(context, 405, "method_not_allowed") })
	return router, nil
}

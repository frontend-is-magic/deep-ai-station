package main

import (
	"errors"
	"github.com/gin-gonic/gin"
	"io"
	"net/http"
	"regexp"
	"strings"
	"time"
	"unicode/utf8"
)

const maxRequestBytes = 4096

var mediaPattern = regexp.MustCompile(`^(text/plain|text/markdown)(?:[ \t]*;[ \t]*charset[ \t]*=[ \t]*(?:"utf-8"|utf-8))?$`)
var filenamePattern = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,59}\.(txt|md)$`)

type Config struct {
	Clock      func() time.Time
	Sessions   SessionStore
	Repository Repository
}
type application struct {
	clock      func() time.Time
	sessions   SessionStore
	repository Repository
}

func writeError(context *gin.Context, status int, code string) {
	if status == http.StatusUnauthorized {
		context.Header("WWW-Authenticate", `Bearer realm="text-upload"`)
	}
	context.AbortWithStatusJSON(status, gin.H{"error": code})
}
func (app *application) principal(context *gin.Context, token string) *Principal {
	principal, err := resolveSession(app.sessions, token, app.clock())
	if err != nil {
		writeError(context, 503, "session_store_unavailable")
		return nil
	}
	if principal == nil {
		writeError(context, 401, "authentication_required")
		return nil
	}
	return principal
}
func uploadHeaders(request *http.Request) (filename, mediaType, code string, status int) {
	media := headerValues(request.Header, "Content-Type")
	names := headerValues(request.Header, "X-Filename")
	if len(media) > 1 || len(names) > 1 {
		return "", "", "ambiguous_upload_headers", 400
	}
	if len(headerValues(request.Header, "Content-Encoding")) > 0 || len(media) != 1 {
		return "", "", "unsupported_media_type", 415
	}
	match := mediaPattern.FindStringSubmatch(asciiLower(strings.Trim(media[0], " \t")))
	if match == nil {
		return "", "", "unsupported_media_type", 415
	}
	mediaType = match[1]
	if len(names) != 1 {
		return "", "", "invalid_filename", 422
	}
	filename = strings.Trim(names[0], " \t")
	canonicalName := asciiLower(filename)
	if !filenamePattern.MatchString(canonicalName) || (strings.HasSuffix(canonicalName, ".txt") && mediaType != "text/plain") || (strings.HasSuffix(canonicalName, ".md") && mediaType != "text/markdown") {
		return "", "", "invalid_filename", 422
	}
	return filename, mediaType, "", 0
}
func validText(content []byte) bool {
	if len(content) == 0 || !utf8.Valid(content) {
		return false
	}
	meaningful := false
	for _, r := range string(content) {
		if (r <= 0x1f && r != '\t' && r != '\n' && r != '\r') || (r >= 0x7f && r <= 0x9f) || r == 0xfeff {
			return false
		}
		if r != ' ' && r != '\t' && r != '\n' && r != '\r' {
			meaningful = true
		}
	}
	return meaningful
}
func (app *application) private(context *gin.Context) {
	token, code := bearerToken(context.Request)
	if code != "" {
		status := 401
		if code == "ambiguous_credentials" {
			status = 400
		}
		writeError(context, status, code)
		return
	}
	principal := app.principal(context, token)
	if principal == nil {
		return
	}
	// SessionStore implementations may reuse a mutable Principal pointer.
	// Capture the initial identity before reading any caller-controlled body.
	originalOwner := principal.UserID
	if context.Request.URL.RawQuery != "" {
		writeError(context, 422, "invalid_input")
		return
	}
	if context.Request.Method == http.MethodPost {
		if !principal.CanWrite {
			writeError(context, 403, "forbidden")
			return
		}
		// LimitReader stops after one byte beyond the policy limit, independent of
		// Content-Length. No session, repository, or application lock spans reading.
		content, err := io.ReadAll(io.LimitReader(context.Request.Body, maxRequestBytes+1))
		if len(content) > maxRequestBytes {
			writeError(context, 413, "request_too_large")
			return
		}
		if err != nil {
			writeError(context, 500, "request_failed")
			return
		}
		filename, mediaType, code, status := uploadHeaders(context.Request)
		if code != "" {
			writeError(context, status, code)
			return
		}
		if !validText(content) {
			writeError(context, 422, "invalid_text")
			return
		}
		principal = app.principal(context, token)
		if principal == nil {
			return
		}
		if principal.UserID != originalOwner {
			writeError(context, 401, "authentication_required")
			return
		}
		if !principal.CanWrite {
			writeError(context, 403, "forbidden")
			return
		}
		// Only this closure can set the private latch. Repository errors never grant
		// permission to expose an arbitrary HTTP error or internal diagnostic.
		authStatus, authCode, checks := 0, "", 0
		beforeWrite := func() error {
			checks++
			fresh, failure := resolveSession(app.sessions, token, app.clock())
			switch {
			case failure != nil:
				authStatus, authCode = 503, "session_store_unavailable"
			case fresh == nil || fresh.UserID != originalOwner:
				authStatus, authCode = 401, "authentication_required"
			case !fresh.CanWrite:
				authStatus, authCode = 403, "forbidden"
			}
			if authStatus != 0 {
				return errors.New("commit authorization denied")
			}
			return nil
		}
		metadata, err := repositoryCall(func() (*Metadata, error) {
			return app.repository.Commit(originalOwner, filename, mediaType, content, beforeWrite)
		})
		if authStatus != 0 {
			writeError(context, authStatus, authCode)
			return
		}
		if err == nil && checks != 1 {
			err = errors.New("authorization missing")
		}
		if errors.Is(err, ErrQuotaExceeded) {
			writeError(context, 409, "quota_exceeded")
			return
		}
		if errors.Is(err, ErrResultUnconfirmed) {
			writeError(context, 503, "result_unconfirmed")
			return
		}
		if err != nil {
			writeError(context, 503, "repository_unavailable")
			return
		}
		context.JSON(201, metadata)
		return
	}
	switch context.FullPath() {
	case "/documents":
		documents, err := repositoryCall(func() ([]Metadata, error) { return app.repository.List(principal.UserID) })
		if err != nil {
			writeError(context, 503, "repository_unavailable")
			return
		}
		context.JSON(200, gin.H{"documents": documents})
	case "/documents/:id":
		metadata, err := repositoryCall(func() (*Metadata, error) { return app.repository.Find(principal.UserID, context.Param("id")) })
		if err != nil {
			writeError(context, 503, "repository_unavailable")
			return
		}
		if metadata == nil {
			writeError(context, 404, "document_not_found")
			return
		}
		context.JSON(200, metadata)
	case "/documents/:id/content":
		download, err := repositoryCall(func() (*Download, error) { return app.repository.Content(principal.UserID, context.Param("id")) })
		if err != nil {
			writeError(context, 503, "repository_unavailable")
			return
		}
		if download == nil {
			writeError(context, 404, "document_not_found")
			return
		}
		extension := "txt"
		if download.Metadata.MediaType == "text/markdown" {
			extension = "md"
		}
		context.Header("Content-Disposition", `attachment; filename="upload-`+download.Metadata.ID+`.`+extension+`"`)
		context.Data(200, "application/octet-stream", download.Content)
	}
}
func NewRouter(config Config) (*gin.Engine, error) {
	if config.Clock == nil {
		config.Clock = time.Now
	}
	if config.Sessions == nil {
		sessions, err := LoadSessions()
		if err != nil {
			return nil, err
		}
		config.Sessions = NewMemorySessionStore(sessions, config.Clock())
	}
	if config.Repository == nil {
		config.Repository = NewMemoryRepository()
	}
	app := &application{clock: config.Clock, sessions: config.Sessions, repository: config.Repository}
	gin.SetMode(gin.ReleaseMode)
	router := gin.New()
	router.RedirectTrailingSlash = false
	router.RedirectFixedPath = false
	router.HandleMethodNotAllowed = true
	router.Use(func(context *gin.Context) {
		context.Header("Cache-Control", "no-store")
		context.Header("X-Content-Type-Options", "nosniff")
		context.Next()
	})
	router.Use(gin.CustomRecoveryWithWriter(io.Discard, func(context *gin.Context, _ any) { writeError(context, 500, "request_failed") }))
	router.GET("/health", func(context *gin.Context) { context.JSON(200, gin.H{"status": "ok", "lab": "text-upload-v1"}) })
	router.GET("/documents", app.private)
	router.POST("/documents", app.private)
	router.GET("/documents/:id", app.private)
	router.GET("/documents/:id/content", app.private)
	router.NoRoute(func(context *gin.Context) { writeError(context, 404, "route_not_found") })
	router.NoMethod(func(context *gin.Context) { writeError(context, 405, "method_not_allowed") })
	return router, nil
}

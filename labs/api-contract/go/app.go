package main

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/gin-gonic/gin"
)

const maxRequestBytes = 4096

func writeError(context *gin.Context, status int, code string) {
	context.AbortWithStatusJSON(status, gin.H{"error": code})
}

func writeServiceError(context *gin.Context, err error) {
	var failure ServiceError
	if errors.As(err, &failure) {
		writeError(context, failure.Status, failure.Code)
		return
	}
	writeError(context, http.StatusInternalServerError, "request_failed")
}

// JSON permits escaped surrogate pairs; encoding/json otherwise replaces lone
// surrogates with U+FFFD. Preserve the shared contract by rejecting those escapes.
func validUnicodeEscapes(value []byte) bool {
	for index := 1; index < len(value)-1; index++ {
		if value[index] != '\\' {
			continue
		}
		index++
		if value[index] != 'u' {
			continue
		}
		code, err := strconv.ParseUint(string(value[index+1:index+5]), 16, 16)
		if err != nil {
			return false
		}
		index += 4
		if code >= 0xDC00 && code <= 0xDFFF {
			return false
		}
		if code >= 0xD800 && code <= 0xDBFF {
			if index+6 >= len(value)-1 || value[index+1] != '\\' || value[index+2] != 'u' {
				return false
			}
			low, err := strconv.ParseUint(string(value[index+3:index+7]), 16, 16)
			if err != nil || low < 0xDC00 || low > 0xDFFF {
				return false
			}
			index += 6
		}
	}
	return true
}

func parseQuestion(raw []byte) (string, bool) {
	if !utf8.Valid(raw) {
		return "", false
	}
	var fields map[string]json.RawMessage
	// Unmarshal rejects malformed JSON, non-object roots, and trailing JSON.
	if json.Unmarshal(raw, &fields) != nil || len(fields) != 1 {
		return "", false
	}
	value, exists := fields["question"]
	if !exists || len(value) == 0 || value[0] != '"' {
		return "", false
	}
	var question string
	if json.Unmarshal(value, &question) != nil || !validUnicodeEscapes(value) {
		return "", false
	}
	question = strings.TrimSpace(question)
	length := utf8.RuneCountInString(question)
	return question, length >= 1 && length <= 500
}

func NewRouter(repository Repository) *gin.Engine {
	gin.SetMode(gin.ReleaseMode)
	router := gin.New()
	router.Use(gin.CustomRecoveryWithWriter(io.Discard, func(context *gin.Context, _ any) {
		writeError(context, http.StatusInternalServerError, "request_failed")
	}))
	service := NewService(repository)
	router.GET("/health", func(context *gin.Context) {
		context.JSON(http.StatusOK, gin.H{"status": "ok", "lab": "api-contract-v1"})
	})
	router.GET("/lessons/:id", func(context *gin.Context) {
		result, err := service.Find(context.Param("id"))
		if err != nil {
			writeServiceError(context, err)
			return
		}
		context.JSON(http.StatusOK, result)
	})
	router.POST("/search", func(context *gin.Context) {
		// Read at most one byte beyond the limit, regardless of Content-Length.
		raw, err := io.ReadAll(io.LimitReader(context.Request.Body, maxRequestBytes+1))
		if len(raw) > maxRequestBytes {
			writeError(context, http.StatusRequestEntityTooLarge, "request_too_large")
			return
		}
		if err != nil {
			writeError(context, http.StatusUnprocessableEntity, "invalid_input")
			return
		}
		// Parameters do not affect media matching; JSON is always strict UTF-8.
		mediaType := asciiLower(strings.Trim(strings.SplitN(context.GetHeader("Content-Type"), ";", 2)[0], " \t"))
		if mediaType != "application/json" {
			writeError(context, http.StatusUnsupportedMediaType, "unsupported_media_type")
			return
		}
		question, valid := parseQuestion(raw)
		if !valid {
			writeError(context, http.StatusUnprocessableEntity, "invalid_input")
			return
		}
		result, err := service.Search(question)
		if err != nil {
			writeServiceError(context, err)
			return
		}
		context.JSON(http.StatusOK, result)
	})
	router.NoRoute(func(context *gin.Context) {
		writeError(context, http.StatusNotFound, "route_not_found")
	})
	return router
}

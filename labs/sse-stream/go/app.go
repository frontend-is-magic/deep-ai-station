package main

import (
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"time"

	"github.com/gin-gonic/gin"
)

type Config struct {
	Producer func(scenario string) Producer
	Deadline time.Duration
	// WithDeadline is a deterministic test seam. Production uses one total timeout.
	WithDeadline func(context.Context, time.Duration) (context.Context, context.CancelFunc)
}

func newRunID() string {
	var id [16]byte
	// crypto/rand.Read fills the buffer or terminates on a system randomness failure.
	_, _ = rand.Read(id[:])
	id[6] = (id[6] & 0x0f) | 0x40
	id[8] = (id[8] & 0x3f) | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", id[:4], id[4:6], id[6:8], id[8:10], id[10:])
}

func scenarioFrom(raw string) (string, bool) {
	query, err := url.ParseQuery(raw)
	if err != nil || len(query) != 1 || len(query["scenario"]) != 1 {
		return "", false
	}
	scenario := query["scenario"][0]
	switch scenario {
	case "success", "error", "timeout", "hold":
		return scenario, true
	default:
		return "", false
	}
}

func NewRouter(config Config) (*gin.Engine, error) {
	fixtures, err := loadFixtures()
	if err != nil {
		return nil, err
	}
	if config.Deadline < 0 {
		return nil, errors.New("deadline must be positive")
	}
	if config.Deadline == 0 {
		config.Deadline = time.Duration(fixtures.DeadlineMS) * time.Millisecond
	}
	if config.WithDeadline == nil {
		config.WithDeadline = context.WithTimeout
	}
	if config.Producer == nil {
		config.Producer = func(scenario string) Producer {
			return &fixtureProducer{scenario: scenario, texts: append([]string(nil), fixtures.Texts...), step: time.Duration(fixtures.StepMS) * time.Millisecond}
		}
	}
	router := gin.New()
	router.RedirectTrailingSlash = false
	router.RedirectFixedPath = false
	router.GET("/health", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"ok": true, "lab": "sse-stream"})
	})
	router.GET("/stream", func(c *gin.Context) {
		scenario, valid := scenarioFrom(c.Request.URL.RawQuery)
		if !valid {
			c.JSON(http.StatusBadRequest, gin.H{"error": gin.H{"code": "invalid_request", "message": "仅支持一个有效的 scenario 参数"}})
			return
		}
		parent := c.Request.Context()
		if parent.Err() != nil {
			return
		}
		// Request.Context is canceled when the client connection closes. The same
		// child covers every Next call; a later frame never renews its deadline.
		ctx, cancel := config.WithDeadline(parent, config.Deadline)
		defer cancel()
		producer := config.Producer(scenario)
		defer producer.Close()
		runID := newRunID()
		c.Header("Content-Type", "text/event-stream; charset=utf-8")
		c.Header("Cache-Control", "no-store")
		c.Header("X-Content-Type-Options", "nosniff")
		c.Header("X-Accel-Buffering", "no")
		emit := func(name string, data gin.H) bool {
			// Deadline errors can still be reported, but confirmed disconnects cannot.
			if parent.Err() != nil {
				return false
			}
			encoded, err := json.Marshal(data)
			if err != nil {
				return false
			}
			if _, err = fmt.Fprintf(c.Writer, "event: %s\ndata: %s\n\n", name, encoded); err != nil {
				return false
			}
			return http.NewResponseController(c.Writer).Flush() == nil
		}
		if !emit("start", gin.H{"run_id": runID, "scenario": scenario}) {
			return
		}
		seq := 0
		for {
			if parent.Err() != nil {
				return
			}
			text, nextErr := producer.Next(ctx)
			if parent.Err() != nil {
				return
			}
			if errors.Is(ctx.Err(), context.DeadlineExceeded) {
				emit("error", gin.H{"run_id": runID, "code": "deadline_exceeded", "message": "教学运行超时"})
				return
			}
			if ctx.Err() != nil {
				return
			}
			if errors.Is(nextErr, io.EOF) {
				emit("done", gin.H{"run_id": runID, "seq": seq})
				return
			}
			if nextErr != nil {
				emit("error", gin.H{"run_id": runID, "code": "producer_failed", "message": "教学数据源失败"})
				return
			}
			seq++
			if !emit("delta", gin.H{"run_id": runID, "seq": seq, "text": text}) {
				return
			}
		}
	})
	return router, nil
}

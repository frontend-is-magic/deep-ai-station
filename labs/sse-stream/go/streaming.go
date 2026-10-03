package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"time"
)

// Next must honor ctx; a request calls it serially and then calls Close exactly once.
// No worker goroutine or shared producer is needed for this fixed teaching source.
type Producer interface {
	Next(ctx context.Context) (string, error)
	Close()
}

type Fixtures struct {
	Texts      []string `json:"texts"`
	DeadlineMS int      `json:"deadline_ms"`
	StepMS     int      `json:"step_ms"`
}

func loadFixtures() (Fixtures, error) {
	data, err := os.ReadFile("fixtures.json")
	if errors.Is(err, os.ErrNotExist) {
		data, err = os.ReadFile(filepath.Join("..", "shared", "fixtures.json"))
	}
	var fixtures Fixtures
	if err != nil || json.Unmarshal(data, &fixtures) != nil || len(fixtures.Texts) != 3 || fixtures.DeadlineMS <= 0 || fixtures.StepMS < 0 {
		return Fixtures{}, errors.New("fixed teaching fixtures unavailable")
	}
	return fixtures, nil
}

type fixtureProducer struct {
	scenario string
	texts    []string
	step     time.Duration
	index    int
	closed   bool
}

func (producer *fixtureProducer) Next(ctx context.Context) (string, error) {
	if err := ctx.Err(); err != nil {
		return "", err
	}
	if producer.closed {
		return "", io.ErrClosedPipe
	}
	if producer.index > 0 {
		switch producer.scenario {
		case "error":
			return "", errors.New("private teaching producer failure")
		case "hold", "timeout":
			<-ctx.Done()
			return "", ctx.Err()
		}
	}
	if producer.index == len(producer.texts) {
		return "", io.EOF
	}
	if producer.index > 0 && producer.step > 0 {
		timer := time.NewTimer(producer.step)
		defer timer.Stop()
		select {
		case <-ctx.Done():
			return "", ctx.Err()
		case <-timer.C:
		}
	}
	if err := ctx.Err(); err != nil {
		return "", err
	}
	text := producer.texts[producer.index]
	producer.index++
	return text, nil
}

func (producer *fixtureProducer) Close() { producer.closed = true }

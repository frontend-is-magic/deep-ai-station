package main

import (
	"encoding/json"
	"errors"
	"math"
	"regexp"
	"time"
	"unicode/utf8"
)

var errInvalid = errors.New("invalid_input")
var errSchema = errors.New("unsupported_schema")
var identifier = regexp.MustCompile(`^[a-z][a-z0-9-]{0,63}$`)
var timestamp = regexp.MustCompile(`^[1-9][0-9]{3}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$`)

const timeLayout = "2006-01-02T15:04:05.000Z"

type Request struct {
	Op        string
	Owner     string
	Lesson    string
	Completed bool
	At        string
	Cursor    int64
	Limit     int
}

func parseRequest(body []byte) (Request, error) {
	var request Request
	if len(body) > 4096 || !utf8.Valid(body) {
		return request, errInvalid
	}
	var data map[string]any
	if json.Unmarshal(body, &data) != nil || data == nil {
		return request, errInvalid
	}
	request.Op, _ = data["op"].(string)
	required := []string{"op"}
	switch request.Op {
	case "migrate":
	case "set_progress":
		required = append(required, "owner_id", "lesson_id", "completed", "at")
	case "list_progress", "list_audit":
		required = append(required, "owner_id", "cursor", "limit")
	default:
		return request, errInvalid
	}
	if len(data) != len(required) {
		return request, errInvalid
	}
	for _, key := range required {
		if _, ok := data[key]; !ok {
			return request, errInvalid
		}
	}
	if request.Op == "migrate" {
		return request, nil
	}
	request.Owner, _ = data["owner_id"].(string)
	if !identifier.MatchString(request.Owner) {
		return request, errInvalid
	}
	if request.Op == "set_progress" {
		request.Lesson, _ = data["lesson_id"].(string)
		value, boolean := data["completed"].(bool)
		request.Completed = value
		request.At, _ = data["at"].(string)
		parsed, err := time.Parse(timeLayout, request.At)
		if !identifier.MatchString(request.Lesson) || !boolean || !timestamp.MatchString(request.At) || err != nil || parsed.Format(timeLayout) != request.At {
			return request, errInvalid
		}
	} else {
		cursor, ok := data["cursor"].(float64)
		limit, valid := data["limit"].(float64)
		if !ok || !valid || cursor < 0 || cursor > 9007199254740991 || math.Trunc(cursor) != cursor || limit < 1 || limit > 50 || math.Trunc(limit) != limit {
			return request, errInvalid
		}
		request.Cursor, request.Limit = int64(cursor), int(limit)
	}
	return request, nil
}

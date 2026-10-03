package main

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
)

// asciiLower matches the shared teaching contract without Unicode case folding.
func asciiLower(value string) string {
	bytes := []byte(value)
	for index, character := range bytes {
		if character >= 'A' && character <= 'Z' {
			bytes[index] = character + ('a' - 'A')
		}
	}
	return string(bytes)
}

type Lesson struct {
	ID    string `json:"id"`
	Title string `json:"title"`
	Body  string `json:"body"`
}

type Repository interface {
	Find(id string) (*Lesson, error)
	Search(question string) ([]Lesson, error)
}

type MemoryRepository struct {
	lessons []Lesson
}

func NewMemoryRepository(lessons []Lesson) *MemoryRepository {
	return &MemoryRepository{lessons: append([]Lesson(nil), lessons...)}
}

func (repository *MemoryRepository) Find(id string) (*Lesson, error) {
	for _, lesson := range repository.lessons {
		if lesson.ID == id {
			result := lesson
			return &result, nil
		}
	}
	return nil, nil
}

func (repository *MemoryRepository) Search(question string) ([]Lesson, error) {
	query := asciiLower(question)
	results := []Lesson{}
	for _, lesson := range repository.lessons {
		if strings.Contains(asciiLower(lesson.Title), query) ||
			strings.Contains(asciiLower(lesson.Body), query) {
			results = append(results, lesson)
		}
	}
	return results, nil
}

// The ZIP places fixtures at its root; source checkouts share ../shared.
func readLabData(name string) ([]byte, error) {
	data, err := os.ReadFile(name)
	if errors.Is(err, os.ErrNotExist) {
		return os.ReadFile(filepath.Join("..", "shared", name))
	}
	return data, err
}

func LoadRepository() (*MemoryRepository, error) {
	data, err := readLabData("lessons.json")
	if err != nil {
		return nil, errors.New("fixed lesson data unavailable")
	}
	var lessons []Lesson
	if err := json.Unmarshal(data, &lessons); err != nil || lessons == nil {
		return nil, errors.New("fixed lesson data invalid")
	}
	return NewMemoryRepository(lessons), nil
}

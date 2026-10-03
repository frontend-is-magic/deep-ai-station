package main

import "net/http"

type LessonSummary struct {
	ID    string `json:"id"`
	Title string `json:"title"`
}

type SearchResponse struct {
	Question string          `json:"question"`
	Items    []LessonSummary `json:"items"`
}

type ServiceError struct {
	Status int
	Code   string
}

func (failure ServiceError) Error() string { return failure.Code }

type Service struct {
	repository Repository
}

func NewService(repository Repository) *Service {
	return &Service{repository: repository}
}

func (service *Service) Find(id string) (LessonSummary, error) {
	lesson, err := service.repository.Find(id)
	if err != nil {
		return LessonSummary{}, ServiceError{http.StatusServiceUnavailable, "repository_unavailable"}
	}
	if lesson == nil {
		return LessonSummary{}, ServiceError{http.StatusNotFound, "lesson_not_found"}
	}
	return LessonSummary{ID: lesson.ID, Title: lesson.Title}, nil
}

func (service *Service) Search(question string) (SearchResponse, error) {
	lessons, err := service.repository.Search(question)
	if err != nil {
		return SearchResponse{}, ServiceError{http.StatusServiceUnavailable, "repository_unavailable"}
	}
	items := make([]LessonSummary, 0, len(lessons))
	for _, lesson := range lessons {
		items = append(items, LessonSummary{ID: lesson.ID, Title: lesson.Title})
	}
	return SearchResponse{Question: question, Items: items}, nil
}

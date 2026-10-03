package main

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"sort"
	"sync"
)

const maxOwnerDocuments = 3
const maxOwnerBytes = 8192

var ErrQuotaExceeded = errors.New("quota_exceeded")

type Metadata struct {
	ID        string `json:"id"`
	Filename  string `json:"filename"`
	MediaType string `json:"media_type"`
	SizeBytes int    `json:"size_bytes"`
	SHA256    string `json:"sha256"`
}
type Download struct {
	Metadata Metadata
	Content  []byte
}
type Repository interface {
	Commit(owner, filename, mediaType string, content []byte) (*Metadata, error)
	List(owner string) ([]Metadata, error)
	Find(owner, id string) (*Metadata, error)
	Content(owner, id string) (*Download, error)
}
type ownedDocument struct {
	owner    string
	metadata Metadata
	content  []byte
}
type usage struct{ count, bytes int }
type MemoryRepository struct {
	mu         sync.Mutex
	documents  map[string]ownedDocument
	ownerUsage map[string]usage
	nextID     int
	// A pre-publication failure seam for native transaction tests, never HTTP.
	beforePublish func() error
}

func NewMemoryRepository() *MemoryRepository {
	return &MemoryRepository{documents: make(map[string]ownedDocument), ownerUsage: make(map[string]usage), nextID: 1}
}
func (repository *MemoryRepository) Commit(owner, filename, mediaType string, content []byte) (*Metadata, error) {
	repository.mu.Lock()
	defer repository.mu.Unlock()
	used := repository.ownerUsage[owner]
	if used.count >= maxOwnerDocuments || len(content) > maxOwnerBytes-used.bytes {
		return nil, ErrQuotaExceeded
	}
	if repository.nextID > 999999 {
		return nil, errors.New("teaching id capacity reached")
	}
	// Build a private immutable candidate before touching counters or visible rows.
	data := append([]byte(nil), content...)
	digest := sha256.Sum256(data)
	metadata := Metadata{ID: fmt.Sprintf("doc-%06d", repository.nextID), Filename: filename, MediaType: mediaType, SizeBytes: len(data), SHA256: hex.EncodeToString(digest[:])}
	if repository.beforePublish != nil {
		if err := repository.beforePublish(); err != nil {
			return nil, err
		}
	}
	repository.documents[metadata.ID] = ownedDocument{owner: owner, metadata: metadata, content: data}
	repository.ownerUsage[owner] = usage{count: used.count + 1, bytes: used.bytes + len(data)}
	repository.nextID++
	return &metadata, nil
}
func (repository *MemoryRepository) List(owner string) ([]Metadata, error) {
	repository.mu.Lock()
	defer repository.mu.Unlock()
	result := []Metadata{}
	for _, document := range repository.documents {
		if document.owner == owner {
			result = append(result, document.metadata)
		}
	}
	sort.Slice(result, func(i, j int) bool { return result[i].ID < result[j].ID })
	return result, nil
}
func (repository *MemoryRepository) Find(owner, id string) (*Metadata, error) {
	repository.mu.Lock()
	defer repository.mu.Unlock()
	document, found := repository.documents[id]
	if !found || document.owner != owner {
		return nil, nil
	}
	result := document.metadata
	return &result, nil
}
func (repository *MemoryRepository) Content(owner, id string) (*Download, error) {
	repository.mu.Lock()
	defer repository.mu.Unlock()
	document, found := repository.documents[id]
	if !found || document.owner != owner {
		return nil, nil
	}
	return &Download{Metadata: document.metadata, Content: append([]byte(nil), document.content...)}, nil
}
func repositoryCall[T any](call func() (T, error)) (result T, err error) {
	defer func() {
		if recover() != nil {
			err = errors.New("repository failure")
		}
	}()
	return call()
}

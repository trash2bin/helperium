// Package ragclient provides HTTP client for calling RAG service endpoints.
//
// RAG (Retrieval-Augmented Generation) service provides semantic search
// over uploaded documents (lectures, methodical materials).
//
// All endpoints use POST with JSON body (consistent with RAG's FastAPI service).
package ragclient

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"
)

// defaultRAGURL — адрес RAG-сервиса по умолчанию.
const defaultRAGURL = "http://127.0.0.1:8082"

// defaultTimeout — таймаут HTTP-запроса к RAG по умолчанию.
const defaultTimeout = 30 * time.Second

// Client — HTTP-клиент для RAG-сервиса.
type Client struct {
	baseURL string
	http    *http.Client
}

// New создаёт новый RAG-клиент.
//
// RAG_SERVICE_URL — базовый URL RAG-сервиса (по умолчанию http://127.0.0.1:8082).
// RAG_HTTP_TIMEOUT — таймаут HTTP-запроса в секундах (по умолчанию 30).
//
// Возвращает nil если RAG не настроен.
func New() *Client {
	base := os.Getenv("RAG_SERVICE_URL")
	if base == "" {
		base = defaultRAGURL
	}
	base = strings.TrimRight(base, "/")

	timeout := defaultTimeout
	if t := os.Getenv("RAG_HTTP_TIMEOUT"); t != "" {
		if sec, err := strconv.Atoi(t); err == nil && sec > 0 {
			timeout = time.Duration(sec) * time.Second
		}
	}

	return &Client{
		baseURL: base,
		http: &http.Client{
			Timeout: timeout,
		},
	}
}

// BaseURL возвращает базовый URL RAG-сервиса.
func (c *Client) BaseURL() string {
	return c.baseURL
}

// IsAvailable проверяет доступность RAG-сервиса через /health.
func (c *Client) IsAvailable() bool {
	u := c.baseURL + "/health"
	resp, err := c.http.Get(u)
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	return resp.StatusCode == http.StatusOK
}

// SearchResult — результат семантического поиска.
type SearchResult struct {
	DocumentID    string  `json:"document_id"`
	DocumentTitle string  `json:"document_title"`
	SourcePath    string  `json:"source_path"`
	DisciplineID  *string `json:"discipline_id,omitempty"`
	ChunkID       string  `json:"chunk_id"`
	ChunkIndex    int     `json:"chunk_index"`
	Page          *int    `json:"page,omitempty"`
	Score         float64 `json:"score"`
	Content       string  `json:"content"`
}

// SearchRequest — запрос семантического поиска.
type SearchRequest struct {
	Query        string `json:"query"`
	DisciplineID string `json:"discipline_id,omitempty"`
	Limit        int    `json:"limit,omitempty"`
}

// SearchResponse — ответ семантического поиска.
type SearchResponse struct {
	Results []SearchResult `json:"results"`
	Count   int            `json:"count"`
}

// SearchDocuments выполняет семантический поиск по документам RAG.
func (c *Client) SearchDocuments(query string, disciplineID string, limit int) ([]SearchResult, error) {
	if limit <= 0 {
		limit = 5
	}
	if limit > 20 {
		limit = 20
	}

	reqBody := SearchRequest{
		Query:        query,
		DisciplineID: disciplineID,
		Limit:        limit,
	}

	data, err := json.Marshal(reqBody)
	if err != nil {
		return nil, fmt.Errorf("rag: marshal search request: %w", err)
	}

	resp, err := c.http.Post(c.baseURL+"/search", "application/json", bytes.NewReader(data))
	if err != nil {
		return nil, fmt.Errorf("rag: search request: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("rag: read search response: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("rag: search returned status %d: %s", resp.StatusCode, string(body))
	}

	var result SearchResponse
	if err := json.Unmarshal(body, &result); err != nil {
		return nil, fmt.Errorf("rag: parse search response: %w", err)
	}

	return result.Results, nil
}

// Document — метаданные документа в RAG-индексе.
type Document struct {
	ID             string  `json:"id"`
	Title          string  `json:"title"`
	SourcePath     string  `json:"source_path"`
	MimeType       string  `json:"mime_type"`
	DisciplineID   *string `json:"discipline_id,omitempty"`
	DisciplineName *string `json:"discipline_name,omitempty"`
	CreatedAt      string  `json:"created_at"`
}

// ListDocumentsRequest — запрос списка документов.
type ListDocumentsRequest struct {
	DisciplineID string `json:"discipline_id,omitempty"`
	Limit        int    `json:"limit,omitempty"`
}

// ListDocumentsResponse — ответ со списком документов.
type ListDocumentsResponse struct {
	Documents []Document `json:"documents"`
	Count     int        `json:"count"`
}

// ListDocuments возвращает список документов в RAG-индексе с фильтрацией по дисциплине.
func (c *Client) ListDocuments(disciplineID string, limit int) ([]Document, error) {
	reqBody := ListDocumentsRequest{
		DisciplineID: disciplineID,
		Limit:        limit,
	}

	data, err := json.Marshal(reqBody)
	if err != nil {
		return nil, fmt.Errorf("rag: marshal list documents request: %w", err)
	}

	resp, err := c.http.Post(c.baseURL+"/documents/list", "application/json", bytes.NewReader(data))
	if err != nil {
		return nil, fmt.Errorf("rag: list documents request: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("rag: read list documents response: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("rag: list documents returned status %d: %s", resp.StatusCode, string(body))
	}

	var result ListDocumentsResponse
	if err := json.Unmarshal(body, &result); err != nil {
		return nil, fmt.Errorf("rag: parse list documents response: %w", err)
	}

	return result.Documents, nil
}

// ContextResponse — ответ сборки контекста.
type ContextResponse struct {
	Context string         `json:"context"`
	Sources []SearchResult `json:"sources"`
}

// GetRagContext формирует готовый контекст для LLM.
func (c *Client) GetRagContext(query string, disciplineID string, limit int) (*ContextResponse, error) {
	if limit <= 0 {
		limit = 5
	}
	if limit > 20 {
		limit = 20
	}

	reqBody := SearchRequest{
		Query:        query,
		DisciplineID: disciplineID,
		Limit:        limit,
	}

	data, err := json.Marshal(reqBody)
	if err != nil {
		return nil, fmt.Errorf("rag: marshal context request: %w", err)
	}

	resp, err := c.http.Post(c.baseURL+"/context", "application/json", bytes.NewReader(data))
	if err != nil {
		return nil, fmt.Errorf("rag: context request: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("rag: read context response: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("rag: context returned status %d: %s", resp.StatusCode, string(body))
	}

	var result ContextResponse
	if err := json.Unmarshal(body, &result); err != nil {
		return nil, fmt.Errorf("rag: parse context response: %w", err)
	}

	return &result, nil
}

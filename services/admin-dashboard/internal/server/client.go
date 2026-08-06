// Package server provides HTTP clients for upstream services.
//
// HTTP routes called:
//   DataServiceClient.Do(GET  /admin/{path}) -> data-service:GET  /admin/{path}
//   DataServiceClient.Do(POST /admin/{path}) -> data-service:POST /admin/{path}
//   RagClient.Do(GET  /{path})              -> rag:GET  /{path}
//   RagClient.Do(POST /{path})              -> rag:POST /{path}
package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
)

// DataServiceClient — HTTP-клиент к data-service admin API.
type DataServiceClient struct {
	baseURL    string
	adminToken string
	httpClient *http.Client
}

// NewDataServiceClient создаёт клиента.
func NewDataServiceClient(baseURL, adminToken string) *DataServiceClient {
	return &DataServiceClient{
		baseURL:    baseURL,
		adminToken: adminToken,
		httpClient: http.DefaultClient,
	}
}

// Do отправляет запрос к data-service и возвращает тело ответа.
func (c *DataServiceClient) Do(method, path string, body any) ([]byte, int, error) {
	url := c.baseURL + path

	var bodyReader io.Reader
	if body != nil {
		data, err := json.Marshal(body)
		if err != nil {
			return nil, 0, fmt.Errorf("marshal body: %w", err)
		}
		bodyReader = bytes.NewReader(data)
	}

	req, err := http.NewRequest(method, url, bodyReader)
	if err != nil {
		return nil, 0, fmt.Errorf("create request: %w", err)
	}

	req.Header.Set("Authorization", "Bearer "+c.adminToken)
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, 0, fmt.Errorf("do request: %w", err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, 0, fmt.Errorf("read response: %w", err)
	}

	return respBody, resp.StatusCode, nil
}

// Get отправляет GET-запрос.
func (c *DataServiceClient) Get(path string) ([]byte, int, error) {
	return c.Do(http.MethodGet, path, nil)
}

// Post отправляет POST-запрос с телом.
func (c *DataServiceClient) Post(path string, body any) ([]byte, int, error) {
	return c.Do(http.MethodPost, path, body)
}

// Delete отправляет DELETE-запрос.
func (c *DataServiceClient) Delete(path string) ([]byte, int, error) {
	return c.Do(http.MethodDelete, path, nil)
}

// ── RAG Client ──

// RagClient — HTTP-клиент к RAG-сервису.
type RagClient struct {
	baseURL    string
	adminToken string
	httpClient *http.Client
}

// NewRagClient создаёт клиента RAG.
func NewRagClient(baseURL, adminToken string) *RagClient {
	if baseURL == "" {
		baseURL = "http://localhost:8082"
	}
	return &RagClient{
		baseURL:    baseURL,
		adminToken: adminToken,
		httpClient: http.DefaultClient,
	}
}

// Do отправляет запрос к RAG и возвращает тело ответа.
func (c *RagClient) Do(method, path string, body any) ([]byte, int, error) {
	url := c.baseURL + path

	var bodyReader io.Reader
	if body != nil {
		data, err := json.Marshal(body)
		if err != nil {
			return nil, 0, fmt.Errorf("marshal body: %w", err)
		}
		bodyReader = bytes.NewReader(data)
	}

	req, err := http.NewRequest(method, url, bodyReader)
	if err != nil {
		return nil, 0, fmt.Errorf("create request: %w", err)
	}

	req.Header.Set("Content-Type", "application/json")
	if c.adminToken != "" {
		req.Header.Set("X-Admin-Token", c.adminToken)
	}

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, 0, fmt.Errorf("do request: %w", err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, 0, fmt.Errorf("read response: %w", err)
	}

	return respBody, resp.StatusCode, nil
}

// Upload отправляет multipart/form-data файл в RAG и возвращает тело ответа.
func (c *RagClient) Upload(path, filename, fieldName string, fileContent []byte, formFields map[string]string) ([]byte, int, error) {
	url := c.baseURL + path

	var buf bytes.Buffer
	mw := multipart.NewWriter(&buf)

	// Поле файла
	part, err := mw.CreateFormFile(fieldName, filename)
	if err != nil {
		return nil, 0, fmt.Errorf("create form file: %w", err)
	}
	if _, err := part.Write(fileContent); err != nil {
		return nil, 0, fmt.Errorf("write file content: %w", err)
	}

	// Дополнительные текстовые поля
	for k, v := range formFields {
		if v != "" {
			if err := mw.WriteField(k, v); err != nil {
				return nil, 0, fmt.Errorf("write field %s: %w", k, err)
			}
		}
	}

	if err := mw.Close(); err != nil {
		return nil, 0, fmt.Errorf("close multipart writer: %w", err)
	}

	req, err := http.NewRequest(http.MethodPost, url, &buf)
	if err != nil {
		return nil, 0, fmt.Errorf("create request: %w", err)
	}

	req.Header.Set("Content-Type", mw.FormDataContentType())

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, 0, fmt.Errorf("do upload: %w", err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, 0, fmt.Errorf("read response: %w", err)
	}

	return respBody, resp.StatusCode, nil
}

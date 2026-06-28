// Package httpclient provides HTTP client for calling data-service endpoints.
package httpclient

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"
)

// defaultBaseURL — адрес data-service по умолчанию.
const defaultBaseURL = "http://127.0.0.1:8084"

// defaultTimeout — таймаут HTTP-запроса к data-service по умолчанию.
const defaultTimeout = 30 * time.Second

// Client — HTTP-клиент для data-service.
type Client struct {
	baseURL string
	http    *http.Client
}

// New создаёт новый клиент.
//
// DATA_SERVICE_URL — базовый URL data-service (по умолчанию http://127.0.0.1:8084).
// DATA_SERVICE_TIMEOUT — таймаут HTTP-запроса в секундах (по умолчанию 30).
func New() *Client {
	base := os.Getenv("DATA_SERVICE_URL")
	if base == "" {
		base = defaultBaseURL
	}
	base = strings.TrimRight(base, "/")

	timeout := defaultTimeout
	if t := os.Getenv("DATA_SERVICE_TIMEOUT"); t != "" {
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

// Call выполняет HTTP GET к data-service.
//
// endpoint — путь из конфига (например "/students/{id}").
// params — карта имя→значение. Path-параметры (из {param}) подставляются
// в URL, остальные — в query-строку.
//
// Возвращает распарсенный JSON (any) — []any для массива, map[string]any для объекта.
func (c *Client) Call(endpoint string, params map[string]any) (any, error) {
	// 1. Подставляем path-параметры
	resolved := resolvePathParams(endpoint, params)

	// 2. Собираем query-параметры (те, что не были path)
	query := url.Values{}
	for k, v := range params {
		// Если это path-параметр, пропускаем (уже подставлен)
		if isPathParam(endpoint, k) {
			continue
		}
		query.Set(k, fmt.Sprintf("%v", v))
	}

	// 3. Строим URL
	u, err := url.Parse(c.baseURL + resolved)
	if err != nil {
		return nil, fmt.Errorf("mcp: parse url: %w", err)
	}
	if len(query) > 0 {
		u.RawQuery = query.Encode()
	}

	// 4. GET
	req, err := http.NewRequest("GET", u.String(), nil)
	if err != nil {
		return nil, fmt.Errorf("mcp: create request: %w", err)
	}
	req.Header.Set("Accept", "application/json")

	resp, err := c.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("mcp: http get %s: %w", u.String(), err)
	}
	defer resp.Body.Close()

	// 5. Читаем тело
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("mcp: read response: %w", err)
	}

	// 6. 404 = nil
	if resp.StatusCode == http.StatusNotFound {
		return nil, nil
	}

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("mcp: data-service returned status %d: %s", resp.StatusCode, string(body))
	}

	// 7. Парсим JSON
	var result any
	if err := json.Unmarshal(body, &result); err != nil {
		return nil, fmt.Errorf("mcp: parse json response: %w", err)
	}

	return result, nil
}

// resolvePathParams заменяет {param} в endpoint на значения из params.
// Возвращает endpoint с подставленными значениями.
func resolvePathParams(endpoint string, params map[string]any) string {
	result := endpoint
	for k, v := range params {
		placeholder := "{" + k + "}"
		if strings.Contains(result, placeholder) {
			result = strings.ReplaceAll(result, placeholder, url.PathEscape(fmt.Sprintf("%v", v)))
		}
	}
	return result
}

// isPathParam проверяет, является ли имя параметра path-параметром.
func isPathParam(endpoint, name string) bool {
	return strings.Contains(endpoint, "{"+name+"}")
}

package httpclient

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/agent-tutor/agent-tutor-go/config"
)

type contextKey string

const TenantIDKey contextKey = "x-tenant-id"

type Client struct {
	baseURL string
	http    *http.Client
}

func New() *Client {
	base := os.Getenv("DATA_SERVICE_URL")
	if base == "" {
		base = "http://127.0.0.1:8084"
	}
	base = strings.TrimRight(base, "/")

	timeout := 30 * time.Second
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

func (c *Client) BaseURL() string {
	return c.baseURL
}

func (c *Client) FetchConfig() (*config.Config, error) {
	tenantID := os.Getenv("BOOTSTRAP_TENANT_ID")
	return c.FetchConfigWithTenant(tenantID)
}

func (c *Client) FetchConfigWithTenant(tenantID string) (*config.Config, error) {
	u := c.baseURL + "/mcp/manifest"

	req, err := http.NewRequest("GET", u, nil)
	if err != nil {
		return nil, fmt.Errorf("mcp: create config request: %w", err)
	}
	req.Header.Set("Accept", "application/json")
	if tenantID != "" {
		req.Header.Set("X-Tenant-ID", tenantID)
	}

	resp, err := c.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("mcp: fetch config from %s: %w", u, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("mcp: config endpoint returned status %d: %s", resp.StatusCode, string(body))
	}

	var cfg config.Config
	if err := json.NewDecoder(resp.Body).Decode(&cfg); err != nil {
		return nil, fmt.Errorf("mcp: decode config: %w", err)
	}

	return &cfg, nil
}

func (c *Client) Call(ctx context.Context, endpoint string, params map[string]any) (any, error) {
	u := c.baseURL + endpoint

	// Separate path params from query params.
	// Path params: substitute {param} placeholders in URL.
	// Query params: append as ?key=value after all path substitutions.
	queryParts := make([]string, 0)

	for k, v := range params {
		placeholder := "{" + k + "}"
		if strings.Contains(u, placeholder) {
			u = strings.ReplaceAll(u, placeholder, fmt.Sprintf("%v", v))
		} else {
			queryParts = append(queryParts, fmt.Sprintf("%s=%v", k, v))
		}
	}

	if len(queryParts) > 0 {
		u += "?" + strings.Join(queryParts, "&")
	}

	req, err := http.NewRequestWithContext(ctx, "GET", u, nil)
	if err != nil {
		return nil, fmt.Errorf("http: create request: %w", err)
	}

	// Pass tenant ID from context
	if tenantID, ok := ctx.Value(TenantIDKey).(string); ok && tenantID != "" {
		req.Header.Set("X-Tenant-ID", tenantID)
	}

	req.Header.Set("Accept", "application/json")

	resp, err := c.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("http: execute request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("http: endpoint %s returned status %d: %s", endpoint, resp.StatusCode, string(body))
	}

	var result any
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, fmt.Errorf("http: decode response: %w", err)
	}

	return result, nil
}

// Package httpclient provides HTTP client for calling data-service.
//
// HTTP routes called:
//   FetchConfigWithTenant() -> data-service:GET /mcp/manifest (load tenant MCP config)
//   Call()                 -> data-service:GET /{endpoint}       (execute data query)
//   Call()                 -> data-service:GET /{endpoint}/{id}  (get entity by ID)
package httpclient

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/trash2bin/helperium/helperium-go/config"
)

type contextKey string

const TenantIDKey contextKey = "x-tenant-id"

type Client struct {
	baseURL string
	http    *http.Client
}

// New creates a new HTTP client for data-service.
// DATA_SERVICE_URL env var (default: http://127.0.0.1:8084).
func New() *Client {
	base := os.Getenv("DATA_SERVICE_URL")
	if base == "" {
		base = "http://127.0.0.1:8084"
	}
	base = strings.TrimRight(base, "/")

	// Warn if DATA_SERVICE_URL points to a private IP range (SSRF risk)
	if err := ValidateURL(base); err != nil {
		slog.Warn("DATA_SERVICE_URL resolves to private IP — this is expected in dev but should be avoided in production",
			"url", base, "error", err)
	}

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
// ── SSRF Protection ──

var (
	// privateCIDRs contains IP ranges that should never be accessed by the client
	// to prevent Server-Side Request Forgery (SSRF) attacks.
	privateCIDRs []*net.IPNet

	// errPrivateIP is returned when a target URL resolves to a private IP.
	errPrivateIP = errors.New("target URL resolves to a private IP range (possible SSRF)")
)

func init() {
	ranges := []string{
		"127.0.0.0/8",    // loopback
		"10.0.0.0/8",     // private (RFC 1918)
		"172.16.0.0/12",  // private (RFC 1918)
		"192.168.0.0/16", // private (RFC 1918)
		"169.254.0.0/16", // link-local (RFC 3927) — includes 169.254.169.254 cloud metadata
		"100.64.0.0/10",  // CGNAT (RFC 6598)
		"0.0.0.0/8",      // current network (RFC 1122)
		"::1/128",        // IPv6 loopback
		"fc00::/7",       // IPv6 unique local (RFC 4193)
	}

	for _, r := range ranges {
		_, cidr, err := net.ParseCIDR(r)
		if err == nil {
			privateCIDRs = append(privateCIDRs, cidr)
		}
	}
}

// isPrivateIP checks if an IP address falls into a known private/reserved range.
func isPrivateIP(ip net.IP) bool {
	if ip == nil {
		return false
	}
	for _, cidr := range privateCIDRs {
		if cidr.Contains(ip) {
			return true
		}
	}
	return false
}

// ValidateURL checks that a URL does not point to a private or restricted IP range.
// It resolves hostnames to IPs and rejects any URL that resolves to a private range.
// This is a basic SSRF prevention mechanism.
func ValidateURL(targetURL string) error {
	if targetURL == "" {
		return errors.New("URL is empty")
	}

	parsed, err := url.Parse(targetURL)
	if err != nil {
		return fmt.Errorf("invalid URL: %w", err)
	}

	if parsed.Scheme == "" {
		return errors.New("URL missing scheme")
	}

	if parsed.Host == "" {
		return errors.New("URL missing host")
	}

	host := parsed.Hostname()

	// Strip port and parse as IP directly
	ip := net.ParseIP(host)
	if ip != nil {
		if isPrivateIP(ip) {
			return errPrivateIP
		}
		return nil
	}

	// Hostname: resolve to IPs and check each one
	ips, err := net.LookupHost(host)
	if err != nil {
		// If DNS resolution fails, we allow the request through (fail open).
		// Fail-closed would break legitimate requests when DNS is temporarily
		// unavailable. The SSRF risk window is limited: an attacker would need
		// to control DNS at the exact moment of resolution.
		return nil
	}

	for _, ipStr := range ips {
		ip = net.ParseIP(ipStr)
		if ip != nil && isPrivateIP(ip) {
			return fmt.Errorf("hostname %q resolves to private IP %s: %w", host, ipStr, errPrivateIP)
		}
	}

	return nil
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
	// All values are URL-escaped to prevent injection via URL (SQL injection, path traversal).
	query := url.Values{}

	for k, v := range params {
		placeholder := "{" + k + "}"
		if strings.Contains(u, placeholder) {
			u = strings.ReplaceAll(u, placeholder, url.PathEscape(fmt.Sprintf("%v", v)))
		} else {
			query.Set(k, fmt.Sprintf("%v", v))
		}
	}

	if len(query) > 0 {
		u += "?" + query.Encode()
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

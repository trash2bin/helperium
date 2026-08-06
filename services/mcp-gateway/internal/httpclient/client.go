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
	"sync"
	"time"

	"github.com/trash2bin/helperium/helperium-go/config"
)

type contextKey string

const TenantIDKey contextKey = "x-tenant-id"

// ── Manifest cache ──
//
// FetchConfigWithTenant is called on every stateless /mcp/message POST.
// The underlying GET /mcp/manifest returns ~28KB JSON per tenant.
// We cache it so repeated calls within the TTL window don't hit data-service.

type cachedConfig struct {
	cfg  *config.Config
	exp  time.Time
}

type Client struct {
	baseURL string
	http    *http.Client

	// manifestCache: per-tenant TTL cache for /mcp/manifest responses.
	//   key: tenantID string (empty for bootstrapped/default)
	//   value: cached config with expiration
	manifestCache     map[string]cachedConfig
	manifestCacheMu   sync.RWMutex
	manifestCacheTTL  time.Duration
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

	// Custom transport with aggressive connection pooling.
	// http.DefaultTransport defaults to MaxIdleConnsPerHost=2, which creates
	// connection churn under concurrent load. We bump pooling aggressively
	// since all requests go to local data-service.
	tr := &http.Transport{
		MaxIdleConns:        100,
		MaxIdleConnsPerHost: 100,
		MaxConnsPerHost:     0, // unlimited
		IdleConnTimeout:     90 * time.Second,
		DisableCompression:  false,
		ForceAttemptHTTP2:   true,
	}

	return &Client{
		baseURL:          base,
		http: &http.Client{
			Timeout:   timeout,
			Transport: tr,
		},
		manifestCache:    make(map[string]cachedConfig),
		manifestCacheTTL: 30 * time.Second,
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
	// Check TTL cache first
	c.manifestCacheMu.RLock()
	if cached, ok := c.manifestCache[tenantID]; ok && time.Now().Before(cached.exp) {
		c.manifestCacheMu.RUnlock()
		return cached.cfg, nil
	}
	c.manifestCacheMu.RUnlock()

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

	// Cache it
	c.manifestCacheMu.Lock()
	c.manifestCache[tenantID] = cachedConfig{
		cfg: &cfg,
		exp: time.Now().Add(c.manifestCacheTTL),
	}
	c.manifestCacheMu.Unlock()

	return &cfg, nil
}

// InvalidateManifestCache clears the cached manifest for the given tenant (or all).
// Called after a config rewrite to force a fresh fetch on the next tool call.
func (c *Client) InvalidateManifestCache(tenantIDs ...string) {
	c.manifestCacheMu.Lock()
	defer c.manifestCacheMu.Unlock()
	if len(tenantIDs) == 0 {
		c.manifestCache = make(map[string]cachedConfig)
		return
	}
	for _, id := range tenantIDs {
		delete(c.manifestCache, id)
	}
}

// FetchSchemaWithTenant fetches the LLM-friendly schema description from data-service.
// Not cached — schema is small and may change per-request context.
func (c *Client) FetchSchemaWithTenant(tenantID string) ([]byte, error) {
	u := c.baseURL + "/mcp/schema"

	req, err := http.NewRequest("GET", u, nil)
	if err != nil {
		return nil, fmt.Errorf("mcp: create schema request: %w", err)
	}
	req.Header.Set("Accept", "application/json")
	if tenantID != "" {
		req.Header.Set("X-Tenant-ID", tenantID)
	}

	resp, err := c.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("mcp: fetch schema from %s: %w", u, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("mcp: schema endpoint returned status %d: %s", resp.StatusCode, string(body))
	}

	return io.ReadAll(resp.Body)
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

package server

import (
	"encoding/json"
	"html/template"
	"net/http"
	"net/url"

	"github.com/agent-tutor/agent-tutor-go/pkg/swaggerui"
	"github.com/agent-tutor/data-service/internal/openapigen"
)

const (
	tenantBarHead template.CSS = `.tenant-bar{position:fixed;top:12px;right:16px;z-index:9999;background:#fff;border:1px solid #e5e7eb;padding:6px 10px;border-radius:8px;display:flex;gap:8px;align-items:center;box-shadow:0 2px 6px rgba(0,0,0,0.06);font:13px system-ui} .tenant-bar input{border:1px solid #d1d5db;border-radius:6px;padding:4px 8px;width:160px} .tenant-bar button{border:1px solid #2563eb;background:#2563eb;color:#fff;border-radius:6px;padding:4px 10px;cursor:pointer}`
	tenantBarBody template.HTML = `<div class="tenant-bar"> <label for="tenant">Tenant:</label> <input id="tenant" list="tenantList" placeholder="default" /> <datalist id="tenantList"></datalist> <button id="applyTenant">Apply</button> </div>`
)

// swaggerInitWithTenant wraps DefaultInit with a requestInterceptor that
// injects X-Tenant-ID from the tenant input field and a script that
// populates the datalist from the current tenant set.
const swaggerInitWithTenant template.JS = ` // Populate tenant dropdown from URL param if present
const qs = new URLSearchParams(window.location.search);
const tenantInput = document.getElementById('tenant');
if (tenantInput && qs.has('tenant')) tenantInput.value = qs.get('tenant');
document.getElementById('applyTenant')?.addEventListener('click', () => {
	const val = tenantInput.value.trim();
	const url = new URL(location.origin + '/docs' + (val ? '?tenant=' + encodeURIComponent(val) : ''));
	location.href = url.toString();
});

SwaggerUIBundle({
	url: "/openapi.json" + window.location.search,
	dom_id: "#swagger-ui",
	presets: [SwaggerUIBundle.presets.apis, SwaggerUIStandalonePreset],
	layout: "StandaloneLayout",
	defaultModelsExpandDepth: -1,
	requestInterceptor: function(req) {
		const v = document.getElementById('tenant')?.value?.trim();
		if (v) req.headers['X-Tenant-ID'] = v;
		return req;
	}
});
`

// SwaggerHandler serves the Swagger UI page via the shared swaggerui package.
func SwaggerHandler(w http.ResponseWriter, r *http.Request) {
	swaggerui.Handler("Data Service", tenantBarHead, tenantBarBody, swaggerInitWithTenant)(w, r)
}

// swaggerHandler is the package-local alias used by the internal router builders.
var swaggerHandler = SwaggerHandler

// NewOpenAPIHandler creates an HTTP handler for /openapi.json.
// Uses TenantStore to resolve the correct config per request.
// If no tenant is provided, returns a system-only spec (health, stats, admin).
func NewOpenAPIHandler(ts *TenantStore, hasAdmin bool) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		inst := ts.resolveTenant(r)
		if inst == nil {
			spec := openapigen.GenerateSystemSpec("http://127.0.0.1:8084", "Data Service", "0.2.0", hasAdmin)
			w.Header().Set("Content-Type", "application/json")
			w.Header().Set("Access-Control-Allow-Origin", "*")
			json.NewEncoder(w).Encode(spec)
			return
		}
		spec := openapigen.Generate(inst.Config, "http://127.0.0.1:8084", "Data Service", "0.2.0", hasAdmin)
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Access-Control-Allow-Origin", "*")
		json.NewEncoder(w).Encode(spec)
	}
}

// SwaggerHandlerWithTenant ensures the Swagger UI and OpenAPI spec carry a tenant
// identifier so the page does not fall through to tenant_not_found.
func SwaggerHandlerWithTenant(ts *TenantStore, defaultTenant string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		tenantID := defaultTenant
		if tenantID == "" {
			tenantID = r.URL.Query().Get("tenant")
		}
		if tenantID == "" {
			tenantID = r.Header.Get("X-Tenant-ID")
		}
		if tenantID == "" {
			tenantID = "default"
		}
		if r.URL.RawQuery != "" {
			r.URL.RawQuery = r.URL.RawQuery + "&tenant=" + url.QueryEscape(tenantID)
		} else {
			r.URL.RawQuery = "tenant=" + url.QueryEscape(tenantID)
		}
		SwaggerHandler(w, r)
	}
}

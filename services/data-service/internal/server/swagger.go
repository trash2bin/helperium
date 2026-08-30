package server

import (
	"encoding/json"
	"html/template"
	"net/http"

	"github.com/trash2bin/helperium/helperium-go/openapigen"
	"github.com/trash2bin/helperium/helperium-go/pkg/cors"
	"github.com/trash2bin/helperium/helperium-go/pkg/swaggerui"
)

const (
	tenantBarHead template.CSS  = `.tenant-bar{position:fixed;top:12px;right:16px;z-index:9999;background:#fff;border:1px solid #e5e7eb;padding:6px 10px;border-radius:8px;display:flex;gap:8px;align-items:center;box-shadow:0 2px 6px rgba(0,0,0,0.06);font:13px system-ui} .tenant-bar input{border:1px solid #d1d5db;border-radius:6px;padding:4px 8px;width:160px} .tenant-bar button{border:1px solid #2563eb;background:#2563eb;color:#fff;border-radius:6px;padding:4px 10px;cursor:pointer}`
	tenantBarBody template.HTML = `<div class="tenant-bar"> <label for="tenant">Tenant:</label> <input id="tenant" list="tenantList" placeholder="default" /> <datalist id="tenantList"></datalist> <button id="applyTenant">Apply</button> </div>`
)

// swaggerInitWithTenant injects X-Tenant-ID via requestInterceptor and
// persists the selected tenant in localStorage (no ?tenant= in the URL).
const swaggerInitWithTenant template.JS = `
const tenantInput = document.getElementById('tenant');
const savedTenant = localStorage.getItem('helperium_tenant') || '';
if (tenantInput && savedTenant) tenantInput.value = savedTenant;

document.getElementById('applyTenant')?.addEventListener('click', () => {
	const val = tenantInput.value.trim();
	localStorage.setItem('helperium_tenant', val);
	window.location.reload();
});

SwaggerUIBundle({
	url: "/openapi.json",
	dom_id: "#swagger-ui",
	presets: [SwaggerUIBundle.presets.apis, SwaggerUIStandalonePreset],
	layout: "StandaloneLayout",
	defaultModelsExpandDepth: -1,
	// requestInterceptor injects X-Tenant-ID into every request, including
	// the initial spec fetch for /openapi.json. This is the ONLY way tenant is
	// communicated — no ?tenant= query parameter is used.
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

// NewOpenAPIHandler creates an HTTP handler for /openapi.json.
// Uses TenantStore to resolve the correct config per request.
// If no tenant is provided, returns a system-only spec (health, stats, admin).
func NewOpenAPIHandler(ts *TenantStore, hasAdmin bool) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		// Внутри tenant-роутера inst лежит в контексте (зарезолвлен в ServeHTTP
		// под RLock) — читаем отсюда, без повторного resolveTenant (иначе
		// вложенный RLock → deadlock). Fallback: ts.resolveTenant(r) для прямых
		// вызовов роутера (тесты) и system-level (/openapi.json из rootRouter,
		// где inst нет в контексте) → system-only spec.
		inst, _ := r.Context().Value(tenantInstanceKey).(*TenantInstance)
		if inst == nil {
			inst = ts.resolveTenant(r)
		}
		if inst == nil {
			spec := openapigen.GenerateSystemSpec("http://127.0.0.1:8084", "Data Service", "0.2.0", hasAdmin)
			w.Header().Set("Content-Type", "application/json")
			w.Header().Set("Access-Control-Allow-Origin", cors.AllowOrigin())
			json.NewEncoder(w).Encode(spec)
			return
		}
		spec := openapigen.Generate(inst.Config, "http://127.0.0.1:8084", "Data Service", "0.2.0", hasAdmin)
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Access-Control-Allow-Origin", cors.AllowOrigin())
		json.NewEncoder(w).Encode(spec)
	}
}

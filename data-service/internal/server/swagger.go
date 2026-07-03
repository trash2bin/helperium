package server

import (
	"encoding/json"
	_ "embed"
	"html/template"
	"net/http"

	"github.com/agent-tutor/data-service/internal/openapigen"
)

//go:embed swagger-ui.html
var swaggerUI string

// swaggerHandler отдаёт Swagger UI страницу.
func swaggerHandler(w http.ResponseWriter, r *http.Request) {
	tmpl, err := template.New("swagger").Parse(swaggerUI)
	if err != nil {
		http.Error(w, "template error", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	tmpl.Execute(w, nil)
}

// NewOpenAPIHandler creates an HTTP handler for /openapi.json.
// It now uses the TenantStore to resolve the correct config based on the request.
// NewOpenAPIHandler creates an HTTP handler for /openapi.json.
// It now uses the TenantStore to resolve the correct config based on the request.
// If no tenant is provided, returns a system-only spec (health, stats, admin).
func NewOpenAPIHandler(ts *TenantStore, hasAdmin bool) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		inst := ts.resolveTenant(r)
		if inst == nil {
			// No tenant specified — return system-only OpenAPI spec
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

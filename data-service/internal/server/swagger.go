package server

import (
	"encoding/json"
	_ "embed"
	"html/template"
	"net/http"

	"github.com/agent-tutor/data-service/internal/config"
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

// NewOpenAPIHandler создаёт HTTP-хендлер для /openapi.json,
// который генерирует спецификацию из runtime-конфига на КАЖДЫЙ запрос.
//
// Это значит что конфиг изменился → openapi.json сам подстроится.
// Без рестарта.
func NewOpenAPIHandler(cfg *config.Config, hasAdmin bool) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		spec := openapigen.Generate(cfg, "http://127.0.0.1:8084", "Data Service", "0.2.0", hasAdmin)
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Access-Control-Allow-Origin", "*")
		json.NewEncoder(w).Encode(spec)
	}
}

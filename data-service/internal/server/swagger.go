package server

import (
	_ "embed"
	"html/template"
	"net/http"
	"path/filepath"
	"strings"
)

//go:embed swagger-ui.html
var swaggerUI string

//go:embed openapi.json
var openapiJSON []byte

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

// openapiHandler отдаёт OpenAPI JSON-спецификацию.
func openapiHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Write(openapiJSON)
}

// trimExt удаляет расширение файла.
func trimExt(path string) string {
	return strings.TrimSuffix(path, filepath.Ext(path))
}

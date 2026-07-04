// Package swaggerui provides a shared, parameterized Swagger UI page
// embedded via go:embed for both data-service and mcp-gateway.
package swaggerui

import (
	_ "embed"
	"html/template"
	"net/http"
)

//go:embed swagger-ui.html
var swaggerUI string

var tmpl = template.Must(template.New("swagger").Parse(swaggerUI))

// PageData is injected into the swagger-ui.html template.
type PageData struct {
	Title       string
	ExtraHead   template.CSS
	ExtraBody   template.HTML
	SwaggerInit template.JS // SwaggerUIBundle({...}) init script
}

// Handler returns an http.HandlerFunc that serves the Swagger UI page
// with the given parameters.
//
// Use DefaultInit for a standard SwaggerUIBundle without interceptors.
func Handler(title string, extraHead template.CSS, extraBody template.HTML, swaggerInit template.JS) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		_ = tmpl.Execute(w, PageData{
			Title:       title,
			ExtraHead:   extraHead,
			ExtraBody:   extraBody,
			SwaggerInit: swaggerInit,
		})
	}
}

// DefaultInit is the standard SwaggerUIBundle init without any requestInterceptor.
const DefaultInit template.JS = `
  SwaggerUIBundle({
    url: "/openapi.json" + window.location.search,
    dom_id: "#swagger-ui",
    presets: [SwaggerUIBundle.presets.apis, SwaggerUIStandalonePreset],
    layout: "StandaloneLayout",
    defaultModelsExpandDepth: -1
  });`

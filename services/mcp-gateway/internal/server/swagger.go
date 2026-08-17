package server

import (
	"encoding/json"
	"net/http"

	"github.com/trash2bin/helperium/helperium-go/pkg/cors"
	"github.com/trash2bin/helperium/helperium-go/pkg/swaggerui"
)

// SwaggerHandler serves the Swagger UI page via the shared swaggerui package.
func SwaggerHandler() http.HandlerFunc {
	return swaggerui.Handler("MCP Gateway", "", "", swaggerui.DefaultInit)
}

// OpenAPIHandler serves a static OpenAPI 3.1.0 specification for the MCP Gateway.
func OpenAPIHandler() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		spec := map[string]any{
			"openapi": "3.1.0",
			"info": map[string]any{
				"title":       "MCP Gateway",
				"version":     "1.1.0",
				"description": "MCP Gateway - tenant-scoped MCP tools over standard Streamable HTTP",
			},
			"servers": []map[string]any{
				{"url": "http://127.0.0.1:8083"},
			},
			"paths": map[string]any{
				"/health": map[string]any{
					"get": map[string]any{
						"summary": "Health check",
						"responses": map[string]any{
							"200": map[string]any{"description": "OK"},
						},
					},
				},
				"/mcp": map[string]any{
					"get": map[string]any{
						"summary": "MCP Streamable HTTP session stream",
						"responses": map[string]any{
							"200": map[string]any{"description": "MCP Streamable HTTP response"},
						},
					},
					"post": map[string]any{
						"summary": "MCP Streamable HTTP request",
						"responses": map[string]any{
							"200": map[string]any{"description": "MCP JSON-RPC response"},
						},
					},
					"delete": map[string]any{
						"summary": "Terminate an MCP Streamable HTTP session",
						"responses": map[string]any{
							"200": map[string]any{"description": "MCP session terminated"},
						},
					},
				},

				"/mcp/manifest": map[string]any{
					"get": map[string]any{
						"summary": "Get MCP tools manifest",
						"parameters": []map[string]any{
							{
								"name":     "X-Tenant-ID",
								"in":       "header",
								"required": false,
								"schema":   map[string]any{"type": "string"},
							},
						},

						"responses": map[string]any{
							"200": map[string]any{"description": "JSON Manifest"},
						},
					},
				},
			},
		}
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Access-Control-Allow-Origin", cors.AllowOrigin())
		json.NewEncoder(w).Encode(spec)
	}
}

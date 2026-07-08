package server

import (
	"encoding/json"
	"net/http"

	"github.com/agent-tutor/agent-tutor-go/pkg/cors"
	"github.com/agent-tutor/agent-tutor-go/pkg/swaggerui"
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
				"version":     "0.1.0",
				"description": "MCP Gateway - Proxy for MCP tools and SSE streaming",
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
						"summary": "SSE endpoint for MCP",
						"responses": map[string]any{
							"200": map[string]any{"description": "SSE Stream"},
						},
					},
				},
				"/sse": map[string]any{
					"get": map[string]any{
						"summary": "SSE endpoint (alias)",
						"responses": map[string]any{
							"200": map[string]any{"description": "SSE Stream"},
						},
					},
				},
				"/": map[string]any{
					"get": map[string]any{
						"summary": "Root endpoint (SSE)",
						"responses": map[string]any{
							"200": map[string]any{"description": "SSE Stream"},
						},
					},
					"post": map[string]any{
						"summary": "MCP JSON-RPC request",
						"responses": map[string]any{
							"200": map[string]any{"description": "JSON-RPC Response"},
						},
					},
				},
				"/mcp/manifest": map[string]any{
					"get": map[string]any{
						"summary": "Get MCP tools manifest",
						"parameters": []map[string]any{
							{
								"name":     "tenant",
								"in":       "query",
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

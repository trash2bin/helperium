// Package cors provides CORS configuration helpers for helperium Go services.
//
// Both data-service and mcp-gateway use this package to read the CORS_ALLOW_ORIGINS
// environment variable, with a fallback to "*" for backward compatibility.
package cors

import "os"

// AllowOrigin returns the value for Access-Control-Allow-Origin, read from the
// CORS_ALLOW_ORIGINS environment variable. Returns "*" when the variable is empty
// or unset (default, backward-compatible behaviour).
func AllowOrigin() string {
	origin := os.Getenv("CORS_ALLOW_ORIGINS")
	if origin == "" {
		return "*"
	}
	return origin
}

package config

import (
	"regexp"
)

// tenantIDPattern is the repo-wide tenant-ID contract (AGENTS.md "MCP scope"):
// [A-Za-z0-9][A-Za-z0-9_-]{0,127}. Every surface that accepts a tenant ID from
// an external input (headers, admin request bodies, upload form fields) must
// enforce it before the value reaches filesystem paths, cache keys, config
// stores, or metric labels. The mcp-gateway, data-service and admin-dashboard
// all delegate to this single source so the contract cannot drift.
var tenantIDPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$`)

// ValidTenantID reports whether id matches the repo-wide tenant-ID contract.
// It is exported so services outside this package (data-service admin
// handlers, admin-dashboard upload handler, mcp-gateway scope resolution) can
// validate externally-controlled tenant IDs against the same pattern the
// gateway enforces for X-Tenant-ID.
func ValidTenantID(id string) bool {
	return tenantIDPattern.MatchString(id)
}

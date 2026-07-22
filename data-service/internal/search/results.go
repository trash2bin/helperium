// Package search — Strategies for building QueryPlan from HTTP/MCP requests.
//
// Each strategy knows how to parse request parameters for a specific
// search pattern (grep, filter, simple) and generate the corresponding
// QueryPlan for the query.Engine.
package search

import "github.com/trash2bin/helperium/data-service/internal/query"

// SearchResult re-exports query.SearchResult for caller convenience.
type SearchResult = query.SearchResult

// CompactRow re-exports query.CompactRow for caller convenience.
type CompactRow = query.CompactRow

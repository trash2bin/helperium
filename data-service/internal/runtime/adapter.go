package runtime

import "github.com/trash2bin/helperium/data-service/internal/query"

// AdapterToQuery bridges runtime.AdapterSubset to query.AdapterSubset.
// Both interfaces have overlapping method sets; this wrapper ensures
// runtime.AdapterSubset satisfies query.AdapterSubset without import cycles.
type AdapterToQuery struct {
	Inner AdapterSubset
}

func (a *AdapterToQuery) TranslatePlaceholder(index int) string { return a.Inner.TranslatePlaceholder(index) }
func (a *AdapterToQuery) QuoteIdentifier(name string) string    { return a.Inner.QuoteIdentifier(name) }

// QuoteString escapes LIKE special chars '%' and '_'.
func (a *AdapterToQuery) QuoteString(s string) string {
	escaped := ""
	for _, c := range s {
		if c == '%' || c == '_' {
			escaped += "\\"
		}
		escaped += string(c)
	}
	return escaped
}

// Ensure AdapterToQuery satisfies query.AdapterSubset interface.
var _ query.AdapterSubset = (*AdapterToQuery)(nil)

package runtime

import "github.com/trash2bin/helperium/data-service/internal/query"

// AdapterToQuery bridges runtime.AdapterSubset to query.AdapterSubset.
// Both interfaces have overlapping method sets; this wrapper ensures
// runtime.AdapterSubset satisfies query.AdapterSubset without import cycles.
type AdapterToQuery struct {
	Inner AdapterSubset
}

func (a *AdapterToQuery) TranslatePlaceholder(index int) string {
	return a.Inner.TranslatePlaceholder(index)
}
func (a *AdapterToQuery) QuoteIdentifier(name string) string { return a.Inner.QuoteIdentifier(name) }

// QuoteString escapes LIKE special chars '%', '_' and the escape char itself
// ('\'). Согласовано с ESCAPE '\\' клаузой, которую builder добавляет к
// каждому LIKE: без экранирования '\' пользовательский ввод вида "\\%"
// даст литеральный '\' + wildcard '%' и сломает точный поиск.
//
// ⚠️ Отличие от filter __like (search/filter.go): там RawValue=true и ввод
// НЕ экранируется — пользователь сам управляет wildcard'ами, а '\' в его
// значении становится escape-символом. Здесь (grep-токены) '\' экранируется
// автоматически. Два разных контракта '\' — grep: auto-escaped; filter: raw.
func (a *AdapterToQuery) QuoteString(s string) string {
	escaped := ""
	for _, c := range s {
		if c == '%' || c == '_' || c == '\\' {
			escaped += "\\"
		}
		escaped += string(c)
	}
	return escaped
}

// Ensure AdapterToQuery satisfies query.AdapterSubset interface.
var _ query.AdapterSubset = (*AdapterToQuery)(nil)

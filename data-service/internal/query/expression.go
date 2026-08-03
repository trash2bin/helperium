// Package query — Expression-based query engine for data-service.
//
// Replaces the 5-method runtime/query_builder.go with a single
// QueryPlan → SQL+args transformation via Engine.Build / BuildCount.
//
// Types are composable, testable, and database-agnostic.
package query

// QueryPlan — полное описание SELECT-запроса.
type QueryPlan struct {
	// Select — описание SELECT-части (колонки).
	Select SelectClause
	// From — имя таблицы (уже квотированное через QuoteIdentifier).
	From string
	// Where — список условий, соединяемых через AND.
	Where []Condition
	// Order — сортировка (опционально).
	Order []OrderClause
	// Limit — максимальное количество строк (0 = без лимита).
	Limit int
	// Offset — смещение (0 = без смещения).
	Offset int
	// RawWhere — сырое WHERE-выражение (без "WHERE").
	// Если задан, игнорирует Where[]. Используется для сложных
	// комбинаций OR/AND, которые нельзя выразить через []Condition.
	RawWhere string

	// RawWhereArgs — аргументы для RawWhere (в порядке placeholder'ов).
	RawWhereArgs []any

	// Format — формат ответа (влияет на SELECT-колонки).
	Format ResponseFormat
}

// SelectClause — описание SELECT-части запроса.
type SelectClause struct {
	// Columns — квотированные имена колонок.
	// Если пусто — используется "*".
	Columns []string
}

// ResponseFormat — формат ответа search endpoint'ов.
type ResponseFormat int

const (
	// FormatCompact — id + name preview (для списковых endpoint'ов).
	FormatCompact ResponseFormat = iota
	// FormatFull — все колонки сущности.
	FormatFull
	// FormatCount — только COUNT(*) (для /count endpoint'ов).
	FormatCount
)

// Condition — одно условие WHERE.
type Condition struct {
	// Field — квотированное имя колонки БД.
	Field string
	// Operator — тип сравнения.
	Operator Operator
	// Value — скалярное значение (для бинарных операторов).
	Value any
	// Values — список значений (для IN/Between).
	Values []any
	// Not — NOT-флаг (инвертирует условие).
	Not bool
	// RawValue — если true, значение передаётся в SQL без QuoteString
	// (для LIKE с уже подготовленными паттернами от пользователя).
	RawValue bool
}

// Operator — тип сравнения в условии WHERE.
type Operator int

const (
	OpEq      Operator = iota // =
	OpNeq                     // !=
	OpLt                      // <
	OpGt                      // >
	OpLte                     // <=
	OpGte                     // >=
	OpLike                    // LIKE
	OpILike                   // ILIKE (Postgres) / LIKE (SQLite)
	OpNotLike                 // NOT LIKE
	OpRegex                   // REGEXP (SQLite) / ~ (Postgres)
	OpIn                      // IN (...)
	OpBetween                 // BETWEEN x AND y
)

// OrderClause — элемент ORDER BY.
type OrderClause struct {
	// Field — квотированное имя колонки.
	Field string
	// Desc — true для DESC, false для ASC.
	Desc bool
}

// EmptyHint — подсказка LLM при пустом результате поиска.
// Возвращается только когда Total == 0, чтобы LLM понимала что делать дальше.
type EmptyHint struct {
	// SuggestedAction — что LLM может сделать чтобы найти данные
	SuggestedAction string `json:"suggested_action,omitempty"`

	// AvailableValues — для каждого string-поля список distinct значений (max 5)
	AvailableValues map[string][]string `json:"available_values,omitempty"`
}

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
	OpEq       Operator = iota // =
	OpNeq                      // !=
	OpLt                       // <
	OpGt                       // >
	OpLte                      // <=
	OpGte                      // >=
	OpLike                     // LIKE
	OpILike                    // ILIKE (Postgres) / LIKE (SQLite)
	OpNotLike                  // NOT LIKE
	OpRegex                    // REGEXP (SQLite) / ~ (Postgres)
	OpIn                       // IN (...)
	OpBetween                  // BETWEEN x AND y
)

// OrderClause — элемент ORDER BY.
type OrderClause struct {
	// Field — квотированное имя колонки.
	Field string
	// Desc — true для DESC, false для ASC.
	Desc bool
}

// ---------------------------------------------------------------------------
// Constructors — удобные функции для создания Condition.
// ---------------------------------------------------------------------------

// Eq создаёт условие равенства: field = value.
func Eq(field string, value any) Condition {
	return Condition{Field: field, Operator: OpEq, Value: value}
}

// Neq создаёт условие неравенства: field != value.
func Neq(field string, value any) Condition {
	return Condition{Field: field, Operator: OpNeq, Value: value}
}

// Lt создаёт условие "меньше": field < value.
func Lt(field string, value any) Condition {
	return Condition{Field: field, Operator: OpLt, Value: value}
}

// Lte создаёт условие "меньше или равно": field <= value.
func Lte(field string, value any) Condition {
	return Condition{Field: field, Operator: OpLte, Value: value}
}

// Gte создаёт условие "больше или равно": field >= value.
func Gte(field string, value any) Condition {
	return Condition{Field: field, Operator: OpGte, Value: value}
}

// Gt создаёт условие "больше": field > value.
func Gt(field string, value any) Condition {
	return Condition{Field: field, Operator: OpGt, Value: value}
}

// Like создаёт условие LIKE: field LIKE pattern.
func Like(field string, pattern string) Condition {
	return Condition{Field: field, Operator: OpLike, Value: pattern}
}

// ILike создаёт условие ILIKE: field ILIKE pattern (Postgres).
// Для SQLite LIKE уже case-insensitive, оператор тот же.
func ILike(field string, pattern string) Condition {
	return Condition{Field: field, Operator: OpILike, Value: pattern}
}

// Regexp создаёт условие regexp: field REGEXP pattern (SQLite) / field ~ pattern (Postgres).
func Regexp(field string, pattern string) Condition {
	return Condition{Field: field, Operator: OpRegex, Value: pattern}
}

// NotLike создаёт условие NOT LIKE: field NOT LIKE pattern.
func NotLike(field string, pattern string) Condition {
	return Condition{Field: field, Operator: OpNotLike, Value: pattern}
}

// In создаёт условие IN: field IN (values...).
func In(field string, values ...any) Condition {
	return Condition{Field: field, Operator: OpIn, Values: values}
}

// Between создаёт условие BETWEEN: field BETWEEN a AND b.
func Between(field string, a, b any) Condition {
	return Condition{Field: field, Operator: OpBetween, Values: []any{a, b}}
}

// And — группирует условия для читаемости; просто возвращает conds как есть.
// Использование: plan.Where = query.And(query.Eq("a", 1), query.Gt("b", 2))
func And(conds ...Condition) []Condition {
	return conds
}

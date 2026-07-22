// Package datasource — абстракция над источником данных для LLM-инструментов.
//
// DataSource interface скрывает детали реализации (SQL, API, CRM, NoSQL)
// за единым контрактом. Каждая имплементация отвечает за свой тип хранилища.
//
// Текущие имплементации:
//   - SQLDataSource — SQLite/Postgres через query.Engine
//
// Планируемые:
//   - APIDataSource — REST/gRPC API (CRM, ERP)
//   - NoSQLDataSource — MongoDB, Redis
package datasource

import "context"

// DataSource — универсальный интерфейс для LLM-инструментов над данными.
//
// Все методы работают через Query структуру, а не отдельные параметры,
// чтобы имплементации могли оптимизировать выполнение (например,
// объединять distinct + count в один SQL запрос).
type DataSource interface {
	// Type возвращает тип источника ("sql", "api", "nosql").
	Type() string

	// Search — текстовый поиск (grep-like).
	// q.Pattern обязателен. Ищет по всем текстовым полям.
	Search(ctx context.Context, q *Query) (*Result, error)

	// Filter — field-based фильтрация (filter-like).
	// q.Filters обязателен. Точные совпадения + операторы.
	Filter(ctx context.Context, q *Query) (*Result, error)

	// GetByID — получение одной записи по идентификатору.
	GetByID(ctx context.Context, entity string, id any) (*Record, error)

	// Count — количество записей, соответствующих фильтрам.
	Count(ctx context.Context, q *Query) (int64, error)

	// Distinct — уникальные значения колонки.
	Distinct(ctx context.Context, entity, column string) ([]string, error)

	// Schema — мета-информация о сущности (total, distinct, min/max/avg).
	// Используется LLM для discovery перед поиском.
	Schema(ctx context.Context, entity string) (*SchemaInfo, error)

	// Close закрывает соединение с источником данных.
	Close() error
}

// Query — универсальный запрос к DataSource.
type Query struct {
	// Entity — имя сущности (products, orders, customers).
	Entity string

	// Pattern — текстовый поисковый запрос (для Search).
	Pattern string

	// Filters — список field-фильтров (для Filter, Count).
	Filters []FieldFilter

	// Fields — список полей для поиска (пусто = все текстовые поля).
	Fields []string

	// Limit — максимальное количество записей (0 = default 10, max 100).
	Limit int

	// Offset — смещение для пагинации.
	Offset int

	// Format — compact (id + name) или full (все колонки).
	Format ResultFormat

	// TenantID — идентификатор tenant'а для изоляции. Заполняется сервером,
	// не доступен LLM через параметры инструмента.
	TenantID string
}

// FieldFilter — одно условие фильтрации по полю.
type FieldFilter struct {
	// Field — имя поля в терминах бизнес-логики.
	Field string

	// Operator — тип сравнения.
	Operator string // "eq", "neq", "gt", "gte", "lt", "lte", "like", "in"

	// Value — скалярное значение (для \"eq\", \"gt\", \"like\" и т.д.).
	Value any

	// Values — список значений (для \"in\").
	Values []any
}

// ResultFormat — формат результата.
type ResultFormat int

const (
	// FormatCompact — id + name preview.
	FormatCompact ResultFormat = iota

	// FormatFull — все колонки.
	FormatFull
)

// Result — структурированный результат поиска.
type Result struct {
	Total    int              `json:"total"`
	Returned int              `json:"returned"`
	Preview  []map[string]any `json:"preview,omitempty"`
	Data     []map[string]any `json:"data,omitempty"`
}

// Record — одна запись.
type Record struct {
	Fields map[string]any `json:"fields"`
}

// SchemaInfo — мета-информация о сущности для LLM.
type SchemaInfo struct {
	Entity string               `json:"entity"`
	Total  int64                `json:"total"`
	Fields map[string]FieldMeta `json:"fields"`
}

// FieldMeta — описание одного поля.
type FieldMeta struct {
	Type     string    `json:"type"`
	Distinct []string  `json:"distinct,omitempty"`
	Min      *float64  `json:"min,omitempty"`
	Max      *float64  `json:"max,omitempty"`
	Avg      *float64  `json:"avg,omitempty"`
}

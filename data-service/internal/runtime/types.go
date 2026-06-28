// Package runtime — query builder и entity resolver для config-driven
// data-service (фаза 3.2 roadmap).
//
// Слой runtime работает с ЛОКАЛЬНЫМИ типами (Entity, EntityField,
// CustomQuery, Endpoint), которые являются зеркалом типов из
// internal/config (фаза 3.2.a). В 3.2.d планируется либо заменить
// эти типы на алиасы к internal/config, либо написать тонкий
// конвертер — это тривиальная операция.
//
// Пакет не импортирует internal/config и internal/datasource напрямую:
//   - Конфигурация передаётся через локальные типы из этого файла.
//   - Доступ к БД идёт через минимальный интерфейс AdapterSubset
//     (QueryContext + QuoteIdentifier + TranslatePlaceholder).
//
// Цикл импортов: runtime → (только stdlib). Конкретные адаптеры
// (sqlite, postgres) импортируются только в main-сервере.
package runtime

import (
	"context"
	"database/sql"
	"fmt"
)

// Entity — публичное описание одной сущности (таблицы) клиента.
//
// Имена полей (Fields[].Name) — публичные (camelCase, для API/JSON).
// Имена колонок (Fields[].Column) — внутренние (snake_case, как в БД).
// Маппинг обязателен — задача UI и API-контракта.
type Entity struct {
	// Name — публичное имя сущности (camelCase, "customer").
	Name string

	// Table — реальное имя таблицы в БД ("customers").
	Table string

	// IDColumn — имя PK-колонки в БД ("id").
	IDColumn string

	// Fields — публичные поля в порядке вывода.
	// Column указывает на колонку в БД.
	Fields []EntityField
}

// EntityField — одно поле сущности.
type EntityField struct {
	// Name — публичное имя поля ("fullName").
	Name string

	// Column — имя колонки в БД ("full_name").
	Column string

	// Type — generic-тип: string | int | float | bool | json | datetime | date.
	Type string

	// Nullable — допускает ли колонка/поле NULL.
	Nullable bool

	// PrimaryKey — колонка входит в PRIMARY KEY (обычно ровно одно на Entity).
	// Используется runtime-валидаторами и хинтами для query builder'а.
	PrimaryKey bool
}

// CustomQuery — escape-hatch: SELECT с фиксированным SQL и whitelist параметров.
//
// SQL использует generic '?' placeholder'ы, builder превращает их в
// нативный синтаксис СУБД через AdapterSubset.TranslatePlaceholder.
//
// ResultMapping описывает типы колонок результата, чтобы response_mapper
// мог корректно привести значения.
type CustomQuery struct {
	// SQL — SELECT-выражение с '?' placeholder'ами.
	// Whitelist операций: только SELECT, запрет ';', обязателен MaxRows.
	SQL string

	// Params — имена параметров в порядке placeholder'ов.
	// Длина должна совпадать с числом '?' в SQL.
	Params []string

	// ResultMapping — маппинг колонка → тип результата.
	// Имена колонок — как в SQL.
	ResultMapping map[string]ResultMappingField

	// MaxRows — верхняя граница количества возвращаемых строк.
	MaxRows int
}

// ResultMappingField — тип и nullability одной колонки результата custom query.
type ResultMappingField struct {
	// Type — generic-тип значения.
	Type string

	// Nullable — допускает ли NULL.
	Nullable bool
}

// Endpoint — описание HTTP endpoint'а в конфиге.
//
// Op выбирает builtin-handler ("get_by_id", "find", "list",
// "custom_query", "builtin_health"). Остальные поля описывают
// привязку к Entity/CustomQuery.
type Endpoint struct {
	// Method — HTTP-метод ("GET", "POST", ...).
	Method string

	// Path — путь с placeholder'ами в стиле chi ("/students/{id}").
	Path string

	// Op — имя операции builtin-handler'а.
	Op string

	// Entity — публичное имя сущности (для op=get_by_id/find/list).
	Entity string

	// SearchField — публичное имя поля для поиска (для op=find).
	SearchField string

	// QueryParam — имя query-параметра (для op=find/list).
	QueryParam string

	// QueryID — идентификатор custom_query (для op=custom_query).
	QueryID string
}

// AdapterSubset — минимальный интерфейс адаптера, который нужен
// query builder'у и response mapper'у.
//
// Полный datasource.Adapter (фаза 3.1) содержит больше методов
// (Introspect, Connect, Driver), но builder работает только с
// этими тремя. Это разрывает жёсткую связь между runtime и
// datasource — runtime не импортирует datasource напрямую.
type AdapterSubset interface {
	// QueryContext выполняет SELECT с prepared args и возвращает *sql.Rows.
	// Сигнатура совпадает с database/sql.Conn и с datasource.Conn.
	QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error)

	// PingContext проверяет доступность соединения с БД.
	PingContext(ctx context.Context) error

	// QuoteIdentifier корректно квотирует идентификатор для SQL.
	QuoteIdentifier(name string) string

	// TranslatePlaceholder превращает порядковый номер generic '?'
	// в нативный синтаксис СУБД (sqlite → "?", postgres → "$N").
	TranslatePlaceholder(index int) string
}

// Query — результат работы query builder'а: SQL с native placeholder'ами
// и args для передачи в database/sql.Conn.QueryContext.
type Query struct {
	// SQL — собранный SELECT с нативными placeholder'ами.
	SQL string

	// Args — параметры для prepared statement (в том же порядке, что '?' в SQL).
	Args []any
}

// QueryError — ошибка построения/выполнения запроса с контекстом операции.
//
// Reason — человеко-читаемая причина ("unknown field", "arg count mismatch").
// Op — имя операции ("BuildGetByID", "BuildFind", ...).
// Err — обёрнутая ошибка (если есть).
type QueryError struct {
	// Op — имя операции или метода, где произошла ошибка.
	Op string

	// Reason — человеко-читаемая причина.
	Reason string

	// Err — нижележащая ошибка (nil допустим для программных ошибок).
	Err error
}

// Error реализует интерфейс error.
func (e *QueryError) Error() string {
	if e.Err != nil {
		return fmt.Sprintf("runtime: %s: %s: %v", e.Op, e.Reason, e.Err)
	}
	return fmt.Sprintf("runtime: %s: %s", e.Op, e.Reason)
}

// Unwrap возвращает нижележащую ошибку для errors.Is/As.
func (e *QueryError) Unwrap() error { return e.Err }
package config

import "fmt"

// Driver — допустимые значения cfg.data_source.driver.
type Driver string

const (
	DriverSQLite   Driver = "sqlite"
	DriverPostgres Driver = "postgres"
)

// Valid проверяет, что значение входит в whitelist из schema.
func (d Driver) Valid() bool {
	switch d {
	case DriverSQLite, DriverPostgres:
		return true
	}
	return false
}

// HTTPMethod — допустимые HTTP-методы для endpoint'ов.
type HTTPMethod string

const (
	MethodGET    HTTPMethod = "GET"
	MethodPOST   HTTPMethod = "POST"
	MethodPUT    HTTPMethod = "PUT"
	MethodPATCH  HTTPMethod = "PATCH"
	MethodDELETE HTTPMethod = "DELETE"
)

// Valid проверяет, что метод входит в whitelist.
func (m HTTPMethod) Valid() bool {
	switch m {
	case MethodGET, MethodPOST, MethodPUT, MethodPATCH, MethodDELETE:
		return true
	}
	return false
}

// Op — реализация endpoint'а (builtin, get_by_id, find, list, custom_query).
type Op string

const (
	OpBuiltinHealth   Op = "builtin_health"
	OpBuiltinStats    Op = "builtin_stats"
	OpGetByID         Op = "get_by_id"
	OpFind            Op = "find"
	OpList            Op = "list"
	OpCustomQuery     Op = "custom_query"
)

// Valid проверяет, что op входит в whitelist.
func (o Op) Valid() bool {
	switch o {
	case OpBuiltinHealth, OpBuiltinStats, OpGetByID, OpFind, OpList, OpCustomQuery:
		return true
	}
	return false
}

// RelationKind — тип связи между сущностями.
type RelationKind string

const (
	RelationManyToOne   RelationKind = "many_to_one"
	RelationOneToMany   RelationKind = "one_to_many"
	RelationManyToMany  RelationKind = "many_to_many"
)

// Valid проверяет, что kind входит в whitelist.
func (r RelationKind) Valid() bool {
	switch r {
	case RelationManyToOne, RelationOneToMany, RelationManyToMany:
		return true
	}
	return false
}

// ParamIn — где расположен параметр endpoint'а.
type ParamIn string

const (
	ParamInPath  ParamIn = "path"
	ParamInQuery ParamIn = "query"
	ParamInBody  ParamIn = "body"
)

// Valid проверяет, что in входит в whitelist.
func (p ParamIn) Valid() bool {
	switch p {
	case ParamInPath, ParamInQuery, ParamInBody:
		return true
	}
	return false
}

// ParamType — generic-тип параметра endpoint'а.
type ParamType string

const (
	ParamTypeString ParamType = "string"
	ParamTypeInt    ParamType = "int"
	ParamTypeFloat  ParamType = "float"
	ParamTypeBool   ParamType = "bool"
)

// Valid проверяет, что type входит в whitelist.
func (t ParamType) Valid() bool {
	switch t {
	case ParamTypeString, ParamTypeInt, ParamTypeFloat, ParamTypeBool:
		return true
	}
	return false
}

// FieldType — generic-тип поля сущности и колонки результата custom_query.
type FieldType string

const (
	FieldTypeString   FieldType = "string"
	FieldTypeInt      FieldType = "int"
	FieldTypeFloat    FieldType = "float"
	FieldTypeBool     FieldType = "bool"
	FieldTypeJSON     FieldType = "json"
	FieldTypeDatetime FieldType = "datetime"
	FieldTypeDate     FieldType = "date"
)

// Valid проверяет, что type входит в whitelist FieldType.
func (f FieldType) Valid() bool {
	switch f {
	case FieldTypeString, FieldTypeInt, FieldTypeFloat, FieldTypeBool,
		FieldTypeJSON, FieldTypeDatetime, FieldTypeDate:
		return true
	}
	return false
}

// AuthStrategy — стратегия multi-tenancy isolation.
type AuthStrategy string

const (
	AuthStrategyNone   AuthStrategy = "none"
	AuthStrategyHeader AuthStrategy = "header"
)

// Valid проверяет, что strategy входит в whitelist.
func (a AuthStrategy) Valid() bool {
	switch a {
	case AuthStrategyNone, AuthStrategyHeader:
		return true
	}
	return false
}

// Config — корневая структура config.json.
//
// Обязательные поля: Version, DataSource.
// Все остальные — опциональные (могут быть nil/пустыми).
type Config struct {
	// Version — версия схемы конфига. На данный момент — всегда 1.
	Version int `json:"version"`

	// DataSource — подключение к клиентской БД. Обязательное.
	DataSource DataSourceConfig `json:"data_source"`

	// Introspection — настройки auto-discovery схемы БД. Опционально.
	Introspection *IntrospectionConfig `json:"introspection,omitempty"`

	// Entities — описание доменных сущностей клиента.
	Entities []Entity `json:"entities,omitempty"`

	// Endpoints — REST endpoints, публикуемые data-service.
	Endpoints []Endpoint `json:"endpoints,omitempty"`

	// CustomQueries — whitelist SQL-запросов для op=custom_query.
	CustomQueries map[string]CustomQuery `json:"custom_queries,omitempty"`

	// Stats — конфигурация endpoint'а /stats.
	Stats *StatsConfig `json:"stats,omitempty"`

	// MCPTools — описание MCP-инструментов (для фазы 3.4).
	MCPTools []MCPTool `json:"mcp_tools,omitempty"`

	// Auth — multi-tenancy и row-level isolation (для фазы 3.7).
	Auth *AuthConfig `json:"auth,omitempty"`

	// Server — настройки HTTP-сервера (таймауты, лимиты). Опционально.
	Server *ServerConfig `json:"server,omitempty"`

	// ApprovedTools — список путей write-эндпоинтов, утверждённых для использования
	// в read-only режиме. Каждый элемент — path из endpoints[].
	// Если пустой или nil — write-доступ запрещён для всех эндпоинтов.
	ApprovedTools []string `json:"approved_tools,omitempty"`
}

// DataSourceConfig — подключение к клиентской БД.
type DataSourceConfig struct {
	// Driver — драйвер СУБД ("sqlite" | "postgres").
	Driver Driver `json:"driver"`

	// DSN — строка подключения. Поддерживает ${ENV} подстановки.
	DSN string `json:"dsn"`

	// PoolSize — максимум одновременных соединений. nil если не задан.
	PoolSize *int `json:"pool_size,omitempty"`

	// ReadOnly — запрет на мутирующие операции. nil если не задан.
	ReadOnly *bool `json:"read_only,omitempty"`

	// ReadonlyDSN — строка подключения с правами только на чтение (database-level).
	// Если задана, data-service использует её для всех запросов от AI-агента.
	// Основная DSN остаётся для admin-операций (introspect, config rewrite).
	//
	// Для SQLite: та же dsn, но (при необходимости) с PRAGMA query_only = 1.
	// Для PostgreSQL: DSN от пользователя с правами только на SELECT.
	// Если не задана — агент работает через ту же DSN (app-level read_only).
	ReadonlyDSN string `json:"readonly_dsn,omitempty"`
}

// IntrospectionConfig — настройки auto-discovery схемы БД при старте.
type IntrospectionConfig struct {
	// Enabled — включить интроспекцию. nil если не задан.
	Enabled *bool `json:"enabled,omitempty"`

	// IncludeSchemas — schemas/базы для интроспекции (Postgres only).
	IncludeSchemas []string `json:"include_schemas,omitempty"`

	// ExcludeTables — regex'ы для имён таблиц, которые нужно исключить.
	ExcludeTables []string `json:"exclude_tables,omitempty"`
}

// Entity — доменная сущность = одна таблица в клиентской БД.
type Entity struct {
	// Name — публичное имя сущности в API (snake_case).
	Name string `json:"name"`

	// Table — имя таблицы в БД.
	Table string `json:"table"`

	// IDColumn — имя колонки с первичным ключом.
	IDColumn string `json:"id_column"`

	// Description — человекочитаемое описание.
	Description string `json:"description,omitempty"`

	// Fields — маппинг публичных полей на колонки БД.
	Fields []EntityField `json:"fields"`

	// Relations — связи с другими сущностями.
	Relations []Relation `json:"relations,omitempty"`
}

// EntityField — поле сущности. name — публичное имя, column — имя колонки.
type EntityField struct {
	// Name — публичное имя поля в API (snake_case).
	Name string `json:"name"`

	// Column — имя колонки в таблице БД.
	Column string `json:"column"`

	// Type — generic-тип поля.
	Type FieldType `json:"type"`

	// Nullable — может ли поле быть NULL. nil если не задан.
	Nullable *bool `json:"nullable,omitempty"`

	// PrimaryKey — является ли колонка первичным ключом. nil если не задан.
	PrimaryKey *bool `json:"primary_key,omitempty"`

	// Description — описание поля.
	Description string `json:"description,omitempty"`
}

// Relation — связь между сущностями.
type Relation struct {
	// Field — имя поля в публичной схеме.
	Field string `json:"field"`

	// Kind — тип связи (many_to_one / one_to_many / many_to_many).
	Kind RelationKind `json:"kind"`

	// Table — имя связанной таблицы в БД.
	Table string `json:"table"`

	// LocalFK — имя FK-колонки в текущей таблице.
	LocalFK string `json:"local_fk"`

	// TargetFK — имя FK-колонки в связанной таблице (для many_to_many).
	TargetFK string `json:"target_fk,omitempty"`
}

// Endpoint — REST endpoint. method+path — публичный контракт, op — реализация.
type Endpoint struct {
	// Method — HTTP метод.
	Method HTTPMethod `json:"method"`

	// Path — URL-путь. Поддерживает {param}.
	Path string `json:"path"`

	// Op — реализация (builtin / get_by_id / find / list / custom_query).
	Op Op `json:"op"`

	// Entity — имя entity (для op=get_by_id, find, list).
	Entity string `json:"entity,omitempty"`

	// SearchField — имя поля для поиска (для op=find).
	SearchField string `json:"search_field,omitempty"`

	// QueryParam — имя query-параметра для значения поиска.
	QueryParam string `json:"query_param,omitempty"`

	// QueryID — ключ из custom_queries (для op=custom_query).
	QueryID string `json:"query_id,omitempty"`

	// Params — описание параметров endpoint'а.
	Params []EndpointParam `json:"params,omitempty"`

	// Description — описание endpoint'а.
	Description string `json:"description,omitempty"`
}

// EndpointParam — параметр endpoint'а.
type EndpointParam struct {
	// Name — имя параметра.
	Name string `json:"name"`

	// In — расположение параметра (path / query / body).
	In ParamIn `json:"in"`

	// Type — тип параметра.
	Type ParamType `json:"type,omitempty"`

	// Required — обязательный ли параметр. nil если не задан.
	Required *bool `json:"required,omitempty"`

	// Description — описание параметра.
	Description string `json:"description,omitempty"`
}

// CustomQuery — whitelist SQL-запрос.
type CustomQuery struct {
	// SQL — SQL-запрос. Должен начинаться с SELECT.
	SQL string `json:"sql"`

	// Params — имена параметров в порядке placeholder'ов '?' в SQL.
	Params []string `json:"params,omitempty"`

	// ResultMapping — маппинг колонок результата на типы.
	ResultMapping map[string]ResultMappingField `json:"result_mapping"`

	// MaxRows — максимум строк в результате. Hard limit.
	MaxRows int `json:"max_rows"`

	// Description — описание запроса.
	Description string `json:"description,omitempty"`
}

// ResultMappingField — тип колонки результата custom_query.
type ResultMappingField struct {
	// Type — generic-тип колонки.
	Type FieldType `json:"type"`

	// Nullable — допускает ли колонка NULL. nil если не задан.
	Nullable *bool `json:"nullable,omitempty"`
}

// StatsConfig — конфигурация endpoint'а /stats.
type StatsConfig struct {
	// Counters — счётчики для /stats.
	Counters []Counter `json:"counters,omitempty"`
}

// Counter — один счётчик для /stats.
type Counter struct {
	// Name — имя счётчика в ответе (snake_case).
	Name string `json:"name"`

	// Entity — имя entity из entities[].
	Entity string `json:"entity"`

	// Filter — опциональный WHERE для подсчёта.
	Filter string `json:"filter,omitempty"`
}

// MCPTool — описание MCP-инструмента.
type MCPTool struct {
	// Name — имя инструмента (snake_case).
	Name string `json:"name"`

	// Endpoint — путь endpoint'а из endpoints[].
	Endpoint string `json:"endpoint"`

	// Description — описание для агента (model-facing).
	Description string `json:"description"`

	// Params — описание параметров инструмента.
	Params []EndpointParam `json:"params,omitempty"`
}

// AuthConfig — multi-tenancy и row-level isolation.
type AuthConfig struct {
	// Strategy — стратегия изоляции тенантов.
	Strategy AuthStrategy `json:"strategy,omitempty"`

	// TenantHeader — имя заголовка для передачи tenant_id.
	TenantHeader string `json:"tenant_header,omitempty"`

	// RowFilters — дополнительные WHERE для multi-tenant isolation.
	RowFilters []RowFilter `json:"row_filters,omitempty"`
}

// RowFilter — дополнительный WHERE для multi-tenant isolation.
type RowFilter struct {
	// Entity — имя entity.
	Entity string `json:"entity"`

	// Where — WHERE-выражение. Поддерживает placeholder :tenant_id.
	Where string `json:"where"`
}

// ServerConfig — настройки HTTP-сервера data-service.
type ServerConfig struct {
	// RequestTimeoutSeconds — таймаут обработки запроса в секундах.
	// По умолчанию 30. Переопределяется через DS_REQUEST_TIMEOUT.
	RequestTimeoutSeconds *int `json:"request_timeout_seconds,omitempty"`

	// BodyLimitMB — максимальный размер тела запроса в MB.
	// По умолчанию 10. Переопределяется через DS_BODY_LIMIT_MB.
	BodyLimitMB *int `json:"body_limit_mb,omitempty"`

	// MaxConcurrent — максимум одновременных запросов.
	// По умолчанию 100. Переопределяется через DS_MAX_CONCURRENT.
	MaxConcurrent *int `json:"max_concurrent,omitempty"`
}

// String возвращает строковое представление Config (для логирования).
// Реализация намеренно лаконичная — детали в полях структуры.
func (c *Config) String() string {
	if c == nil {
		return "<nil config>"
	}
	return fmt.Sprintf("Config{version=%d, driver=%s, entities=%d, endpoints=%d, custom_queries=%d, mcp_tools=%d, server=%v}",
		c.Version, c.DataSource.Driver,
		len(c.Entities), len(c.Endpoints), len(c.CustomQueries), len(c.MCPTools),
		c.Server)
}

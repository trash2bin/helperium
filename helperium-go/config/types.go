package config

import (
	"fmt"
	"regexp"
	"strings"
)

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

// Op — реализация endpoint'а (builtin, get_by_id, strategy, custom_query, distinct, count).
// Legacy op="find"/op="list" НЕ поддерживаются (удалены в v4) — Validate их отвергает.
type Op string

const (
	OpBuiltinHealth   Op = "builtin_health"
	OpBuiltinStats    Op = "builtin_stats"
	OpGetByID         Op = "get_by_id"
	OpStrategy        Op = "strategy"
	OpCustomQuery     Op = "custom_query"
	OpDistinct        Op = "distinct"
	OpCount           Op = "count"
)

// Valid проверяет, что op входит в whitelist.
func (o Op) Valid() bool {
	switch o {
	case OpBuiltinHealth, OpBuiltinStats, OpGetByID, OpStrategy, OpCustomQuery, OpDistinct, OpCount:
		return true
	}
	return false
}

// validStrategy проверяет, что имя strategy входит в whitelist.
func validStrategy(s string) bool {
	switch s {
	case "grep", "filter", "schema":
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
	// Version — версия схемы конфига. Нормализуется через Normalize().
	Version int `json:"version"`

	// Meta — мета-информация о конфиге (когда/чем сгенерирован).
	Meta *ConfigMeta `json:"meta,omitempty"`

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

	// SkipRules — таблицы для исключения при генерации. Дополняет DefaultSkipRules.
	SkipRules []SkipRule `json:"skip_rules,omitempty"`

	// DisabledDefaultRules — список prefix дефолтных skip rules, которые нужно отключить.
	DisabledDefaultRules []string `json:"disabled_default_rules,omitempty"`

	// DisplayPrefixes — префиксы имён таблиц, отрезаемые от display_name.
	DisplayPrefixes []string `json:"display_prefixes,omitempty"`

	// CustomPlurals — кастомные plural-формы для tool display names.
	CustomPlurals map[string]string `json:"custom_plurals,omitempty"`

	// Field-level rules — настройка filterable/searchable/enum правил через конфиг.
	// Паттерн как у SkipRules: Default*() + DisabledDefault* + Custom*.

	// FilterableRules — дополняют дефолтные правила фильтруемых полей.
	// Поле считается filterable если: FK (*_id), бизнес-дата (*_date), бизнес-бул,
	// или проходит хотя бы одно из правил.
	FilterableRules []FieldRule `json:"filterable_rules,omitempty"`
	// DisabledDefaultFilterableRules — список имён дефолтных filterable rules для отключения.
	DisabledDefaultFilterableRules []string `json:"disabled_default_filterable_rules,omitempty"`

	// SearchableRules — дополняют дефолтные правила поисковых полей.
	// Поле считается searchable если: string-тип, не block-правило.
	SearchableRules []FieldRule `json:"searchable_rules,omitempty"`
	// DisabledDefaultSearchableRules — список имён дефолтных searchable rules для отключения.
	DisabledDefaultSearchableRules []string `json:"disabled_default_searchable_rules,omitempty"`

	// EnumRules — дополняют дефолтные правила enum-полей (для distinct).
	// Поле считается enum если: string-тип и проходит хотя бы одно из правил.
	EnumRules []FieldRule `json:"enum_rules,omitempty"`
	// DisabledDefaultEnumRules — список имён дефолтных enum rules для отключения.
	DisabledDefaultEnumRules []string `json:"disabled_default_enum_rules,omitempty"`

	// CustomShortNames — кастомные короткие имена для entity display_name.
	// Ключ — короткое имя (например "cartitem"), значение — display name ("Cart item").
	CustomShortNames map[string]string `json:"custom_short_names,omitempty"`
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

// IDColumnOrDefault возвращает IDColumn или первую PK-колонку или "id".
func (e Entity) IDColumnOrDefault() string {
	if e.IDColumn != "" {
		return e.IDColumn
	}
	for _, f := range e.Fields {
		if f.PrimaryKey != nil && *f.PrimaryKey {
			return f.Column
		}
	}
	return "id"
}

// FindColumn возвращает имя DB-колонки по публичному имени поля или имени колонки.
// Если поле не найдено — возвращает пустую строку.
func (e Entity) FindColumn(fieldName string) string {
	for _, f := range e.Fields {
		if f.Name == fieldName || f.Column == fieldName {
			return f.Column
		}
	}
	return ""
}

// FirstStringFieldColumn возвращает первую не-PK строковую колонку.
func (e Entity) FirstStringFieldColumn() string {
	for _, f := range e.Fields {
		if f.PrimaryKey != nil && *f.PrimaryKey {
			continue
		}
		if f.Type == FieldTypeString {
			return f.Column
		}
	}
	return ""
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

	// ExcludeFromSearch — не участвует в поиске (grep/filter).
	// Полезно для PII-полей (email, phone, passport).
	ExcludeFromSearch bool `json:"exclude_from_search,omitempty"`
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

	// JunctionTable — имя junction-таблицы для many_to_many.
	JunctionTable string `json:"junction_table,omitempty"`
}

// Endpoint — REST endpoint. method+path — публичный контракт, op — реализация.
type Endpoint struct {
	// Method — HTTP метод.
	Method HTTPMethod `json:"method"`

	// Path — URL-путь. Поддерживает {param}.
	Path string `json:"path"`

	// Op — реализация (builtin / get_by_id / strategy / custom_query).
	Op Op `json:"op"`

	// Entity — имя entity (для op=get_by_id, strategy).
	Entity string `json:"entity,omitempty"`

	// QueryID — ключ из custom_queries (для op=custom_query).
	QueryID string `json:"query_id,omitempty"`

	// Strategy — имя search strategy ("grep", "filter", "schema").
	// Если пусто — используется Op-based routing (legacy).
	Strategy string `json:"strategy,omitempty"`

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

	// Type — тип параметра для простых значений.
	Type ParamType `json:"type,omitempty"`

	// ArrayOf — для array-параметров: тип элементов.
	// {"name":"ids", "type":"array", "array_of":"int"}
	ArrayOf ParamType `json:"array_of,omitempty"`

	// EnumValues — допустимые значения для enum-параметров.
	EnumValues []string `json:"enum_values,omitempty"`

	// Required — обязательный ли параметр.
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

	// DisplayName — публичное имя для отображения пользователю в UI.
	// Если пусто, UI использует Name.
	// Заполняется вручную через admin API или напрямую в config.json.
	DisplayName string `json:"display_name,omitempty"`

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

// SkipRule defines a pattern for tables to exclude from tool generation.
// Multiple fields are AND-ed — all non-empty fields must match.
type SkipRule struct {
	Prefix   string `json:"prefix,omitempty"`
	Suffix   string `json:"suffix,omitempty"`
	Contains string `json:"contains,omitempty"`
	Reason   string `json:"reason,omitempty"`
}

// Matches returns true if the table name satisfies this rule (AND logic).
func (r SkipRule) Matches(name string) bool {
	// Empty rule matches nothing — protects against accidentally skipping all tables
	if r.Prefix == "" && r.Suffix == "" && r.Contains == "" {
		return false
	}
	if r.Prefix != "" && !strings.HasPrefix(name, r.Prefix) {
		return false
	}
	if r.Suffix != "" && !strings.HasSuffix(name, r.Suffix) {
		return false
	}
	if r.Contains != "" && !strings.Contains(name, r.Contains) {
		return false
	}
	return true
}

// FieldRule defines a pattern for field-level allow/block rules.
// Used for controlling which fields are searchable, filterable, or enum-eligible.
// Allow* fields use OR logic: at least one must match.
// Block* fields use OR logic: if any matches, the rule blocks.
// If all Allow* slices are empty, the rule is treated as an allow-all (block-only rule).
type FieldRule struct {
	// ID — стабильный идентификатор правила (для disabled_default_*_rules).
	// Дефолтные правила имеют фиксированные ID ('filterable.common',
	// 'searchable.block_image', 'enum.contains'). Reason — только описательный,
	// для матчинга не используется.
	ID            string   `json:"id,omitempty"`
	AllowNames    []string `json:"allow_names,omitempty"`
	AllowSuffix   []string `json:"allow_suffix,omitempty"`
	AllowContains []string `json:"allow_contains,omitempty"`
	BlockNames    []string `json:"block_names,omitempty"`
	BlockSuffix   []string `json:"block_suffix,omitempty"`
	BlockContains []string `json:"block_contains,omitempty"`
	Reason        string   `json:"reason,omitempty"`
}

// Matches checks if a field name matches this rule according to the FieldRule semantics.
//   - If any Allow* is non-empty: at least one must match (OR), or the field is rejected.
//   - If all Allow* are empty: the allow check passes (treat as block-only rule).
//   - Then Block*: if any matches (OR), the field is rejected.
func (r FieldRule) Matches(name string) bool {
	hasAllow := len(r.AllowNames) > 0 || len(r.AllowSuffix) > 0 || len(r.AllowContains) > 0

	if hasAllow {
		allowMatch := false
		for _, a := range r.AllowNames {
			if a == name {
				allowMatch = true
				break
			}
		}
		if !allowMatch {
			for _, s := range r.AllowSuffix {
				if strings.HasSuffix(name, s) {
					allowMatch = true
					break
				}
			}
		}
		if !allowMatch {
			for _, c := range r.AllowContains {
				if strings.Contains(name, c) {
					allowMatch = true
					break
				}
			}
		}
		if !allowMatch {
			return false
		}
	}

	for _, b := range r.BlockNames {
		if b == name {
			return false
		}
	}
	for _, s := range r.BlockSuffix {
		if strings.HasSuffix(name, s) {
			return false
		}
	}
	for _, c := range r.BlockContains {
		if strings.Contains(name, c) {
			return false
		}
	}

	return true
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

// Validate проверяет конфиг на уровне Go-типов: обязательные поля,
// enum-значения, перекрёстные ссылки между entities/endpoints/queries.
//
// Ранее валидация была во внешнем JSON Schema файле. Теперь все проверки
// живут в Go-коде и не могут рассинхронизироваться с типами.
func (c *Config) Validate() error {
	// Auto-normalize: ensures backward compatibility even when
	// Validate() is called without a preceding Normalize().
	c.Normalize()

	var errs []string

	// ── Version ───────────────────────────────────────────────────────
	if c.Version != CurrentConfigVersion {
		errs = append(errs, fmt.Sprintf("version: expected %d after Normalize, got %d", CurrentConfigVersion, c.Version))
	}

	// ── Meta ──────────────────────────────────────────────────────────
	if c.Meta != nil && c.Meta.ConfigVersion != c.Version {
		errs = append(errs, fmt.Sprintf("meta.config_version (%d) != version (%d)", c.Meta.ConfigVersion, c.Version))
	}

	// ── DataSource ────────────────────────────────────────────────────
	if c.DataSource.Driver == "" {
		errs = append(errs, "data_source.driver: required")
	} else if !c.DataSource.Driver.Valid() {
		errs = append(errs, fmt.Sprintf("data_source.driver: unsupported %q", c.DataSource.Driver))
	}
	if c.DataSource.DSN == "" {
		errs = append(errs, "data_source.dsn: required")
	}

	// ── Entity names index ────────────────────────────────────────────
	entityNames := make(map[string]bool, len(c.Entities))
	for i, e := range c.Entities {
		if e.Name == "" {
			errs = append(errs, fmt.Sprintf("entities[%d].name: required", i))
		} else if entityNames[e.Name] {
			errs = append(errs, fmt.Sprintf("entities[%d].name: duplicate %q", i, e.Name))
		} else {
			entityNames[e.Name] = true
		}
		if e.Table == "" {
			errs = append(errs, fmt.Sprintf("entities[%d].table: required", i))
		}
		if e.IDColumn == "" {
			errs = append(errs, fmt.Sprintf("entities[%d].id_column: required", i))
		}
		if len(e.Fields) == 0 {
			errs = append(errs, fmt.Sprintf("entities[%d].fields: at least one field required", i))
		}
		for j, f := range e.Fields {
			if f.Name == "" {
				errs = append(errs, fmt.Sprintf("entities[%d].fields[%d].name: required", i, j))
			}
			if f.Column == "" {
				errs = append(errs, fmt.Sprintf("entities[%d].fields[%d].column: required", i, j))
			}
			if !f.Type.Valid() {
				errs = append(errs, fmt.Sprintf("entities[%d].fields[%d].type: unsupported %q", i, j, f.Type))
			}
		}

		// ── Relations ────────────────────────────────────────────────
		for k, r := range e.Relations {
			if r.Field == "" {
				errs = append(errs, fmt.Sprintf("entities[%d].relations[%d].field: required", i, k))
			}
			if !r.Kind.Valid() {
				errs = append(errs, fmt.Sprintf("entities[%d].relations[%d].kind: unsupported %q", i, k, r.Kind))
			}
			if r.Table == "" {
				errs = append(errs, fmt.Sprintf("entities[%d].relations[%d].table: required", i, k))
			}
			if r.LocalFK == "" {
				errs = append(errs, fmt.Sprintf("entities[%d].relations[%d].local_fk: required", i, k))
			}
			if r.Kind == RelationManyToMany && r.JunctionTable == "" {
				errs = append(errs, fmt.Sprintf("entities[%d].relations[%d]: junction_table required for many_to_many", i, k))
			}
		}
	}

	// ── Endpoints ─────────────────────────────────────────────────────
	for i, ep := range c.Endpoints {
		if !ep.Method.Valid() {
			errs = append(errs, fmt.Sprintf("endpoints[%d].method: unsupported %q", i, ep.Method))
		}
		if ep.Path == "" {
			errs = append(errs, fmt.Sprintf("endpoints[%d].path: required", i))
		}
		if !ep.Op.Valid() {
			errs = append(errs, fmt.Sprintf("endpoints[%d].op: unsupported %q", i, ep.Op))
		}
		switch ep.Op {
		case OpGetByID:
			if ep.Entity == "" {
				errs = append(errs, fmt.Sprintf("endpoints[%d].entity: required for op=%q", i, ep.Op))
			} else if !entityNames[ep.Entity] {
				errs = append(errs, fmt.Sprintf("endpoints[%d].entity %q not found in entities", i, ep.Entity))
			}
		case OpStrategy:
			if ep.Entity == "" {
				errs = append(errs, fmt.Sprintf("endpoints[%d].entity: required for op=%q", i, ep.Op))
			} else if !entityNames[ep.Entity] {
				errs = append(errs, fmt.Sprintf("endpoints[%d].entity %q not found in entities", i, ep.Entity))
			}
			if ep.Strategy == "" {
				errs = append(errs, fmt.Sprintf("endpoints[%d].strategy: required for op=%q", i, ep.Op))
			} else if !validStrategy(ep.Strategy) {
				errs = append(errs, fmt.Sprintf("endpoints[%d].strategy: unknown %q, must be one of: grep, filter, schema", i, ep.Strategy))
			}
		case OpCustomQuery:
			if ep.QueryID == "" {
				errs = append(errs, fmt.Sprintf("endpoints[%d].query_id: required for op=custom_query", i))
			} else if _, exists := c.CustomQueries[ep.QueryID]; !exists {
				errs = append(errs, fmt.Sprintf("endpoints[%d].query_id %q not found in custom_queries", i, ep.QueryID))
			}
		}
		for j, p := range ep.Params {
			if p.Name == "" {
				errs = append(errs, fmt.Sprintf("endpoints[%d].params[%d].name: required", i, j))
			}
			if !p.In.Valid() {
				errs = append(errs, fmt.Sprintf("endpoints[%d].params[%d].in: unsupported %q", i, j, p.In))
			}
			if p.Type != "" && !p.Type.Valid() {
				errs = append(errs, fmt.Sprintf("endpoints[%d].params[%d].type: unsupported %q", i, j, p.Type))
			}
			if p.ArrayOf != "" && !p.ArrayOf.Valid() {
				errs = append(errs, fmt.Sprintf("endpoints[%d].params[%d].array_of: unsupported %q", i, j, p.ArrayOf))
			}
		}
	}

	// ── Custom queries ────────────────────────────────────────────────
	for qk, q := range c.CustomQueries {
		if q.SQL == "" {
			errs = append(errs, fmt.Sprintf("custom_queries[%q].sql: required", qk))
		}
		if q.MaxRows <= 0 || q.MaxRows > 10000 {
			errs = append(errs, fmt.Sprintf("custom_queries[%q].max_rows: out of range (1-10000)", qk))
		}
	}

	// ── MCP tools ─────────────────────────────────────────────────────
	for i, mt := range c.MCPTools {
		if mt.Name == "" {
			errs = append(errs, fmt.Sprintf("mcp_tools[%d].name: required", i))
		}
		if mt.Endpoint == "" {
			errs = append(errs, fmt.Sprintf("mcp_tools[%d].endpoint: required", i))
		} else {
			found := false
			for _, ep := range c.Endpoints {
				if ep.Path == mt.Endpoint {
					found = true
					break
				}
			}
			if !found {
				errs = append(errs, fmt.Sprintf("mcp_tools[%d].endpoint %q not found in endpoints", i, mt.Endpoint))
			}
		}
		if mt.Description == "" {
			errs = append(errs, fmt.Sprintf("mcp_tools[%d].description: required", i))
		}
	}

	// ── Auth ──────────────────────────────────────────────────────────
	if c.Auth != nil {
		if !c.Auth.Strategy.Valid() {
			errs = append(errs, fmt.Sprintf("auth.strategy: unsupported %q", c.Auth.Strategy))
		}
		// P0-1 fail-closed: при ЛЮБОЙ реальной стратегии (не "none") КАЖДАЯ entity
		// обязана иметь row_filter. Иначе запрос к непокрытой entity вернёт 403
		// в рантайме (tenantFilter deny), что клиент обнаружит только в проде.
		// Ловим на онбординге: конфиг с auth и entity без row_filter — невалиден.
		// Инвариант по факту (auth != none), а не по имени стратегии — будущие
		// стратегии (jwt, api_key) автоматически под защитой.
		if c.Auth.Strategy != AuthStrategyNone {
			covered := make(map[string]bool, len(c.Auth.RowFilters))
			for _, rf := range c.Auth.RowFilters {
				covered[rf.Entity] = true
			}
			for i := range c.Entities {
				if !covered[c.Entities[i].Name] {
					errs = append(errs, fmt.Sprintf(
						"auth.row_filters: entity %q has no row_filter under strategy=%q "+
							"(fail-closed: requests to it would return 403)",
						c.Entities[i].Name, c.Auth.Strategy))
				}
			}
		}
		// RowFilter.Where валидируется как безопасное WHERE-выражение (тот же
		// механизм, что и counter.Filter). Без этого битый RowFilter (напр.
		// несуществующая колонка или инъекция) ронял /stats и другие хендлеры.
		for i, rf := range c.Auth.RowFilters {
			if rf.Entity == "" {
				errs = append(errs, fmt.Sprintf("auth.row_filters[%d].entity: required", i))
			} else if !entityNames[rf.Entity] {
				errs = append(errs, fmt.Sprintf("auth.row_filters[%d].entity %q not found in entities", i, rf.Entity))
			}
			if rf.Where == "" {
				errs = append(errs, fmt.Sprintf("auth.row_filters[%d].where: required", i))
			} else if !isValidFilterExpression(rf.Where) {
				errs = append(errs, fmt.Sprintf("auth.row_filters[%d].where: contains forbidden SQL construct", i))
			}
		}
	}

	// ── Stats ─────────────────────────────────────────────────────────
	if c.Stats != nil {
		for i, cnt := range c.Stats.Counters {
			if cnt.Name == "" {
				errs = append(errs, fmt.Sprintf("stats.counters[%d].name: required", i))
			}
			if cnt.Entity == "" {
				errs = append(errs, fmt.Sprintf("stats.counters[%d].entity: required", i))
			} else if !entityNames[cnt.Entity] {
				errs = append(errs, fmt.Sprintf("stats.counters[%d].entity %q not found in entities", i, cnt.Entity))
			}
			if cnt.Filter != "" && !isValidFilterExpression(cnt.Filter) {
				errs = append(errs, fmt.Sprintf("stats.counters[%d].filter: contains forbidden SQL construct", i))
			}
		}
	}

	if len(errs) == 0 {
		return nil
	}
	return fmt.Errorf("config validation: %s", strings.Join(errs, "; "))
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

// forbiddenSQLPatterns matches SQL keywords that should not appear in counter.Filter.
var forbiddenSQLPattern = regexp.MustCompile(`(?i)\b(DROP|INSERT|UPDATE|DELETE|ALTER|CREATE|TRUNCATE|EXEC|EXECUTE|UNION)\b`)

// isValidFilterExpression проверяет, что filter содержит только безопасные
// SQL WHERE-выражения. Запрещены multi-statement (;) и DDL/DML ключевые слова.
//
// Разрешены: column op value, AND/OR, IS NULL, IS NOT NULL, IN (...), LIKE
func isValidFilterExpression(filter string) bool {
	if filter == "" {
		return true
	}

	// Запрещаем multi-statement (;) — единственная реальная SQL injection защита
	if strings.Contains(filter, ";") {
		return false
	}

	// Запрещаем SQL комментарии
	if strings.Contains(filter, "--") || strings.Contains(filter, "/*") {
		return false
	}

	// Запрещаем ключевые слова DDL/DML
	if forbiddenSQLPattern.MatchString(filter) {
		return false
	}

	return true
}

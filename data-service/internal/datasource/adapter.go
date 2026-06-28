// Package datasource — generic абстракция над источниками данных клиента.
//
// В фазе 3.0 объявлен только интерфейс Adapter. Реализации (sqlite, postgres)
// появятся в фазе 3.1.
//
// Связь с internal/db:
//   - internal/db.DB — низкоуровневый интерфейс к database/sql (уже есть).
//   - internal/datasource.Adapter — высокоуровневый адаптер, который
//     инкапсулирует драйвер + интроспекцию схемы.
//
// В фазе 3.2 runtime-слой будет работать с Adapter, не с конкретным драйвером.
//
// Цикл импортов:
// Чтобы избежать цикла db → datasource → db (когда connector.go использует
// Registry), интерфейс DB определён здесь локально как Conn. db.DB остаётся
// как псевдоним для backward-compat с репозиториями (будут удалены в 3.3).
package datasource

import (
	"context"
	"database/sql"
)

// Conn — минимальный интерфейс, который Adapter ожидает от подключения.
// Совместим с *sql.DB и с обёртками (PostgresConn/SqliteConn).
//
// Контракт совпадает с internal/db.DB, но определён здесь, чтобы разорвать
// import cycle: db → datasource → db. В фазе 3.3 db.DB исчезнет вместе с
// репозиториями, и останется только этот тип.
type Conn interface {
	QueryRowContext(ctx context.Context, query string, args ...any) *sql.Row
	QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error)
	ExecContext(ctx context.Context, query string, args ...any) (sql.Result, error)
	PingContext(ctx context.Context) error
	Close() error
}

// Adapter — интерфейс адаптера источника данных.
//
// Каждый адаптер (sqlite, postgres, ...) реализует:
//  1. Driver() — идентификатор драйвера для конфига.
//  2. Connect() — открыть соединение по DSN.
//  3. Introspect() — прочитать метаданные схемы БД.
//  4. TranslatePlaceholder() — преобразовать generic '?' в нативный placeholder.
//  5. QuoteIdentifier() — корректно квотировать идентификатор в SQL.
type Adapter interface {
	// Driver возвращает стабильный идентификатор драйвера.
	// Используется в cfg.data_source.driver для выбора адаптера.
	Driver() string

	// Connect открывает соединение с БД по DSN.
	// Возвращает Conn, реализующий тот же контракт, что и db.DB.
	Connect(ctx context.Context, dsn string) (Conn, error)

	// Introspect читает метаданные схемы БД.
	// Возвращает generic-описание, одинаковое для всех СУБД:
	// таблицы, колонки (с generic-типами), FK, индексы.
	Introspect(ctx context.Context, database Conn) (*Schema, error)

	// TranslatePlaceholder преобразует порядковый номер placeholder'а
	// в нативный синтаксис СУБД.
	//   sqlite, mysql:    1 → "?"
	//   postgres:         1 → "$1", 2 → "$2", ...
	TranslatePlaceholder(index int) string

	// QuoteIdentifier квотирует имя таблицы/колонки для безопасного
	// использования в SQL.
	//   sqlite, mysql, postgres: "name" → "\"name\""
	//   mssql:                    "name" → "[name]"
	QuoteIdentifier(name string) string
}

// Schema — generic описание схемы БД, нормализованное поверх драйвера.
//
// Типы колонок приведены к generic-набору:
//   "string", "int", "float", "bool", "json", "datetime", "date"
//
// Имена таблиц/колонок сохраняются в их нативном регистре
// (snake_case для большинства СУБД).
type Schema struct {
	// Tables — список таблиц в схеме.
	Tables []Table `json:"tables"`

	// Driver — какой драйвер был источником этой Schema.
	// Полезно для логирования и диагностики.
	Driver string `json:"driver"`
}

// Table — описание одной таблицы.
type Table struct {
	// Name — имя таблицы (нативное, как в БД).
	Name string `json:"name"`

	// Columns — колонки в порядке их определения в таблице.
	Columns []Column `json:"columns"`

	// PrimaryKey — имена колонок, входящих в PRIMARY KEY.
	// Обычно содержит одну колонку.
	PrimaryKey []string `json:"primary_key"`

	// ForeignKeys — внешние ключи, определённые на этой таблице.
	ForeignKeys []ForeignKey `json:"foreign_keys"`
}

// Column — описание одной колонки.
type Column struct {
	// Name — имя колонки (нативное).
	Name string `json:"name"`

	// Type — generic-тип, приведённый адаптером к одному из:
	// "string", "int", "float", "bool", "json", "datetime", "date".
	Type string `json:"type"`

	// Nullable — допускает ли колонка NULL.
	Nullable bool `json:"nullable"`

	// Description — комментарий из БД (если СУБД поддерживает).
	// Для SQLite это обычно пусто.
	Description string `json:"description,omitempty"`
}

// ForeignKey — внешний ключ.
type ForeignKey struct {
	// Name — имя FK-ограничения (если СУБД поддерживает).
	Name string `json:"name,omitempty"`

	// Columns — колонки в текущей таблице.
	Columns []string `json:"columns"`

	// ReferencedTable — таблица, на которую ссылается FK.
	ReferencedTable string `json:"referenced_table"`

	// ReferencedColumns — колонки в ReferencedTable.
	ReferencedColumns []string `json:"referenced_columns"`
}

// GenericType — набор generic-типов, к которым адаптеры приводят
// нативные типы СУБД.
//
// Маппинг на нативные типы:
//
//	string   — TEXT, VARCHAR, CHAR, CLOB, ENUM (sqlite/postgres/mysql)
//	int      — INTEGER, INT, BIGINT, SMALLINT, SERIAL
//	float    — REAL, DOUBLE, NUMERIC, DECIMAL, FLOAT
//	bool     — BOOLEAN, BOOL, BIT (1)
//	json     — JSON, JSONB, TEXT (если помечен как JSON)
//	datetime — DATETIME, TIMESTAMP, TIMESTAMPTZ
//	date     — DATE
const (
	TypeString   = "string"
	TypeInt      = "int"
	TypeFloat    = "float"
	TypeBool     = "bool"
	TypeJSON     = "json"
	TypeDatetime = "datetime"
	TypeDate     = "date"
)

// Registry — реестр адаптеров по имени драйвера.
//
// Фаза 3.1: зарегистрировать "sqlite" и "postgres".
// Фаза 3.x: расширять реестр без правок существующего кода.
type Registry struct {
	adapters map[string]Adapter
}

// NewRegistry создаёт пустой реестр.
func NewRegistry() *Registry {
	return &Registry{adapters: make(map[string]Adapter)}
}

// Register регистрирует адаптер.
//
// Паника при дубликате — это programming error и должна быть поймана на старте.
func (r *Registry) Register(a Adapter) {
	if _, exists := r.adapters[a.Driver()]; exists {
		panic("datasource: adapter already registered for driver " + a.Driver())
	}
	r.adapters[a.Driver()] = a
}

// Get возвращает адаптер по имени драйвера.
// nil + false, если адаптер не зарегистрирован.
func (r *Registry) Get(driver string) (Adapter, bool) {
	a, ok := r.adapters[driver]
	return a, ok
}

// Drivers — список зарегистрированных драйверов.
// Используется в /admin/adapters для диагностики.
func (r *Registry) Drivers() []string {
	out := make([]string, 0, len(r.adapters))
	for d := range r.adapters {
		out = append(out, d)
	}
	return out
}

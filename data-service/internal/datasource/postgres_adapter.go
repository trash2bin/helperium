// Package datasource — реализация Adapter для PostgreSQL.
//
// PostgresAdapter инкапсулирует:
//   - открытие соединения по DSN через pgx/v5 stdlib;
//   - интроспекцию схемы через information_schema + pg_catalog;
//   - перевод generic placeholder '?' в нативный '$1', '$2', ...;
//   - квотирование идентификаторов через двойные кавычки (ANSI SQL).
//
// Связь с internal/db:
//   - internal/Conn — низкоуровневый интерфейс к database/sql.
//   - PostgresAdapter возвращает обёртку PostgresConn, реализующую Conn
//     через композицию над *sql.DB. Это позволяет драйверу datasource
//     оставаться независимым от internal/db.NewPostgres() (который живёт
//     в ветке 3.1.a и ещё не смержен).
//
// Что входит в Introspect:
//   - список BASE TABLE (без pg_catalog/information_schema, без VIEW);
//   - колонки с generic-маппингом типов (см. mapPostgresType);
//   - PRIMARY KEY через information_schema.table_constraints;
//   - FOREIGN KEY с группировкой по constraint_name;
//   - описание колонки через pg_catalog.col_description.
//
// View-таблицы сознательно исключены: контракт Adapter пока описывает
// только BASE TABLE (см. тип Table). Если позже понадобится — добавим
// отдельный флаг через переменные окружения или параметр.
package datasource

import (
	"context"
	"database/sql"
	"fmt"
	"strconv"
	"strings"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib" // database/sql-совместимый драйвер pgx
)

// PostgresAdapter — реализация Adapter для PostgreSQL (pgx/v5 stdlib).
type PostgresAdapter struct{}

// Driver возвращает идентификатор драйвера.
func (PostgresAdapter) Driver() string { return "postgres" }

// Connect открывает PostgreSQL-соединение по DSN.
//
// DSN принимается в одном из форматов:
//   - URL:     postgres://user:password@host:port/dbname?sslmode=disable
//   - Keyword: host=... user=... password=... dbname=... port=...
//
// Перед возвратом выполняется PingContext для проверки доступности
// (отлавливает неверные учётки, недоступный хост и пр.).
//
// Возвращает обёртку PostgresConn, реализующую Conn через композицию
// над *sql.DB — datasource-слой не зависит от internal/db.NewPostgres()
// и не подгружает его переменные окружения.
func (PostgresAdapter) Connect(ctx context.Context, dsn string) (Conn, error) {
	if dsn == "" {
		return nil, fmt.Errorf("postgres: empty DSN")
	}

	conn, err := sql.Open("pgx", dsn)
	if err != nil {
		return nil, fmt.Errorf("postgres: failed to open: %w", err)
	}

	// Разумные дефолты пула, согласованные с internal/db.NewPostgres().
	// Для pgx через stdlib применимы те же лимиты, что и для нативного pgxpool.
	conn.SetMaxOpenConns(25)
	conn.SetMaxIdleConns(5)
	conn.SetConnMaxLifetime(5 * time.Minute)

	if err := conn.PingContext(ctx); err != nil {
		_ = conn.Close()
		return nil, fmt.Errorf("postgres: ping failed: %w", err)
	}

	return &PostgresConn{conn: conn}, nil
}

// TranslatePlaceholder — Postgres нативно использует '$N' (1-based).
func (PostgresAdapter) TranslatePlaceholder(index int) string {
	return "$" + strconv.Itoa(index)
}

// QuoteIdentifier — двойные кавычки (ANSI SQL).
//
// Если в имени есть точка, квотируем каждый сегмент отдельно. Иначе
// Postgres считает всю строку одним identifier и имя таблицы
// "public.customers" становится буквальным именем, а не
// public.customers (schema.table). Квоты делаем для schema-qualified
// таблиц, а также для надёжности имён с пробелами / спецсимволами.
func (PostgresAdapter) QuoteIdentifier(name string) string {
	if strings.Contains(name, ".") {
		parts := strings.Split(name, ".")
		for i, p := range parts {
			parts[i] = `"` + p + `"`
		}
		return strings.Join(parts, ".")
	}
	return `"` + name + `"`
}

// Introspect читает метаданные схемы через information_schema + pg_catalog.
//
// Алгоритм:
//  1. Список BASE TABLE из information_schema.tables
//     (исключая pg_catalog и information_schema).
//  2. Для каждой таблицы: колонки из information_schema.columns.
//  3. Для каждой таблицы: PK из information_schema.table_constraints +
//     key_column_usage (надёжнее, чем regclass-каст в pg_index).
//  4. Для каждой таблицы: FK из table_constraints + key_column_usage +
//     constraint_column_usage, сгруппированные по constraint_name.
//  5. Для каждой колонки: описание через pg_catalog.col_description.
//
// Имена таблиц из information_schema.tables — доверенные (приходят из самой
// БД). Тем не менее, при составлении имён вида "schema.table" используется
// разделение через точку без квотирования: имя схемы и таблицы валидируются
// на пустоту и краткость.
//
// Description может быть пустым — это нормально, если COMMENT ON COLUMN
// не выполнялся (см. контракт Column.Description: omitempty).
func (PostgresAdapter) Introspect(ctx context.Context, database Conn) (*Schema, error) {
	const listSQL = `
		SELECT table_schema, table_name
		FROM information_schema.tables
		WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
		  AND table_type = 'BASE TABLE'
		ORDER BY table_schema, table_name
	`

	// Шаг 1: собираем список таблиц в слайс до закрытия rows, чтобы
	// SetMaxOpenConns не блокировал последующие запросы к тому же пулу.
	rows, err := database.QueryContext(ctx, listSQL)
	if err != nil {
		return nil, fmt.Errorf("postgres: list information_schema.tables failed: %w", err)
	}

	type tableRef struct {
		schema string
		name   string
	}
	var tableRefs []tableRef
	for rows.Next() {
		var schema, name string
		if err := rows.Scan(&schema, &name); err != nil {
			rows.Close()
			return nil, fmt.Errorf("postgres: scan information_schema.tables row: %w", err)
		}
		tableRefs = append(tableRefs, tableRef{schema: schema, name: name})
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return nil, fmt.Errorf("postgres: iterate information_schema.tables: %w", err)
	}
	if err := rows.Close(); err != nil {
		return nil, fmt.Errorf("postgres: close information_schema.tables rows: %w", err)
	}

	// Шаг 2–5: для каждой таблицы читаем колонки, PK, FK и описания.
	schema := &Schema{Driver: "postgres"}
	for _, ref := range tableRefs {
		tbl, err := introspectPostgresTable(ctx, database, ref.schema, ref.name)
		if err != nil {
			return nil, fmt.Errorf("postgres: introspect table %q.%q: %w", ref.schema, ref.name, err)
		}
		schema.Tables = append(schema.Tables, tbl)
	}

	return schema, nil
}

// introspectPostgresTable читает колонки, PK, FK и описания одной таблицы.
//
// Имя таблицы отражается в Table.Name как "schema.table" — это позволяет
// не терять информацию о схеме при матчинге в тестах и runtime-слое
// (см. contract: имя нативное, как в БД).
func introspectPostgresTable(ctx context.Context, database Conn, schemaName, tableName string) (Table, error) {
	tbl := Table{Name: schemaName + "." + tableName}

	// Шаг 2: колонки.
	const colsSQL = `
		SELECT column_name, data_type, is_nullable
		FROM information_schema.columns
		WHERE table_schema = $1 AND table_name = $2
		ORDER BY ordinal_position
	`
	colRows, err := database.QueryContext(ctx, colsSQL, schemaName, tableName)
	if err != nil {
		return tbl, fmt.Errorf("columns: %w", err)
	}

	columns := make([]Column, 0)
	columnNames := make([]string, 0)
	for colRows.Next() {
		var cname, dtype, nullable string
		if err := colRows.Scan(&cname, &dtype, &nullable); err != nil {
			colRows.Close()
			return tbl, fmt.Errorf("scan columns: %w", err)
		}
		columns = append(columns, Column{
			Name:     cname,
			Type:     mapPostgresType(dtype),
			Nullable: strings.EqualFold(nullable, "YES"),
			// Description заполняется отдельным запросом ниже — здесь оставляем пустым.
		})
		columnNames = append(columnNames, cname)
	}
	if err := colRows.Err(); err != nil {
		colRows.Close()
		return tbl, fmt.Errorf("iterate columns: %w", err)
	}
	if err := colRows.Close(); err != nil {
		return tbl, fmt.Errorf("close columns rows: %w", err)
	}

	// Шаг 5: description через pg_catalog.col_description.
	// В pg_description хранится комментарий (из COMMENT ON COLUMN ... IS '...').
	// Выполняем один запрос на таблицу, чтобы минимизировать round-trips.
	if err := fillColumnDescriptions(ctx, database, schemaName, tableName, columns); err != nil {
		// Описание — best-effort. Если запрос упал (например, нет прав на pg_catalog),
		// не валим всю интроспекцию — оставляем Description пустым.
		// Ошибка логируется в обёртке, но не пробрасывается дальше.
		_ = err
	}

	tbl.Columns = columns

	// Шаг 3: PRIMARY KEY.
	pk, err := queryPostgresPrimaryKey(ctx, database, schemaName, tableName)
	if err != nil {
		return tbl, fmt.Errorf("primary key: %w", err)
	}
	tbl.PrimaryKey = pk

	// Шаг 4: FOREIGN KEYS.
	fks, err := queryPostgresForeignKeys(ctx, database, schemaName, tableName)
	if err != nil {
		return tbl, fmt.Errorf("foreign keys: %w", err)
	}
	tbl.ForeignKeys = fks

	return tbl, nil
}

// queryPostgresPrimaryKey возвращает имена колонок PRIMARY KEY в порядке
// их определения (ordinal_position).
//
// Используем information_schema.table_constraints, а не pg_index с regclass,
// потому что information_schema стабильнее для типизированного доступа
// и не требует привилегий на pg_catalog.
func queryPostgresPrimaryKey(ctx context.Context, database Conn, schemaName, tableName string) ([]string, error) {
	const q = `
		SELECT k.column_name
		FROM information_schema.table_constraints t
		JOIN information_schema.key_column_usage k
		  ON t.constraint_schema = k.constraint_schema
		 AND t.constraint_name   = k.constraint_name
		WHERE t.constraint_schema = $1
		  AND t.table_name        = $2
		  AND t.constraint_type   = 'PRIMARY KEY'
		ORDER BY k.ordinal_position
	`
	rows, err := database.QueryContext(ctx, q, schemaName, tableName)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	out := make([]string, 0)
	for rows.Next() {
		var col string
		if err := rows.Scan(&col); err != nil {
			return nil, fmt.Errorf("scan primary key: %w", err)
		}
		out = append(out, col)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate primary key: %w", err)
	}
	return out, nil
}

// queryPostgresForeignKeys возвращает FK-ограничения таблицы,
// сгруппированные по constraint_name. Один constraint — один ForeignKey.
//
// Если таблица ссылается на колонку в другой схеме, ReferencedTable
// сохраняется в формате "schema.table" — это согласуется с форматом
// Table.Name и упрощает матчинг в runtime.
func queryPostgresForeignKeys(ctx context.Context, database Conn, schemaName, tableName string) ([]ForeignKey, error) {
	const q = `
		SELECT tc.constraint_name,
		       kcu.column_name,
		       ccu.table_schema   AS foreign_table_schema,
		       ccu.table_name     AS foreign_table,
		       ccu.column_name    AS foreign_column,
		       kcu.ordinal_position
		FROM information_schema.table_constraints tc
		JOIN information_schema.key_column_usage kcu
		  ON tc.constraint_schema = kcu.constraint_schema
		 AND tc.constraint_name   = kcu.constraint_name
		JOIN information_schema.constraint_column_usage ccu
		  ON tc.constraint_schema = ccu.constraint_schema
		 AND tc.constraint_name   = ccu.constraint_name
		WHERE tc.constraint_schema = $1
		  AND tc.table_name        = $2
		  AND tc.constraint_type   = 'FOREIGN KEY'
		ORDER BY tc.constraint_name, kcu.ordinal_position
	`
	rows, err := database.QueryContext(ctx, q, schemaName, tableName)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	// Группируем строки по constraint_name. ordinal_position задаёт
	// позицию в составе композитного ключа — но для текущей задачи
	// (одна колонка на FK) сортировка по ordinal_position достаточна,
	// и композитные FK корректно упорядочатся по тому же полю.
	type fkRow struct {
		constraintName string
		column         string
		refSchema      string
		refTable       string
		refColumn      string
	}
	var collected []fkRow
	for rows.Next() {
		var (
			cname, col, refSchema, refTable, refCol string
			ord                                     int
		)
		if err := rows.Scan(&cname, &col, &refSchema, &refTable, &refCol, &ord); err != nil {
			return nil, fmt.Errorf("scan foreign key: %w", err)
		}
		collected = append(collected, fkRow{
			constraintName: cname,
			column:         col,
			refSchema:      refSchema,
			refTable:       refTable,
			refColumn:      refCol,
		})
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate foreign keys: %w", err)
	}

	// Группируем по constraint_name, сохраняя порядок первого появления.
	groupOrder := make([]string, 0)
	groups := make(map[string]*struct {
		referencedTable string
		columns         []string
		referencedCols  []string
	})

	for _, r := range collected {
		g, exists := groups[r.constraintName]
		if !exists {
			g = &struct {
				referencedTable string
				columns         []string
				referencedCols  []string
			}{referencedTable: r.refSchema + "." + r.refTable}
			groups[r.constraintName] = g
			groupOrder = append(groupOrder, r.constraintName)
		}
		g.columns = append(g.columns, r.column)
		g.referencedCols = append(g.referencedCols, r.refColumn)
	}

	out := make([]ForeignKey, 0, len(groupOrder))
	for _, name := range groupOrder {
		g := groups[name]
		out = append(out, ForeignKey{
			Name:              name,
			Columns:           g.columns,
			ReferencedTable:   g.referencedTable,
			ReferencedColumns: g.referencedCols,
		})
	}
	return out, nil
}

// fillColumnDescriptions заполняет Column.Description из pg_catalog.col_description.
//
// pg_description.col_description(oid, attnum) возвращает text или NULL
// (если комментария нет). NULL → оставляем Description пустым (omitempty
// в JSON уберёт поле целиком).
//
// На таблице: SELECT oid FROM pg_catalog.pg_class WHERE relname = $1
// обычно нестрогий по схеме (может вернуть oid таблицы из другой схемы,
// если имена совпадают). Поэтому фильтруем дополнительно по schema:
//
//	JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = $2.
func fillColumnDescriptions(ctx context.Context, database Conn, schemaName, tableName string, columns []Column) error {
	if len(columns) == 0 {
		return nil
	}

	const q = `
		SELECT a.attname, pg_catalog.col_description(c.oid, a.attnum)
		FROM pg_catalog.pg_class c
		JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid
		JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
		WHERE c.relname = $1 AND n.nspname = $2 AND a.attnum > 0
	`
	rows, err := database.QueryContext(ctx, q, tableName, schemaName)
	if err != nil {
		return fmt.Errorf("col_description: %w", err)
	}
	defer rows.Close()

	// Индексируем по имени колонки.
	descs := make(map[string]string, len(columns))
	for rows.Next() {
		var name string
		var desc sql.NullString
		if err := rows.Scan(&name, &desc); err != nil {
			return fmt.Errorf("scan col_description: %w", err)
		}
		if desc.Valid {
			descs[name] = desc.String
		}
	}
	if err := rows.Err(); err != nil {
		return fmt.Errorf("iterate col_description: %w", err)
	}

	for i := range columns {
		if d, ok := descs[columns[i].Name]; ok {
			columns[i].Description = d
		}
	}
	return nil
}

// mapPostgresType приводит нативный тип Postgres (data_type из
// information_schema.columns) к одному из generic-типов из adapter.go
// (TypeString / TypeInt / TypeFloat / TypeBool / TypeJSON / TypeDatetime / TypeDate).
//
// Источник истины: Postgres documentation, раздел "Data Types".
// data_type в information_schema нормализован в нижний регистр и
// в пробельные варианты (например, "character varying",
// "timestamp without time zone").
//
// Узкие типы (bool/json/datetime/date) проверяются раньше широких,
// чтобы их подстроки не ловились правилами для VARCHAR/TEXT.
func mapPostgresType(native string) string {
	t := strings.ToLower(strings.TrimSpace(native))

	// --- Узкие типы (приоритет выше, чтобы не ловились широкими правилами). ---

	// Bool.
	if t == "boolean" || t == "bool" {
		return TypeBool
	}

	// JSON / JSONB.
	if t == "json" || t == "jsonb" {
		return TypeJSON
	}

	// Datetime.
	switch t {
	case "timestamp without time zone",
		"timestamp with time zone",
		"timestamptz",
		"timestamp":
		return TypeDatetime
	}

	// Date.
	if t == "date" {
		return TypeDate
	}

	// --- Числовые целые. ---
	switch t {
	case "bigint", "integer", "smallint",
		"int", "int2", "int4", "int8",
		"serial", "bigserial", "smallserial":
		return TypeInt
	}

	// --- Числовые дробные. ---
	switch t {
	case "numeric", "decimal", "real",
		"double precision", "float4", "float8", "money":
		return TypeFloat
	}

	// --- Строковые. ---
	switch t {
	case "character varying", "character",
		"text", "char", "varchar", "bpchar",
		"name", "citext":
		return TypeString
	}

	// --- Специальные случаи с fallback. ---

	// bytea — бинарные данные. В контексте data-service пока трактуется
	// как JSON-сериализация (аналогично BLOB в sqlite-адаптере): если
	// в схеме появится bytea, это скорее всего означает "храним сериализованный
	// объект". Возвращаем TypeJSON, чтобы runtime-слой обработал его как
	// структурированные данные, а не как строку.
	if t == "bytea" {
		return TypeJSON
	}

	// --- Fallback для неизвестных типов. ---
	// Если появится новый тип (uuid, inet, cidr, xml, массивы, ...),
	// безопаснее вернуть "string" и пометить это в коде, чем упасть.
	// Маппинг можно расширить по мере необходимости.
	return TypeString
}

// PostgresConn — обёртка над *sql.DB, реализующая интерфейс Conn
// через композицию. Не дублирует логику internal/db.NewPostgres() и
// не зависит от переменных окружения.
//
// Используется PostgresAdapter.Connect для возврата Conn.
type PostgresConn struct {
	conn *sql.DB
}

func (p *PostgresConn) QueryRowContext(ctx context.Context, query string, args ...any) *sql.Row {
	return p.conn.QueryRowContext(ctx, query, args...)
}

func (p *PostgresConn) QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error) {
	return p.conn.QueryContext(ctx, query, args...)
}

func (p *PostgresConn) ExecContext(ctx context.Context, query string, args ...any) (sql.Result, error) {
	return p.conn.ExecContext(ctx, query, args...)
}

func (p *PostgresConn) PingContext(ctx context.Context) error {
	return p.conn.PingContext(ctx)
}

func (p *PostgresConn) Close() error {
	return p.conn.Close()
}

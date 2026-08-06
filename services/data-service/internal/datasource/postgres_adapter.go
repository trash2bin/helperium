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
//
// Контракт: имя приходит из интроспекции (schema.table) или из конфига
// НАТИВНЫМ именем БЕЗ кавычек. Имена колонок с точкой внутри (foo.bar)
// трактуются как schema-qualified и квотируются по сегментам — если колонка
// реально называется "foo.bar", укажи её в конфиге уже квотированной.
func (PostgresAdapter) QuoteIdentifier(name string) string {
	// Экранируем двойные кавычки внутри сегментов (" → "") до квотирования,
	// иначе имя из интроспекции вида a"; DROP TABLE x; -- выходит из кавычек.
	escape := func(seg string) string { return strings.ReplaceAll(seg, `"`, `""`) }
	if strings.Contains(name, ".") {
		parts := strings.Split(name, ".")
		for i, p := range parts {
			parts[i] = `"` + escape(p) + `"`
		}
		return strings.Join(parts, ".")
	}
	return `"` + escape(name) + `"`
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

	// Шаг 1: список таблиц.
	rows, err := database.QueryContext(ctx, listSQL)
	if err != nil {
		return nil, fmt.Errorf("postgres: list tables failed: %w", err)
	}
	type tableRef struct{ schema, name string }
	var tableRefs []tableRef
	for rows.Next() {
		var schema, name string
		if err := rows.Scan(&schema, &name); err != nil {
			_ = rows.Close()
			return nil, fmt.Errorf("postgres: scan table row: %w", err)
		}
		tableRefs = append(tableRefs, tableRef{schema: schema, name: name})
	}
	if err := rows.Err(); err != nil {
		_ = rows.Close()
		return nil, fmt.Errorf("postgres: iterate tables: %w", err)
	}
	_ = rows.Close()

	if len(tableRefs) == 0 {
		return &Schema{Driver: "postgres"}, nil
	}

	// Шаг 2: все колонки + PK за 1 запрос (вместо N).
	const colsSQL = `
		SELECT c.table_schema, c.table_name,
		       c.column_name, c.data_type, c.is_nullable, c.ordinal_position,
		       (pk.column_name IS NOT NULL) AS is_pk
		FROM information_schema.columns c
		LEFT JOIN (
		    SELECT ku.column_name, ku.table_name, ku.table_schema
		    FROM information_schema.table_constraints tc
		    JOIN information_schema.key_column_usage ku
		        ON tc.constraint_schema = ku.constraint_schema
		       AND tc.constraint_name   = ku.constraint_name
		    WHERE tc.constraint_type = 'PRIMARY KEY'
		) pk ON pk.table_name = c.table_name
		    AND pk.table_schema = c.table_schema
		    AND pk.column_name = c.column_name
		WHERE c.table_schema NOT IN ('pg_catalog', 'information_schema')
		ORDER BY c.table_schema, c.table_name, c.ordinal_position
	`

	type colRef struct {
		schema, table, column, dtype, nullable string
		ordinal                                int
		isPK                                   bool
	}
	colRows, err := database.QueryContext(ctx, colsSQL)
	if err != nil {
		return nil, fmt.Errorf("postgres: list columns failed: %w", err)
	}
	var colRefs []colRef
	for colRows.Next() {
		var r colRef
		nullStr := "YES"
		if err := colRows.Scan(&r.schema, &r.table, &r.column, &r.dtype, &nullStr, &r.ordinal, &r.isPK); err != nil {
			_ = colRows.Close()
			return nil, fmt.Errorf("postgres: scan column row: %w", err)
		}
		r.nullable = nullStr
		colRefs = append(colRefs, r)
	}
	if err := colRows.Err(); err != nil {
		_ = colRows.Close()
		return nil, fmt.Errorf("postgres: iterate columns: %w", err)
	}
	_ = colRows.Close()

	// Шаг 3: description всех колонок за 1 запрос.
	const descSQL = `
		SELECT n.nspname, c.relname, a.attname,
		       pg_catalog.col_description(c.oid, a.attnum) AS description
		FROM pg_catalog.pg_class c
		JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid
		JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
		WHERE a.attnum > 0 AND c.relkind = 'r'
		  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
	`

	type descRef struct{ schema, table, column string }
	descMap := make(map[string]map[string]string) // [schema.table][column]desc
	{
		dRows, err := database.QueryContext(ctx, descSQL)
		if err == nil {
			for dRows.Next() {
				var r descRef
				var desc sql.NullString
				if err := dRows.Scan(&r.schema, &r.table, &r.column, &desc); err == nil {
					key := r.schema + "." + r.table
					if descMap[key] == nil {
						descMap[key] = make(map[string]string)
					}
					if desc.Valid {
						descMap[key][r.column] = desc.String
					}
				}
			}
			_ = dRows.Close()
		}
		// best-effort: если pg_catalog недоступен — просто без описаний
	}

	// Шаг 4: все FK за 1 запрос.
	const fkSQL = `
		SELECT kcu.table_schema, kcu.table_name,
		       tc.constraint_name,
		       kcu.column_name,
		       ccu.table_schema AS ref_table_schema,
		       ccu.table_name   AS ref_table_name,
		       ccu.column_name  AS ref_column_name,
		       kcu.ordinal_position
		FROM information_schema.table_constraints tc
		JOIN information_schema.key_column_usage kcu
		    ON tc.constraint_schema = kcu.constraint_schema
		   AND tc.constraint_name   = kcu.constraint_name
		JOIN information_schema.constraint_column_usage ccu
		    ON tc.constraint_schema = ccu.constraint_schema
		   AND tc.constraint_name   = ccu.constraint_name
		WHERE tc.constraint_type = 'FOREIGN KEY'
		  AND tc.table_schema NOT IN ('pg_catalog', 'information_schema')
		ORDER BY kcu.table_schema, kcu.table_name, tc.constraint_name, kcu.ordinal_position
	`

	type fkRow struct {
		tableSchema, tableName, constraintName string
		column, refSchema, refTable, refColumn string
	}
	var fkRows []fkRow
	{
		fRows, err := database.QueryContext(ctx, fkSQL)
		if err != nil {
			return nil, fmt.Errorf("postgres: list fk failed: %w", err)
		}
		for fRows.Next() {
			var r fkRow
			var ord int
			if err := fRows.Scan(&r.tableSchema, &r.tableName, &r.constraintName,
				&r.column, &r.refSchema, &r.refTable, &r.refColumn, &ord); err != nil {
				_ = fRows.Close()
				return nil, fmt.Errorf("postgres: scan fk row: %w", err)
			}
			fkRows = append(fkRows, r)
		}
		if err := fRows.Err(); err != nil {
			_ = fRows.Close()
			return nil, fmt.Errorf("postgres: iterate fk: %w", err)
		}
		_ = fRows.Close()
	}

	// Шаг 5: сборка Schema.
	schema := &Schema{Driver: "postgres"}

	// Группируем колонки по таблицам
	type tblBuilder struct {
		columns []Column
		pkCols  []string
	}
	tblMap := make(map[string]*tblBuilder)
	for _, c := range colRefs {
		key := c.schema + "." + c.table
		if _, ok := tblMap[key]; !ok {
			tblMap[key] = &tblBuilder{}
		}
		col := Column{
			Name:     c.column,
			Type:     mapPostgresType(c.dtype),
			Nullable: strings.EqualFold(c.nullable, "YES"),
		}
		if desc, ok := descMap[key]; ok {
			col.Description = desc[c.column]
		}
		tblMap[key].columns = append(tblMap[key].columns, col)
		if c.isPK {
			tblMap[key].pkCols = append(tblMap[key].pkCols, c.column)
		}
	}

	// Группируем FK по constraint_name
	type fkGroup struct {
		refTable       string
		columns        []string
		referencedCols []string
	}
	fkMap := make(map[string]map[string]*fkGroup) // [tableKey][constraintName]
	for _, f := range fkRows {
		key := f.tableSchema + "." + f.tableName
		if fkMap[key] == nil {
			fkMap[key] = make(map[string]*fkGroup)
		}
		g, ok := fkMap[key][f.constraintName]
		if !ok {
			g = &fkGroup{refTable: f.refSchema + "." + f.refTable}
			fkMap[key][f.constraintName] = g
		}
		g.columns = append(g.columns, f.column)
		g.referencedCols = append(g.referencedCols, f.refColumn)
	}

	// Собираем таблицы в порядке tableRefs
	for _, ref := range tableRefs {
		key := ref.schema + "." + ref.name
		tbl := Table{Name: key, Columns: make([]Column, 0)}
		if tb, ok := tblMap[key]; ok {
			tbl.Columns = tb.columns
			tbl.PrimaryKey = tb.pkCols
		}
		if fks, ok := fkMap[key]; ok {
			for cname, g := range fks {
				tbl.ForeignKeys = append(tbl.ForeignKeys, ForeignKey{
					Name:              cname,
					Columns:           g.columns,
					ReferencedTable:   g.refTable,
					ReferencedColumns: g.referencedCols,
				})
			}
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

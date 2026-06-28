// Package datasource — реализация Adapter для SQLite.
//
// SqliteAdapter инкапсулирует:
//   - открытие соединения по DSN (путь к файлу SQLite);
//   - интроспекцию схемы через sqlite_master + PRAGMA table_info/foreign_key_list;
//   - перевод generic placeholder '?' в нативный '?';
//   - квотирование идентификаторов через двойные кавычки.
//
// Связь с internal/db:
//   - internal/Conn — низкоуровневый интерфейс к database/sql.
//   - SqliteAdapter возвращает обёртку SqliteConn, реализующую Conn
//     через композицию над *sql.DB. Это позволяет драйверу datasource
//     оставаться независимым от NewSQLite() и его env-логики.
package datasource

import (
	"context"
	"database/sql"
	"fmt"
	"strings"

	_ "modernc.org/sqlite" // pure-Go SQLite driver
)

// SqliteAdapter — реализация Adapter для SQLite (modernc.org/sqlite).
type SqliteAdapter struct{}

// Driver возвращает идентификатор драйвера.
func (SqliteAdapter) Driver() string { return "sqlite" }

// Connect открывает SQLite-файл по dsn (трактуется как путь к файлу).
//
// DSN-формат: путь к файлу. Если в DSN уже есть '?', параметры
// (например _journal_mode=WAL&_foreign_keys=on) сохраняются as-is.
//
// Если путь — ":memory:", открывается in-memory БД (для тестов).
//
// Возвращает обёртку SqliteConn, реализующую Conn через композицию
// над *sql.DB — чтобы datasource-слой не зависел от internal/db.NewSQLite()
// и его переменных окружения.
func (SqliteAdapter) Connect(ctx context.Context, dsn string) (Conn, error) {
	if dsn == "" {
		return nil, fmt.Errorf("sqlite: empty DSN")
	}

	openDSN := dsn
	if !strings.Contains(dsn, "?") {
		// Дефолтные прагмы для боевого и тестового использования.
		openDSN = dsn + "?_journal_mode=WAL&_foreign_keys=on"
	}

	conn, err := sql.Open("sqlite", openDSN)
	if err != nil {
		return nil, fmt.Errorf("sqlite: failed to open %q: %w", dsn, err)
	}

	// Для SQLite актуален один writer; ограничиваем пул, чтобы избежать
	// "database is locked" в многопоточных тестах.
	conn.SetMaxOpenConns(1)

	if err := conn.PingContext(ctx); err != nil {
		_ = conn.Close()
		return nil, fmt.Errorf("sqlite: ping failed for %q: %w", dsn, err)
	}

	return &SqliteConn{conn: conn}, nil
}

// TranslatePlaceholder — SQLite нативно использует '?'.
func (SqliteAdapter) TranslatePlaceholder(index int) string { return "?" }

// QuoteIdentifier — двойные кавычки (ANSI SQL).
func (SqliteAdapter) QuoteIdentifier(name string) string { return `"` + name + `"` }

// Introspect читает метаданные схемы через sqlite_master + PRAGMA.
//
// Алгоритм:
//  1. SELECT type, name FROM sqlite_master WHERE type IN ('table','view')
//     AND name NOT LIKE 'sqlite_%' — список таблиц и view.
//  2. Для каждой таблицы: PRAGMA table_info(<table>) — колонки.
//  3. Для каждой таблицы: PRAGMA foreign_key_list(<table>) — FK.
//
// PRAGMA в SQLite не поддерживают плейсхолдеры для имени таблицы,
// поэтому идентификатор безопасно подставляется через QuoteIdentifier.
//
// SQLite не хранит комментарии к колонкам, поэтому Description всегда пуст.
func (SqliteAdapter) Introspect(ctx context.Context, database Conn) (*Schema, error) {
	const listSQL = `
		SELECT type, name
		FROM sqlite_master
		WHERE type IN ('table', 'view')
		  AND name NOT LIKE 'sqlite_%'
		ORDER BY name
	`
	// Шаг 1: собираем список таблиц в слайс, не удерживая *sql.Rows открытым.
	// С SetMaxOpenConns(1) (single-writer SQLite) удержание rows блокирует
	// любой следующий запрос к этой же БД.
	rows, err := database.QueryContext(ctx, listSQL)
	if err != nil {
		return nil, fmt.Errorf("sqlite: list sqlite_master failed: %w", err)
	}

	type tableRef struct {
		kind string
		name string
	}
	var tableRefs []tableRef
	for rows.Next() {
		var kind, name string
		if err := rows.Scan(&kind, &name); err != nil {
			rows.Close()
			return nil, fmt.Errorf("sqlite: scan sqlite_master row: %w", err)
		}
		tableRefs = append(tableRefs, tableRef{kind: kind, name: name})
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return nil, fmt.Errorf("sqlite: iterate sqlite_master: %w", err)
	}
	if err := rows.Close(); err != nil {
		return nil, fmt.Errorf("sqlite: close sqlite_master rows: %w", err)
	}

	// Шаг 2: для каждой таблицы выполняем PRAGMA (можно в той же транзакции).
	schema := &Schema{Driver: "sqlite"}
	for _, ref := range tableRefs {
		table, err := introspectTable(ctx, database, ref.name)
		if err != nil {
			return nil, fmt.Errorf("sqlite: introspect table %q: %w", ref.name, err)
		}
		schema.Tables = append(schema.Tables, table)
	}

	return schema, nil
}

// introspectTable читает колонки, PK и FK одной таблицы.
//
// PRAGMA нельзя параметризовать — имя таблицы подставляется через
// QuoteIdentifier. Так как имена приходят из sqlite_master, они
// доверенные, но квотирование всё равно обязательно для имён с
// пробелами или спецсимволами.
func introspectTable(ctx context.Context, database Conn, name string) (Table, error) {
	quoted := SqliteAdapter{}.QuoteIdentifier(name)
	tbl := Table{Name: name}

	// PRAGMA table_info: cid, name, type, notnull, dflt_value, pk.
	colRows, err := database.QueryContext(ctx, "PRAGMA table_info("+quoted+")")
	if err != nil {
		return tbl, fmt.Errorf("table_info: %w", err)
	}

	primaryKey := make([]string, 0)
	for colRows.Next() {
		var cid int
		var cname, ctype string
		var notnull int
		var dflt sql.NullString
		var pk int

		if err := colRows.Scan(&cid, &cname, &ctype, &notnull, &dflt, &pk); err != nil {
			colRows.Close()
			return tbl, fmt.Errorf("scan table_info: %w", err)
		}

		tbl.Columns = append(tbl.Columns, Column{
			Name:        cname,
			Type:        mapSQLiteType(ctype),
			Nullable:    notnull == 0,
			Description: "", // SQLite не хранит комментарии к колонкам
		})

		// pk — порядковый номер в составе PRIMARY KEY (1, 2, ...).
		// Если pk > 0 — колонка входит в PK.
		if pk > 0 {
			primaryKey = append(primaryKey, cname)
		}
	}
	if err := colRows.Err(); err != nil {
		colRows.Close()
		return tbl, fmt.Errorf("iterate table_info: %w", err)
	}
	colRows.Close()
	tbl.PrimaryKey = primaryKey

	// PRAGMA foreign_key_list: id, seq, table, from, to, on_update, on_delete, match.
	// Строки группируются по id: каждая группа — один FK-constraint,
	// строки внутри упорядочены по seq и формируют композитный ключ.
	fkRows, err := database.QueryContext(ctx, "PRAGMA foreign_key_list("+quoted+")")
	if err != nil {
		return tbl, fmt.Errorf("foreign_key_list: %w", err)
	}
	defer fkRows.Close()

	type fkGroup struct {
		referencedTable string
		columns         []string
		referencedCols  []string
	}
	groups := make(map[int]*fkGroup)
	order := make([]int, 0)

	for fkRows.Next() {
		var id, seq int
		var table, from, to string
		var onUpdate, onDelete, match sql.NullString

		if err := fkRows.Scan(&id, &seq, &table, &from, &to, &onUpdate, &onDelete, &match); err != nil {
			return tbl, fmt.Errorf("scan foreign_key_list: %w", err)
		}

		g, exists := groups[id]
		if !exists {
			g = &fkGroup{referencedTable: table}
			groups[id] = g
			order = append(order, id)
		}
		g.columns = append(g.columns, from)
		g.referencedCols = append(g.referencedCols, to)
	}
	if err := fkRows.Err(); err != nil {
		return tbl, fmt.Errorf("iterate foreign_key_list: %w", err)
	}

	for _, id := range order {
		g := groups[id]
		tbl.ForeignKeys = append(tbl.ForeignKeys, ForeignKey{
			Name:              fmt.Sprintf("fk_%s_%d", name, id),
			Columns:           g.columns,
			ReferencedTable:   g.referencedTable,
			ReferencedColumns: g.referencedCols,
		})
	}

	return tbl, nil
}

// mapSQLiteType приводит нативный тип SQLite к одному из generic-типов.
//
// Маппинг определён в adapter.go (TypeString/Int/Float/Bool/JSON/Datetime/Date).
//
// Приоритет: bool/json/datetime/date — узкие, проверяются первыми
// (благодаря этому "VARCHAR" с лексемой "DATE" не превратится в TypeDate).
func mapSQLiteType(native string) string {
	t := strings.ToUpper(strings.TrimSpace(native))

	// Узкие типы — bool/json/datetime/date — проверяются первыми,
	// чтобы их подстроки не ловились широкими правилами.
	switch t {
	case "BOOLEAN", "BOOL":
		return TypeBool
	case "JSON", "JSONB":
		return TypeJSON
	case "DATETIME", "TIMESTAMP", "TIMESTAMPTZ":
		return TypeDatetime
	case "DATE":
		return TypeDate
	}

	// Числовые.
	switch t {
	case "INTEGER", "INT", "INT2", "INT8", "BIGINT", "SMALLINT", "MEDIUMINT":
		return TypeInt
	case "REAL", "DOUBLE", "DOUBLE PRECISION", "FLOAT", "NUMERIC", "DECIMAL":
		return TypeFloat
	}

	// BLOB трактуем как json: содержимое бинарное, но в data-service
	// (пока) это означает JSON-сериализацию (см. lessons_json, metadata_json).
	if t == "BLOB" {
		return TypeJSON
	}

	// Текстовые — дефолт для TEXT/VARCHAR/CLOB/CHARACTER и всего,
	// что не распознано явно.
	if t == "TEXT" || strings.HasPrefix(t, "VARCHAR") ||
		strings.HasPrefix(t, "CHARACTER") || t == "CHAR" || t == "CLOB" {
		return TypeString
	}

	return TypeString
}

// SqliteConn — обёртка над *sql.DB, реализующая интерфейс Conn
// через композицию. Не дублирует логику internal/db.NewSQLite() и
// не зависит от переменных окружения.
//
// Используется SqliteAdapter.Connect для возврата Conn.
type SqliteConn struct {
	conn *sql.DB
}

func (s *SqliteConn) QueryRowContext(ctx context.Context, query string, args ...any) *sql.Row {
	return s.conn.QueryRowContext(ctx, query, args...)
}

func (s *SqliteConn) QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error) {
	return s.conn.QueryContext(ctx, query, args...)
}

func (s *SqliteConn) ExecContext(ctx context.Context, query string, args ...any) (sql.Result, error) {
	return s.conn.ExecContext(ctx, query, args...)
}

func (s *SqliteConn) PingContext(ctx context.Context) error {
	return s.conn.PingContext(ctx)
}

func (s *SqliteConn) Close() error {
	return s.conn.Close()
}

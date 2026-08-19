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
	"database/sql/driver"
	"fmt"
	"log/slog"
	"regexp"
	"strings"
	"sync"

	sqlite "modernc.org/sqlite" // pure-Go SQLite driver
)

// regexpOnce гарантирует однократную регистрацию SQL-функции regexp() для
// драйвера modernc.org/sqlite. Без неё grep strategy с regex=true падает
// на живой БД: "no such function: REGEXP" (SQLite не включает regexp()
// по умолчанию, в отличие от PostgreSQL).
var regexpOnce sync.Once

// registerSQLiteRegexp регистрирует скалярную функцию regexp(pattern, value),
// семантически эквивалентную PG regexp-оператору. Обёртка поверх regexp.MatchString
// с ReDoS-защитой: длина pattern уже ограничена maxRegexLen в grep strategy
// (200 символов), но дополнительно лимитируем глубину/время через регулярное
// выражение с модификатором (?i) при необходимости — здесь держим простой путь.
func registerSQLiteRegexp() {
	regexpOnce.Do(func() {
		err := sqlite.RegisterScalarFunction("regexp", 2, func(_ *sqlite.FunctionContext, args []driver.Value) (driver.Value, error) {
			if len(args) != 2 {
				return nil, fmt.Errorf("regexp: expected 2 args, got %d", len(args))
			}
			pattern, ok := args[0].(string)
			if !ok {
				return nil, fmt.Errorf("regexp: pattern must be string")
			}
			value, ok := args[1].(string)
			if !ok {
				return nil, fmt.Errorf("regexp: value must be string")
			}
			matched, err := regexp.MatchString(pattern, value)
			if err != nil {
				return nil, err
			}
			return matched, nil
		})
		if err != nil {
			slog.Warn("sqlite: failed to register regexp function", "error", err)
		}
	})
}

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

	// ВАЖНО: DSN с '?' в имени файла не поддерживается — modernc.org/sqlite
	// трактует '?' как начало query-строки, путь обрезается. Если файл содержит
	// '?', используй file:-URI с %3F (file:reports%3F2024.db) или переименуй файл.

	slog.Info("sqlite: opening connection")

	// Прагмы через DSN-параметры (_pragma=...), а не только через ExecContext:
	// ExecContext-PRAGMA применяется к ОДНОМУ коннекту пула (тому, что его выполнил),
	// второй коннект (SetMaxOpenConns(2)) остаётся без foreign_keys/busy_timeout.
	// modernc.org/sqlite применяет _pragma к КАЖДОМУ открываемому коннекту.
	dsn = ensurePragmaParams(dsn)

	conn, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, fmt.Errorf("sqlite: failed to open connection: %w", err)
	}

	// Регистрируем regexp() для modernc (без неё grep regex=true падает).
	// Глобальная регистрация в драйвере — идемпотентна через regexpOnce.
	registerSQLiteRegexp()

	// WAL mode поддерживает конкурентных читателей, поэтому пул увеличен до 2.
	// busy_timeout=5000 позволяет запросу подождать блокировки до 5 секунд
	// вместо немедленного "database is locked".
	conn.SetMaxOpenConns(2)

	if err := conn.PingContext(ctx); err != nil {
		_ = conn.Close()
		return nil, fmt.Errorf("sqlite: ping failed: %w", err)
	}

	// Дублируем прагмы через Exec для DSN, где _pragma не применился
	// (некоторые драйверы/форматы DSN). На 1-м коннекте это no-op,
	// на остальных — fallback. Основное обеспечение — DSN-параметры выше.
	//
	// M5: если DSN УЖЕ содержит _pragma= (явные прагмы пользователя) —
	// Exec-fallback НЕ выполняем: он применился бы только к 1-му коннекту
	// пула и перебил явные настройки (напр. foreign_keys(0) → ON), а 2-й
	// коннект остался бы без прагм вовсе → рассинхрон в рамках пула.
	if !strings.Contains(dsn, "_pragma=") {
		for _, pragma := range []string{
			"PRAGMA journal_mode=WAL",
			"PRAGMA synchronous=NORMAL",
			"PRAGMA busy_timeout=5000",
			"PRAGMA foreign_keys=ON",
		} {
			if _, err := conn.ExecContext(ctx, pragma); err != nil {
				slog.Warn("sqlite: pragma failed (non-fatal)", "pragma", pragma, "error", err)
			}
		}
	}

	slog.Info("sqlite: connection opened")

	return &SqliteConn{conn: conn}, nil
}

// pragmaParams — прагмы, добавляемые к каждому DSN как параметры modernc
// (применяются ко ВСЕМ коннектам пула, в отличие от ExecContext-PRAGMA).
// journal_mode(WAL) — write-операция: НЕ добавляется к read-only DSN
// (mode=ro / immutable=1) — на read-only БД WAL-переключение даёт
// "attempt to write a readonly database".
var pragmaParams = "_pragma=journal_mode(WAL)&_pragma=synchronous(NORMAL)&_pragma=busy_timeout(5000)&_pragma=foreign_keys(1)"

// readOnlyDSNRe — маркеры read-only DSN в modernc.org/sqlite URI.
var readOnlyDSNRe = regexp.MustCompile(`(^|&|\?)mode=ro(&|$)|immutable=1`)

// ensurePragmaParams добавляет _pragma параметры в DSN, если их там ещё нет.
// Не трогает DSN с уже заданными _pragma (приоритет у явных параметров).
// Для read-only DSN (mode=ro/immutable=1) write-прагмы (journal_mode WAL)
// пропускаются — они несовместимы с read-only файлом БД (см. pragmaParams).
func ensurePragmaParams(dsn string) string {
	if strings.Contains(dsn, "_pragma=") {
		return dsn
	}
	sep := "?"
	if strings.Contains(dsn, "?") {
		sep = "&"
	}
	if readOnlyDSNRe.MatchString(dsn) {
		// Read-only: только безопасные (read) прагмы.
		return dsn + sep + "_pragma=busy_timeout(5000)"
	}
	return dsn + sep + pragmaParams
}

// TranslatePlaceholder — SQLite нативно использует '?'.
func (SqliteAdapter) TranslatePlaceholder(index int) string { return "?" }

// QuoteIdentifier — двойные кавычки (ANSI SQL).
// Двойная кавычка внутри имени экранируется удвоением (" → ""),
// иначе имя вида a"; DROP TABLE x; -- выходит из кавычек (SQL-инъекция).
func (SqliteAdapter) QuoteIdentifier(name string) string {
	return `"` + strings.ReplaceAll(name, `"`, `""`) + `"`
}

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
			_ = rows.Close()
			return nil, fmt.Errorf("sqlite: scan sqlite_master row: %w", err)
		}
		tableRefs = append(tableRefs, tableRef{kind: kind, name: name})
	}
	if err := rows.Err(); err != nil {
		_ = rows.Close()
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
	// PRAGMA в SQLite не принимает связанные параметры (bound parameters),
	// поэтому имя таблицы экранируется через QuoteIdentifier.
	colRows, err := database.QueryContext(ctx, fmt.Sprintf("PRAGMA table_info(%s)", quoted))
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
			_ = colRows.Close()
			return tbl, fmt.Errorf("scan table_info: %w", err)
		}

		// nullable: PRAGMA table_info возвращает notnull=0 для PK-колонок,
		// если в DDL не было явного NOT NULL. Но PRIMARY KEY подразумевает
		// NOT NULL по стандарту SQL — корректная семантика nullable=false
		// для PK-колонок нужна для консистентности с Postgres (information_schema).
		isPK := pk > 0
		tbl.Columns = append(tbl.Columns, Column{
			Name:        cname,
			Type:        mapSQLiteType(ctype),
			Nullable:    !isPK && notnull == 0,
			Description: "", // SQLite не хранит комментарии к колонкам
		})

		// pk — порядковый номер в составе PRIMARY KEY (1, 2, ...).
		// Если pk > 0 — колонка входит в PK.
		if pk > 0 {
			primaryKey = append(primaryKey, cname)
		}
	}
	if err := colRows.Err(); err != nil {
		_ = colRows.Close()
		return tbl, fmt.Errorf("iterate table_info: %w", err)
	}
	_ = colRows.Close()
	tbl.PrimaryKey = primaryKey

	// PRAGMA foreign_key_list: id, seq, table, from, to, on_update, on_delete, match.
	// Строки группируются по id: каждая группа — один FK-constraint,
	// строки внутри упорядочены по seq и формируют композитный ключ.
	// PRAGMA не поддерживает bound parameters — имя экранируется через QuoteIdentifier.
	fkRows, err := database.QueryContext(ctx, fmt.Sprintf("PRAGMA foreign_key_list(%s)", quoted))
	if err != nil {
		return tbl, fmt.Errorf("foreign_key_list: %w", err)
	}
	defer fkRows.Close() //nolint:errcheck

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

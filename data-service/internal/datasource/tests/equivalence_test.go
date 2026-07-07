package datasource_test

// Контрактные тесты эквивалентности адаптеров.
//
// Цель: доказать, что SqliteAdapter и PostgresAdapter возвращают
// идентичный *Schema для одной и той же доменной схемы (после сортировки
// массивов и нормализации driver-имени).
//
// Это ГЛАВНЫЙ критерий готовности фазы 3.1: если SQLite и Postgres
// дают разные Schema для эквивалентных таблиц — контракт Adapter нарушен
// и runtime-слой фазы 3.2 не сможет быть СУБД-независимым.
//
// Запуск:
//   go test ./internal/datasource/... -run Equivalence -v
//
// Полная проверка требует живого Postgres:
//   docker compose up -d db
//   POSTGRES_TEST_URL='postgres://tutor:tutor@127.0.0.1:5432/postgres?sslmode=disable' \
//     go test ./internal/datasource/... -run Equivalence -v

import (
	"context"
	"encoding/json"
	"os"
	"testing"

	"github.com/agent-tutor/data-service/internal/datasource"
)

// genericSchemaDDL — нейтральная (e-commerce) DDL, совместимая и с SQLite,
// и с PostgreSQL. Используется как эталон для сравнения.
//
// Намеренно НЕ используем university-схему (students/teachers/...) чтобы
// не зависеть от домена.
const genericSchemaDDL = `
CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL,
    created_at TEXT
);
CREATE TABLE items (
    id INTEGER PRIMARY KEY,
    sku TEXT NOT NULL,
    price REAL NOT NULL,
    metadata TEXT
);
CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    quantity INTEGER,
    FOREIGN KEY (customer_id) REFERENCES customers(id),
    FOREIGN KEY (item_id) REFERENCES items(id)
);
`

// TestEquivalence_SqliteSelfCheck — sanity-check: один и тот же адаптер
// на одной и той же схеме даёт стабильный результат. Это baseline для
// TestEquivalence_CrossDriver.
func TestEquivalence_SqliteSelfCheck(t *testing.T) {
	ctx := context.Background()
	a := datasource.SqliteAdapter{}

	conn1, err := a.Connect(ctx, ":memory:")
	if err != nil {
		t.Fatalf("Connect #1: %v", err)
	}
	t.Cleanup(func() { _ = conn1.Close() })

	if err := execDDL(ctx, conn1, genericSchemaDDL); err != nil {
		t.Fatalf("DDL #1: %v", err)
	}

	schema1, err := a.Introspect(ctx, conn1)
	if err != nil {
		t.Fatalf("Introspect #1: %v", err)
	}

	conn2, err := a.Connect(ctx, ":memory:")
	if err != nil {
		t.Fatalf("Connect #2: %v", err)
	}
	t.Cleanup(func() { _ = conn2.Close() })

	if err := execDDL(ctx, conn2, genericSchemaDDL); err != nil {
		t.Fatalf("DDL #2: %v", err)
	}

	schema2, err := a.Introspect(ctx, conn2)
	if err != nil {
		t.Fatalf("Introspect #2: %v", err)
	}

	normalized1 := normalize(t, schema1)
	normalized2 := normalize(t, schema2)

	if string(normalized1) != string(normalized2) {
		t.Errorf("self-check failed: two introspections of the same schema differ\n#1: %s\n#2: %s",
			normalized1, normalized2)
	}
}

// TestEquivalence_CrossDriver — главный тест фазы 3.1.
//
// Skip'ается без POSTGRES_TEST_URL. Сравнивает Schema от SQLite
// и Schema от Postgres на одной и той же DDL.
//
// После нормализации (sort tables/columns/FKs + driver→"generic") два
// Schema должны совпадать байт-в-байт.
//
// Известные отклонения (документируются в коде, не считаются ошибками):
//   - Description: SQLite не хранит комментарии колонок → пустая строка.
//     Postgres хранит через COMMENT ON COLUMN. Тест использует DDL
//     без COMMENT, чтобы исключить это расхождение.
//   - Тип INTEGER NOT NULL: SQLite хранит INTEGER, Postgres — integer.
//     Нормализуется в generic TypeInt через mapSQLiteType / mapPostgresType.
func TestEquivalence_CrossDriver(t *testing.T) {
	pgDSN := os.Getenv("POSTGRES_TEST_URL")
	if pgDSN == "" {
		t.Skip("POSTGRES_TEST_URL не задана — пропускаем cross-driver тест.\n" +
			"Для запуска: docker compose up -d db + установить POSTGRES_TEST_URL.")
	}

	ctx := context.Background()

	// SQLite-схема.
	sqliteSchema, err := introspectSQLite(ctx, genericSchemaDDL)
	if err != nil {
		t.Fatalf("sqlite introspect: %v", err)
	}

	// Postgres-схема.
	pgSchema, err := introspectPostgres(ctx, pgDSN, genericSchemaDDL)
	if err != nil {
		t.Fatalf("postgres introspect: %v", err)
	}

	// Нормализуем обе Schema (driver→"generic", сортировка массивов).
	normSQLite := normalize(t, sqliteSchema)
	normPG := normalize(t, pgSchema)

	if string(normSQLite) != string(normPG) {
		t.Errorf("cross-driver equivalence failed\nSQLite:   %s\nPostgres: %s",
			normSQLite, normPG)
	}
}

// introspectSQLite создаёт :memory: SQLite-БД с заданным DDL и возвращает Schema.
func introspectSQLite(ctx context.Context, ddl string) (*datasource.Schema, error) {
	a := datasource.SqliteAdapter{}
	conn, err := a.Connect(ctx, ":memory:")
	if err != nil {
		return nil, err
	}
	defer conn.Close() //nolint:errcheck
	if err := execDDL(ctx, conn, ddl); err != nil {
		return nil, err
	}
	return a.Introspect(ctx, conn)
}

// introspectPostgres создаёт временную схему в Postgres-БД,
// применяет DDL, интроспектит, удаляет схему.
//
// Возвращает Schema только для временной схемы (фильтрует по schema name).
func introspectPostgres(ctx context.Context, dsn, ddl string) (*datasource.Schema, error) {
	a := datasource.PostgresAdapter{}
	conn, err := a.Connect(ctx, dsn)
	if err != nil {
		return nil, err
	}
	defer conn.Close() //nolint:errcheck

	const schema = "test_equivalence"
	setup := []string{
		"DROP SCHEMA IF EXISTS " + schema + " CASCADE",
		"CREATE SCHEMA " + schema,
	}
	for _, stmt := range setup {
		if _, err := conn.ExecContext(ctx, stmt); err != nil {
			return nil, err
		}
	}
	// Переписываем DDL с префиксом схемы (упрощённо — split by ';')
	for _, stmt := range splitDDL(ddl) {
		prefixed := injectSchema(stmt, schema)
		if _, err := conn.ExecContext(ctx, prefixed); err != nil {
			_, _ = conn.ExecContext(context.Background(),
				"DROP SCHEMA IF EXISTS "+schema+" CASCADE")
			return nil, err
		}
	}

	fullSchema, err := a.Introspect(ctx, conn)
	if err != nil {
		_, _ = conn.ExecContext(context.Background(),
			"DROP SCHEMA IF EXISTS "+schema+" CASCADE")
		return nil, err
	}

	_, _ = conn.ExecContext(context.Background(),
		"DROP SCHEMA IF EXISTS "+schema+" CASCADE")

	// Фильтруем по нашей схеме (Introspect возвращает все пользовательские таблицы).
	filtered := &datasource.Schema{Driver: fullSchema.Driver}
	for _, tbl := range fullSchema.Tables {
		// Postgres возвращает имена в формате "schema.table".
		if hasSchemaPrefix(tbl.Name, schema) {
			filtered.Tables = append(filtered.Tables, tbl)
		}
	}
	return filtered, nil
}

// hasSchemaPrefix проверяет, начинается ли FQN с schema.table.
func hasSchemaPrefix(fqn, schema string) bool {
	if len(fqn) <= len(schema)+1 {
		return false
	}
	return fqn[:len(schema)] == schema && fqn[len(schema)] == '.'
}

// injectSchema добавляет префикс схемы ко всем именам таблиц в DDL.
// Упрощённо: для известного набора таблиц из genericSchemaDDL.
func injectSchema(ddlStmt, schema string) string {
	// Не пытаемся сделать полноценный SQL-парсер — заменяем по известным именам.
	// Для фазы 3.1 этого достаточно; в фазе 3.x нужен нормальный SQL parser.
	replacements := []struct{ from, to string }{
		{"CREATE TABLE customers ", "CREATE TABLE " + schema + ".customers "},
		{"CREATE TABLE items ", "CREATE TABLE " + schema + ".items "},
		{"CREATE TABLE orders ", "CREATE TABLE " + schema + ".orders "},
		{"REFERENCES customers(id)", "REFERENCES " + schema + ".customers(id)"},
		{"REFERENCES items(id)", "REFERENCES " + schema + ".items(id)"},
	}
	out := ddlStmt
	for _, r := range replacements {
		j := indexOf(out, r.from, 0)
		if j >= 0 {
			out = out[:j] + r.to + out[j+len(r.from):]
		}
	}
	return out
}

// indexOf — лёгкий strings.Index без импорта strings.
func indexOf(s, sub string, from int) int {
	if from < 0 {
		from = 0
	}
	for i := from; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return i
		}
	}
	return -1
}

// splitDDL — наивный split по ';' (см. комментарий в sqlite_adapter_test.go).
// Вынесен сюда чтобы не дублировать.
func splitDDL(ddl string) []string {
	out := make([]string, 0)
	cur := ""
	for _, r := range ddl {
		if r == ';' {
			out = append(out, trimWhitespace(cur))
			cur = ""
			continue
		}
		cur += string(r)
	}
	if cur != "" {
		out = append(out, trimWhitespace(cur))
	}
	return out
}

func trimWhitespace(s string) string {
	for len(s) > 0 && isSpace(s[0]) {
		s = s[1:]
	}
	for len(s) > 0 && isSpace(s[len(s)-1]) {
		s = s[:len(s)-1]
	}
	return s
}

func isSpace(b byte) bool {
	return b == ' ' || b == '\n' || b == '\t' || b == '\r'
}

// execDDL применяет multi-statement DDL через ExecContext.
func execDDL(ctx context.Context, conn datasource.Conn, ddl string) error {
	for _, stmt := range splitDDL(ddl) {
		if stmt == "" {
			continue
		}
		if _, err := conn.ExecContext(ctx, stmt); err != nil {
			return err
		}
	}
	return nil
}

// normalize приводит Schema к каноническому виду для сравнения:
//   - Driver заменяется на "generic" (нам важен shape, не источник)
//   - Tables сортируются по имени
//   - Columns сортируются по ordinal_position (== индекс в слайсе,
//     сохраняется порядок из БД)
//   - ForeignKeys сортируются по (ReferencedTable, Columns[0])
//   - PrimaryKey уже отсортирован по ordinal_position из БД
//   - Schema prefix из имён таблиц и FK-ссылок удаляется
//     (Postgres возвращает "test_equivalence.customers", SQLite — "customers")
//   - Имена FK-констрейнтов обнуляются
//     (Postgres генерирует "table_col_fkey", SQLite — "fk_N",
//     эти имена СУБД-зависимы и не должны влиять на equivalence)
//
// Возвращает canonical JSON (deterministic order, no whitespace).
func normalize(t *testing.T, s *datasource.Schema) []byte {
	t.Helper()

	clone := datasource.Schema{
		Driver: "generic",
		Tables: make([]datasource.Table, len(s.Tables)),
	}
	copy(clone.Tables, s.Tables)

	// Strip schema prefix и обнуляем FK-имена.
	// Также инициализируем пустые слайсы (nil → []), чтобы JSON encoding
	// давал одинаковый результат в SQLite (nil slice) и Postgres (пустой slice).
	for i := range clone.Tables {
		clone.Tables[i].Name = stripSchemaPrefix(clone.Tables[i].Name)
		if clone.Tables[i].Columns == nil {
			clone.Tables[i].Columns = []datasource.Column{}
		}
		if clone.Tables[i].PrimaryKey == nil {
			clone.Tables[i].PrimaryKey = []string{}
		}
		if clone.Tables[i].ForeignKeys == nil {
			clone.Tables[i].ForeignKeys = []datasource.ForeignKey{}
		}
		for j := range clone.Tables[i].ForeignKeys {
			fk := &clone.Tables[i].ForeignKeys[j]
			fk.Name = ""
			fk.ReferencedTable = stripSchemaPrefix(fk.ReferencedTable)
			if fk.Columns == nil {
				fk.Columns = []string{}
			}
			if fk.ReferencedColumns == nil {
				fk.ReferencedColumns = []string{}
			}
		}
	}

	// Sort tables by Name.
	for i := 0; i < len(clone.Tables); i++ {
		for j := i + 1; j < len(clone.Tables); j++ {
			if clone.Tables[i].Name > clone.Tables[j].Name {
				clone.Tables[i], clone.Tables[j] = clone.Tables[j], clone.Tables[i]
			}
		}
	}

	// Sort ForeignKeys within each table.
	for i := range clone.Tables {
		fks := clone.Tables[i].ForeignKeys
		for a := 0; a < len(fks); a++ {
			for b := a + 1; b < len(fks); b++ {
				keyA := fkSortKey(fks[a])
				keyB := fkSortKey(fks[b])
				if keyA > keyB {
					fks[a], fks[b] = fks[b], fks[a]
				}
			}
		}
	}

	out, err := json.Marshal(clone)
	if err != nil {
		t.Fatalf("normalize: marshal: %v", err)
	}
	return out
}

// stripSchemaPrefix удаляет префикс схемы из FQN: "test_equivalence.customers"
// → "customers". Если точка не найдена, возвращает строку as-is.
func stripSchemaPrefix(name string) string {
	for i := 0; i < len(name); i++ {
		if name[i] == '.' {
			return name[i+1:]
		}
	}
	return name
}

// fkSortKey возвращает детерминированный ключ для сортировки FK.
func fkSortKey(fk datasource.ForeignKey) string {
	if len(fk.Columns) == 0 {
		return fk.ReferencedTable + "|"
	}
	return fk.ReferencedTable + "|" + fk.Columns[0]
}

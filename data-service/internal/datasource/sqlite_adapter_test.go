package datasource_test

import (
	"context"
	"testing"

	"github.com/agent-tutor/data-service/internal/datasource"
	"github.com/agent-tutor/data-service/internal/db"
)

// TestSqliteAdapter_Driver — адаптер сообщает свой идентификатор.
func TestSqliteAdapter_Driver(t *testing.T) {
	if got := (datasource.SqliteAdapter{}).Driver(); got != "sqlite" {
		t.Fatalf("Driver() = %q, want %q", got, "sqlite")
	}
}

// TestSqliteAdapter_TranslatePlaceholder — SQLite использует нативный '?'.
func TestSqliteAdapter_TranslatePlaceholder(t *testing.T) {
	a := datasource.SqliteAdapter{}
	for _, idx := range []int{1, 2, 3, 42} {
		if got := a.TranslatePlaceholder(idx); got != "?" {
			t.Fatalf("TranslatePlaceholder(%d) = %q, want %q", idx, got, "?")
		}
	}
}

// TestSqliteAdapter_QuoteIdentifier — двойные кавычки ANSI SQL.
func TestSqliteAdapter_QuoteIdentifier(t *testing.T) {
	a := datasource.SqliteAdapter{}
	cases := []struct {
		in, want string
	}{
		{"students", `"students"`},
		{"student name", `"student name"`},
		{"group_id", `"group_id"`},
		{"", `""`},
	}
	for _, c := range cases {
		if got := a.QuoteIdentifier(c.in); got != c.want {
			t.Errorf("QuoteIdentifier(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}

// TestSqliteAdapter_Introspect_Empty — на :memory: без таблиц возвращается
// пустой Schema с правильным Driver.
func TestSqliteAdapter_Introspect_Empty(t *testing.T) {
	ctx := context.Background()

	conn, err := (datasource.SqliteAdapter{}).Connect(ctx, ":memory:")
	if err != nil {
		t.Fatalf("Connect: %v", err)
	}
	t.Cleanup(func() { _ = conn.Close() })

	got, err := (datasource.SqliteAdapter{}).Introspect(ctx, conn)
	if err != nil {
		t.Fatalf("Introspect: %v", err)
	}
	if got.Driver != "sqlite" {
		t.Errorf("Driver = %q, want %q", got.Driver, "sqlite")
	}
	if len(got.Tables) != 0 {
		t.Errorf("len(Tables) = %d, want 0; got tables = %v", len(got.Tables), tableNames(got))
	}
}

// TestSqliteAdapter_Introspect_UniversitySchema — на DDL из schema.sql
// должны получиться 6 таблиц с правильными колонками, PK и FK.
func TestSqliteAdapter_Introspect_UniversitySchema(t *testing.T) {
	ctx := context.Background()

	conn, err := (datasource.SqliteAdapter{}).Connect(ctx, ":memory:")
	if err != nil {
		t.Fatalf("Connect: %v", err)
	}
	t.Cleanup(func() { _ = conn.Close() })

	// Применяем реальный DDL проекта — это гарантирует, что интроспекция
	// работает на продакшен-схеме university.db, а не на тестовом сабсете.
	if err := applyDDL(ctx, conn, db.SchemaSQL); err != nil {
		t.Fatalf("apply DDL: %v", err)
	}

	got, err := (datasource.SqliteAdapter{}).Introspect(ctx, conn)
	if err != nil {
		t.Fatalf("Introspect: %v", err)
	}

	if got.Driver != "sqlite" {
		t.Errorf("Driver = %q, want %q", got.Driver, "sqlite")
	}

	if len(got.Tables) != 6 {
		t.Fatalf("len(Tables) = %d, want 6; got = %v", len(got.Tables), tableNames(got))
	}

	wantTables := map[string]struct {
		columns    []string
		primaryKey []string
		fks        map[string]fkExpectation
	}{
		"groups": {
			columns:    []string{"id", "name", "speciality"},
			primaryKey: []string{"id"},
			fks:        map[string]fkExpectation{},
		},
		"students": {
			columns:    []string{"id", "name", "group_id", "course"},
			primaryKey: []string{"id"},
			fks: map[string]fkExpectation{
				"students.group_id -> groups": {col: "group_id", refTable: "groups", refCol: "id"},
			},
		},
		"teachers": {
			columns:    []string{"id", "name", "disciplines_json"},
			primaryKey: []string{"id"},
			fks:        map[string]fkExpectation{},
		},
		"disciplines": {
			columns:    []string{"id", "name", "description"},
			primaryKey: []string{"id"},
			fks:        map[string]fkExpectation{},
		},
		"grades": {
			columns:    []string{"id", "student_id", "discipline_id", "grade", "date"},
			primaryKey: []string{"id"},
			fks: map[string]fkExpectation{
				"grades.student_id -> students":       {col: "student_id", refTable: "students", refCol: "id"},
				"grades.discipline_id -> disciplines": {col: "discipline_id", refTable: "disciplines", refCol: "id"},
			},
		},
		"schedule": {
			columns:    []string{"id", "day", "group_id", "lessons_json"},
			primaryKey: []string{"id"},
			fks: map[string]fkExpectation{
				"schedule.group_id -> groups": {col: "group_id", refTable: "groups", refCol: "id"},
			},
		},
	}

	byName := make(map[string]datasource.Table, len(got.Tables))
	for _, tbl := range got.Tables {
		byName[tbl.Name] = tbl
	}

	for name, want := range wantTables {
		tbl, ok := byName[name]
		if !ok {
			t.Errorf("table %q missing from introspection", name)
			continue
		}

		// Колонки.
		gotCols := make([]string, 0, len(tbl.Columns))
		for _, c := range tbl.Columns {
			gotCols = append(gotCols, c.Name)
		}
		if !equalStringSlices(gotCols, want.columns) {
			t.Errorf("table %q columns = %v, want %v", name, gotCols, want.columns)
		}

		// PrimaryKey.
		if !equalStringSlices(tbl.PrimaryKey, want.primaryKey) {
			t.Errorf("table %q primary_key = %v, want %v", name, tbl.PrimaryKey, want.primaryKey)
		}

		// ForeignKeys.
		for fkLabel, wantFK := range want.fks {
			fk := findFK(tbl.ForeignKeys, wantFK.col, wantFK.refTable)
			if fk == nil {
				t.Errorf("table %q missing FK %s", name, fkLabel)
				continue
			}
			if !equalStringSlices(fk.Columns, []string{wantFK.col}) {
				t.Errorf("table %q FK %s columns = %v, want [%s]",
					name, fkLabel, fk.Columns, wantFK.col)
			}
			if !equalStringSlices(fk.ReferencedColumns, []string{wantFK.refCol}) {
				t.Errorf("table %q FK %s referenced_columns = %v, want [%s]",
					name, fkLabel, fk.ReferencedColumns, wantFK.refCol)
			}
		}

		// Не должно быть лишних FK.
		if len(tbl.ForeignKeys) != len(want.fks) {
			t.Errorf("table %q foreign_keys count = %d, want %d (got %v)",
				name, len(tbl.ForeignKeys), len(want.fks), tbl.ForeignKeys)
		}
	}
}

// tableNames — утилита для диагностических сообщений.
func tableNames(s *datasource.Schema) []string {
	out := make([]string, 0, len(s.Tables))
	for _, tbl := range s.Tables {
		out = append(out, tbl.Name)
	}
	return out
}

type fkExpectation struct {
	col      string
	refTable string
	refCol   string
}

// findFK возвращает FK, у которого колонка и целевая таблица совпадают.
func findFK(fks []datasource.ForeignKey, col, refTable string) *datasource.ForeignKey {
	for i := range fks {
		fk := &fks[i]
		if len(fk.Columns) == 1 && fk.Columns[0] == col && fk.ReferencedTable == refTable {
			return fk
		}
	}
	return nil
}

// equalStringSlices — поэлементное сравнение, nil-эквивалентно [].
func equalStringSlices(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// applyDDL применяет DDL (SQLite совместимый) через ExecContext, разделяя
// DDL по ';' — простая эвристика, достаточная для schema.sql без сложных
// вложенных конструкций и хранимых процедур.
func applyDDL(ctx context.Context, database db.DB, ddl string) error {
	stmts := splitSQL(ddl)
	for _, s := range stmts {
		if s == "" {
			continue
		}
		if _, err := database.ExecContext(ctx, s); err != nil {
			return err
		}
	}
	return nil
}

// splitSQL — наивный сплиттер по ';'. Для schema.sql этого достаточно:
// нет строковых литералов с ';' внутри, нет хранимых процедур.
func splitSQL(ddl string) []string {
	out := make([]string, 0)
	cur := ""
	for _, r := range ddl {
		if r == ';' {
			out = append(out, trimSQL(cur))
			cur = ""
			continue
		}
		cur += string(r)
	}
	if cur != "" {
		out = append(out, trimSQL(cur))
	}
	return out
}

func trimSQL(s string) string {
	// Убираем ведущие/завершающие пробелы и переводы строк.
	for len(s) > 0 && (s[0] == ' ' || s[0] == '\n' || s[0] == '\t' || s[0] == '\r') {
		s = s[1:]
	}
	for len(s) > 0 && (s[len(s)-1] == ' ' || s[len(s)-1] == '\n' || s[len(s)-1] == '\t' || s[len(s)-1] == '\r') {
		s = s[:len(s)-1]
	}
	return s
}

package runtime

import (
	"context"
	"database/sql"
	"math"
	"reflect"
	"testing"
	"time"

	_ "modernc.org/sqlite"
)

func TestResponseMapper_PublicFor_Found(t *testing.T) {
	b := &Builder{}
	entity := Entity{
		Fields: []EntityField{
			{Name: "id", Column: "id_col"},
			{Name: "email", Column: "email_address"},
		},
	}

	name, ok := b.publicFor(entity, "email_address")
	if !ok {
		t.Fatal("publicFor('email_address'): expected ok=true")
	}
	if name != "email" {
		t.Errorf("publicFor = %q, want %q", name, "email")
	}
}

func TestResponseMapper_PublicFor_NotFound(t *testing.T) {
	b := &Builder{}
	entity := Entity{
		Fields: []EntityField{
			{Name: "id", Column: "id_col"},
		},
	}

	_, ok := b.publicFor(entity, "nope")
	if ok {
		t.Error("publicFor('nope'): expected ok=false")
	}
}

func TestResponseMapper_PublicFor_EmptyFields(t *testing.T) {
	b := &Builder{}
	entity := Entity{Fields: []EntityField{}}

	_, ok := b.publicFor(entity, "anything")
	if ok {
		t.Error("publicFor with empty fields: expected ok=false")
	}
}

func TestFieldTypeFor_Found(t *testing.T) {
	b := &Builder{}
	entity := Entity{
		Fields: []EntityField{
			{Name: "id", Type: "int"},
			{Name: "name", Type: "string"},
			{Name: "score", Type: "float"},
			{Name: "active", Type: "bool"},
			{Name: "data", Type: "json"},
		},
	}

	tests := []struct {
		field string
		want  string
	}{
		{"id", "int"},
		{"name", "string"},
		{"score", "float"},
		{"active", "bool"},
		{"data", "json"},
	}
	for _, tc := range tests {
		got := b.fieldTypeFor(entity, tc.field)
		if got != tc.want {
			t.Errorf("fieldTypeFor(%q) = %q, want %q", tc.field, got, tc.want)
		}
	}
}

func TestFieldTypeFor_NotFound(t *testing.T) {
	b := &Builder{}
	entity := Entity{
		Fields: []EntityField{
			{Name: "id", Type: "int"},
		},
	}

	got := b.fieldTypeFor(entity, "nonexistent")
	if got != "" {
		t.Errorf("fieldTypeFor('nonexistent') = %q, want ''", got)
	}
}

func TestFieldTypeFor_EmptyFields(t *testing.T) {
	b := &Builder{}
	entity := Entity{Fields: []EntityField{}}

	got := b.fieldTypeFor(entity, "anything")
	if got != "" {
		t.Errorf("fieldTypeFor with empty fields = %q, want ''", got)
	}
}

// TestMapRow_WithPublicMapping runs MapRow against a real SQLite DB
// to ensure column→public name mapping and type coercion both work.
func TestMapRow_WithPublicMapping(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("sql.Open: %v", err)
	}
	defer db.Close() //nolint:errcheck
	db.SetMaxOpenConns(1)

	ctx := context.Background()
	_, err = db.ExecContext(ctx,
		`CREATE TABLE test_table (id INTEGER PRIMARY KEY, full_name TEXT, points REAL)`)
	if err != nil {
		t.Fatalf("create: %v", err)
	}
	_, err = db.ExecContext(ctx,
		`INSERT INTO test_table (id, full_name, points) VALUES (?, ?, ?)`,
		42, "Alice", 95.5)
	if err != nil {
		t.Fatalf("insert: %v", err)
	}

	adapter := &internalTestAdapter{db: db}
	b := NewBuilder(adapter)

	entity := Entity{
		Name:     "test",
		Table:    "test_table",
		IDColumn: "id",
		Fields: []EntityField{
			{Name: "id", Column: "id", Type: "int", PrimaryKey: true},
			{Name: "name", Column: "full_name", Type: "string"},
			{Name: "score", Column: "points", Type: "float"},
		},
	}

	rows, err := adapter.QueryContext(ctx, `SELECT id, full_name, points FROM test_table`)
	if err != nil {
		t.Fatalf("select: %v", err)
	}
	defer rows.Close() //nolint:errcheck

	if !rows.Next() {
		t.Fatal("rows.Next: no rows")
	}

	row, err := b.MapRow(rows, entity)
	if err != nil {
		t.Fatalf("MapRow: %v", err)
	}

	// Check public names, NOT column names
	if row["id"] != int64(42) {
		t.Errorf("id = %v (%T), want 42 (int64)", row["id"], row["id"])
	}
	if row["name"] != "Alice" {
		t.Errorf("name = %v, want Alice", row["name"])
	}
	if row["score"] != 95.5 {
		t.Errorf("score = %v, want 95.5", row["score"])
	}

	// Must NOT contain raw column name
	if _, ok := row["full_name"]; ok {
		t.Error("row should not contain DB column name 'full_name', got ok=true")
	}
}

// TestMapRows_LimitAndFullIteration tests MapRows with different maxRows values.
func TestMapRows_LimitAndFullIteration(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("sql.Open: %v", err)
	}
	defer db.Close() //nolint:errcheck
	db.SetMaxOpenConns(1)

	ctx := context.Background()
	_, err = db.ExecContext(ctx, `CREATE TABLE items (id INTEGER PRIMARY KEY, val TEXT)`)
	if err != nil {
		t.Fatalf("create: %v", err)
	}
	for i := 1; i <= 5; i++ {
		_, err = db.ExecContext(ctx, `INSERT INTO items (id, val) VALUES (?, ?)`, i, "x")
		if err != nil {
			t.Fatalf("insert %d: %v", i, err)
		}
	}

	adapter := &internalTestAdapter{db: db}
	b := NewBuilder(adapter)

	entity := Entity{
		Name:  "item",
		Table: "items",
		Fields: []EntityField{
			{Name: "id", Column: "id", Type: "int"},
			{Name: "val", Column: "val", Type: "string"},
		},
	}

	rows, err := adapter.QueryContext(ctx, `SELECT id, val FROM items ORDER BY id`)
	if err != nil {
		t.Fatalf("select: %v", err)
	}

	mapper := func(r *sql.Rows) (map[string]any, error) {
		return b.MapRow(r, entity)
	}

	out, err := b.MapRows(rows, mapper, 2)
	if err != nil {
		t.Fatalf("MapRows: %v", err)
	}
	if len(out) != 2 {
		t.Errorf("MapRows maxRows=2: got %d rows, want 2", len(out))
	}
}

func TestMapRows_ZeroLimit(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("sql.Open: %v", err)
	}
	defer db.Close() //nolint:errcheck
	db.SetMaxOpenConns(1)

	ctx := context.Background()
	_, err = db.ExecContext(ctx, `CREATE TABLE items (id INTEGER PRIMARY KEY, val TEXT)`)
	if err != nil {
		t.Fatalf("create: %v", err)
	}
	for i := 1; i <= 3; i++ {
		if _, err = db.ExecContext(ctx, `INSERT INTO items (id, val) VALUES (?, ?)`, i, "x"); err != nil {
			t.Fatalf("insert %d: %v", i, err)
		}
	}

	adapter := &internalTestAdapter{db: db}
	b := NewBuilder(adapter)

	entity := Entity{
		Name:  "item",
		Table: "items",
		Fields: []EntityField{
			{Name: "id", Column: "id", Type: "int"},
		},
	}

	rows, err := adapter.QueryContext(ctx, `SELECT id FROM items ORDER BY id`)
	if err != nil {
		t.Fatalf("select: %v", err)
	}

	mapper := func(r *sql.Rows) (map[string]any, error) {
		return b.MapRow(r, entity)
	}

	out, err := b.MapRows(rows, mapper, 0)
	if err != nil {
		t.Fatalf("MapRows: %v", err)
	}
	if len(out) != 3 {
		t.Errorf("MapRows maxRows=0: got %d rows, want 3", len(out))
	}
}

// TestMapRow_NilColumn tests MapRow with NULL in the DB.
// TestCoerceNative_DateTimeMissingCase verifies that coerceNative with
// typ="datetime" or typ="date" normalizes to canonical RFC3339 instead of
// falling through to the default case (fmt.Sprintf) or pass-through.
// Разные драйверы отдают по-разному (sqlite — string, pgx — time.Time),
// поэтому формат канонизируется: time.Time → RFC3339, string → parse → RFC3339.
func TestCoerceNative_DateTimeMissingCase(t *testing.T) {
	now := time.Now().Truncate(time.Second).UTC()

	tests := []struct {
		name string
		val  any
		typ  string
		want any
	}{
		{
			name: "datetime returns time.Time as RFC3339",
			val:  now,
			typ:  "datetime",
			want: now.Format(time.RFC3339),
		},
		{
			name: "date returns time.Time as RFC3339",
			val:  now,
			typ:  "date",
			want: now.Format(time.RFC3339),
		},
		{
			name: "sqlite string datetime normalizes to RFC3339",
			val:  "2024-01-15 10:30:00",
			typ:  "datetime",
			want: "2024-01-15T10:30:00Z",
		},
		{
			name: "sqlite string date normalizes to RFC3339",
			val:  "2024-01-15",
			typ:  "date",
			want: "2024-01-15T00:00:00Z",
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := coerceNative(tc.val, tc.typ)
			if got != tc.want {
				t.Errorf("coerceNative(%v, %q) = %v (%T), want %v (%T)",
					tc.val, tc.typ, got, got, tc.want, tc.want)
			}
		})
	}
}

func TestMapRow_NilColumn(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("sql.Open: %v", err)
	}
	defer db.Close() //nolint:errcheck
	db.SetMaxOpenConns(1)

	ctx := context.Background()
	_, err = db.ExecContext(ctx, `CREATE TABLE nullable (id INTEGER PRIMARY KEY, val TEXT, score REAL)`)
	if err != nil {
		t.Fatalf("create: %v", err)
	}
	_, err = db.ExecContext(ctx,
		`INSERT INTO nullable (id, val, score) VALUES (?, ?, ?)`, 1, nil, nil)
	if err != nil {
		t.Fatalf("insert: %v", err)
	}

	adapter := &internalTestAdapter{db: db}
	b := NewBuilder(adapter)

	entity := Entity{
		Name:  "nullable",
		Table: "nullable",
		Fields: []EntityField{
			{Name: "id", Column: "id", Type: "int"},
			{Name: "val", Column: "val", Type: "string", Nullable: true},
			{Name: "score", Column: "score", Type: "float", Nullable: true},
		},
	}

	rows, err := adapter.QueryContext(ctx, `SELECT id, val, score FROM nullable`)
	if err != nil {
		t.Fatalf("select: %v", err)
	}
	defer rows.Close() //nolint:errcheck

	if !rows.Next() {
		t.Fatal("rows.Next: no rows")
	}

	row, err := b.MapRow(rows, entity)
	if err != nil {
		t.Fatalf("MapRow: %v", err)
	}

	if row["val"] != nil {
		t.Errorf("val = %v, want nil", row["val"])
	}
	if row["score"] != nil {
		t.Errorf("score = %v, want nil", row["score"])
	}
}

// internalTestAdapter — local adapter for internal tests.
type internalTestAdapter struct {
	db *sql.DB
}

func (a *internalTestAdapter) QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error) {
	return a.db.QueryContext(ctx, query, args...)
}
func (a *internalTestAdapter) QuoteIdentifier(name string) string    { return `"` + name + `"` }
func (a *internalTestAdapter) TranslatePlaceholder(idx int) string   { return "?" }
func (a *internalTestAdapter) PingContext(ctx context.Context) error { return a.db.PingContext(ctx) }

// TestCoerceNative_FloatToInt_NoSilentLoss — Задача 1 (HIGH):
// float64 → int64 не должен тихо усекать дробь или saturate диапазон.
func TestCoerceNative_FloatToInt_NoSilentLoss(t *testing.T) {
	tests := []struct {
		name string
		val  any
		want any // nil → проверяем только что НЕ int64
	}{
		{"fractional 95.7", 95.7, nil},
		{"overflow 1e20", 1e20, nil},
		{"huge positive 1e300", 1e300, nil},
		{"huge negative -1e300", -1e300, nil},
		{"exact int 42.0", 42.0, int64(42)},
		{"negative exact -7.0", -7.0, int64(-7)},
		{"in range max", float64(1 << 40), int64(1 << 40)},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := coerceNative(tc.val, "int")
			if tc.want == nil {
				// Должен вернуть исходный float64 (НЕ тихо кастовать в int64).
				if _, ok := got.(int64); ok {
					t.Errorf("coerceNative(%v, \"int\") = %v (int64) — тихий каст с потерей, want float64", tc.val, got)
				}
				if f, ok := got.(float64); !ok || f != tc.val {
					t.Errorf("coerceNative(%v, \"int\") = %v (%T), want исходный float64 %v", tc.val, got, got, tc.val)
				}
			} else {
				if got != tc.want {
					t.Errorf("coerceNative(%v, \"int\") = %v (%T), want %v", tc.val, got, got, tc.want)
				}
			}
		})
	}
}

// TestCoerceNative_DatetimeCanonicalRFC3339 — Задача 3 (MEDIUM):
// datetime/date → канонический RFC3339 независимо от драйвера.
func TestCoerceNative_DatetimeCanonicalRFC3339(t *testing.T) {
	now := time.Date(2024, 1, 15, 10, 30, 0, 0, time.UTC)

	tests := []struct {
		name string
		val  any
		typ  string
		want any
	}{
		{
			name: "time.Time datetime → RFC3339",
			val:  now,
			typ:  "datetime",
			want: "2024-01-15T10:30:00Z",
		},
		{
			name: "time.Time date → RFC3339",
			val:  now,
			typ:  "date",
			want: "2024-01-15T10:30:00Z",
		},
		{
			name: "sqlite string datetime → RFC3339",
			val:  "2024-01-15 10:30:00",
			typ:  "datetime",
			want: "2024-01-15T10:30:00Z",
		},
		{
			name: "sqlite string date → RFC3339",
			val:  "2024-01-15",
			typ:  "date",
			want: "2024-01-15T00:00:00Z",
		},
		{
			name: "already RFC3339 string stays",
			val:  "2024-01-15T10:30:00Z",
			typ:  "datetime",
			want: "2024-01-15T10:30:00Z",
		},
		{
			name: "unparseable string stays as-is",
			val:  "not-a-date",
			typ:  "datetime",
			want: "not-a-date",
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := coerceNative(tc.val, tc.typ)
			if got != tc.want {
				t.Errorf("coerceNative(%v, %q) = %v (%T), want %v", tc.val, tc.typ, got, got, tc.want)
			}
		})
	}
}

// TestNormalizeDateTime_MillisAndTimezone — M6: normalizeDateTime должен
// парсить sqlite-форматы с миллисекундами и таймзоной (раньше ok=false).
func TestNormalizeDateTime_MillisAndTimezone(t *testing.T) {
	tests := []struct {
		name string
		in   string
		want string
		ok   bool
	}{
		{"millis space", "2024-01-02 15:04:05.123", "2024-01-02T15:04:05Z", true},
		{"millis T", "2024-01-02T15:04:05.123", "2024-01-02T15:04:05Z", true},
		{"timezone space", "2024-01-02 15:04:05+00:00", "2024-01-02T15:04:05Z", true},
		{"timezone T", "2024-01-02T15:04:05+03:00", "2024-01-02T12:04:05Z", true},
		{"millis timezone", "2024-01-02 15:04:05.123+00:00", "2024-01-02T15:04:05Z", true},
		{"plain stays", "2024-01-02 15:04:05", "2024-01-02T15:04:05Z", true},
		{"empty", "  ", "", false},
		{"invalid", "not-a-date", "", false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, ok := normalizeDateTime(tt.in)
			if ok != tt.ok {
				t.Fatalf("normalizeDateTime(%q) ok = %v, want %v", tt.in, ok, tt.ok)
			}
			if ok && got != tt.want {
				t.Errorf("normalizeDateTime(%q) = %q, want %q", tt.in, got, tt.want)
			}
		})
	}
}

// TestCoerceNative_ConversionMatrix covers conversion branches not exercised by
// the date/time and float-to-int regression tests above.
func TestCoerceNative_ConversionMatrix(t *testing.T) {
	tests := []struct {
		name    string
		val     any
		typ     string
		want    any
		wantNaN bool
	}{
		{name: "nil stays nil", val: nil, typ: "int", want: nil},
		{name: "int string parses", val: "17", typ: "int", want: int64(17)},
		{name: "invalid int string stays string", val: "17x", typ: "int", want: "17x"},
		{name: "integer converts to float", val: int64(7), typ: "float", want: float64(7)},
		{name: "float string parses", val: "2.5", typ: "float", want: float64(2.5)},
		{name: "invalid float string stays string", val: "two", typ: "float", want: "two"},
		{name: "zero integer converts to false", val: int64(0), typ: "bool", want: false},
		{name: "nonzero float converts to true", val: float64(-0.25), typ: "bool", want: true},
		{name: "boolean string parses", val: "true", typ: "bool", want: true},
		{name: "invalid boolean string stays string", val: "yes", typ: "bool", want: "yes"},
		{name: "json string decodes", val: `{"enabled":true}`, typ: "json", want: map[string]any{"enabled": true}},
		{name: "invalid json string stays string", val: "{", typ: "json", want: "{"},
		{name: "json bytes decode", val: []byte(`[1,2]`), typ: "json", want: []any{float64(1), float64(2)}},
		{name: "invalid json bytes become string", val: []byte("["), typ: "json", want: "["},
		{name: "date bytes normalize", val: []byte("2024-01-15"), typ: "date", want: "2024-01-15T00:00:00Z"},
		{name: "invalid date bytes stay bytes", val: []byte("not-a-date"), typ: "datetime", want: []byte("not-a-date")},
		{name: "unknown type formats value", val: int64(7), typ: "unknown", want: "7"},
		{name: "NaN integer conversion preserves NaN", val: math.NaN(), typ: "int", wantNaN: true},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := coerceNative(tc.val, tc.typ)
			if tc.wantNaN {
				value, ok := got.(float64)
				if !ok || !math.IsNaN(value) {
					t.Fatalf("coerceNative(%v, %q) = %v (%T), want NaN float64", tc.val, tc.typ, got, got)
				}
				return
			}
			if !reflect.DeepEqual(got, tc.want) {
				t.Errorf("coerceNative(%v, %q) = %#v (%T), want %#v (%T)", tc.val, tc.typ, got, got, tc.want, tc.want)
			}
		})
	}
}

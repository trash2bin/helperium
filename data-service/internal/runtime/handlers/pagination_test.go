package handlers

import (
	"context"
	"database/sql"
	"net/http"
	"testing"

	_ "modernc.org/sqlite"
)

func TestCountQuery(t *testing.T) {
	tests := []struct {
		name  string
		input string
		want  string
	}{
		{
			name:  "simple select",
			input: `SELECT "id", "name" FROM "students"`,
			want:  `SELECT COUNT(*) FROM "students"`,
		},
		{
			name:  "with where",
			input: `SELECT "id", "name" FROM "students" WHERE "course" = ?`,
			want:  `SELECT COUNT(*) FROM "students" WHERE "course" = ?`,
		},
		{
			name:  "with multiple conditions",
			input: `SELECT "id" FROM "students" WHERE "course" = ? AND "group_id" = ?`,
			want:  `SELECT COUNT(*) FROM "students" WHERE "course" = ? AND "group_id" = ?`,
		},
		{
			name:  "with limit",
			input: `SELECT "id" FROM "students" LIMIT 10 OFFSET 0`,
			want:  `SELECT COUNT(*) FROM "students"`,
		},
		{
			name:  "no FROM",
			input: `SELECT 1`,
			want:  "",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := countQuery(tt.input)
			if got != tt.want {
				t.Errorf("countQuery(%q) = %q, want %q", tt.input, got, tt.want)
			}
		})
	}
}

func TestAppendPagination(t *testing.T) {
	tests := []struct {
		name   string
		sql    string
		limit  int
		offset int
		want   string
	}{
		{
			name:   "basic",
			sql:    `SELECT * FROM "students"`,
			limit:  10,
			offset: 0,
			want:   `SELECT * FROM "students" LIMIT 10 OFFSET 0`,
		},
		{
			name:   "with offset",
			sql:    `SELECT * FROM "students"`,
			limit:  20,
			offset: 40,
			want:   `SELECT * FROM "students" LIMIT 20 OFFSET 40`,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := appendPagination(tt.sql, tt.limit, tt.offset)
			if got != tt.want {
				t.Errorf("appendPagination(%q, %d, %d) = %q, want %q",
					tt.sql, tt.limit, tt.offset, got, tt.want)
			}
		})
	}
}

func TestReadPagination_OffsetCapped(t *testing.T) {
	tests := []struct {
		name       string
		query      string
		wantLimit  int
		wantOffset int
	}{
		{
			name:       "huge offset capped at 100000",
			query:      "offset=99999999",
			wantLimit:  defaultLimit,
			wantOffset: 100000,
		},
		{
			name:       "regular offset preserved",
			query:      "offset=50",
			wantLimit:  defaultLimit,
			wantOffset: 50,
		},
		{
			name:       "no offset defaults to 0",
			query:      "",
			wantLimit:  defaultLimit,
			wantOffset: 0,
		},
		{
			name:       "negative offset defaults to 0",
			query:      "offset=-5",
			wantLimit:  defaultLimit,
			wantOffset: 0,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			req, err := http.NewRequest("GET", "/test?"+tt.query, nil)
			if err != nil {
				t.Fatal(err)
			}
			limit, offset := readPagination(req)
			if limit != tt.wantLimit {
				t.Errorf("readPagination limit = %d, want %d", limit, tt.wantLimit)
			}
			if offset != tt.wantOffset {
				t.Errorf("readPagination offset = %d, want %d", offset, tt.wantOffset)
			}
		})
	}
}

type testDB struct {
	db *sql.DB
}

func (a *testDB) QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error) {
	return a.db.QueryContext(ctx, query, args...)
}

func (a *testDB) QuoteIdentifier(name string) string {
	return `"` + name + `"`
}

func (a *testDB) TranslatePlaceholder(index int) string {
	return "?"
}

func (a *testDB) PingContext(ctx context.Context) error {
	return a.db.PingContext(ctx)
}

func TestRunCountQuery_CancelledContext(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close() //nolint:errcheck

	_, err = db.ExecContext(context.Background(), `CREATE TABLE test (id INT)`)
	if err != nil {
		t.Fatal(err)
	}

	adapter := &testDB{db: db}

	// Создаём отменённый контекст
	ctx, cancel := context.WithCancel(context.Background())
	cancel() // отменяем сразу

	// runCountQuery с отменённым контекстом должен вернуть -1
	got := runCountQuery(ctx, adapter, "SELECT COUNT(*) FROM test", nil)
	if got != -1 {
		t.Errorf("runCountQuery with cancelled context = %d, want -1", got)
	}
}

func TestRunCountQuery_Success(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close() //nolint:errcheck

	_, err = db.ExecContext(context.Background(), `
		CREATE TABLE test (id INT);
		INSERT INTO test VALUES (1), (2), (3);
	`)
	if err != nil {
		t.Fatal(err)
	}

	adapter := &testDB{db: db}

	got := runCountQuery(context.Background(), adapter, "SELECT COUNT(*) FROM test", nil)
	if got != 3 {
		t.Errorf("runCountQuery = %d, want 3", got)
	}
}

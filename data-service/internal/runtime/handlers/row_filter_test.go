package handlers

import (
	"context"
	"database/sql"
	"testing"

	"github.com/agent-tutor/agent-tutor-go/config"
	"github.com/agent-tutor/data-service/internal/runtime"
)

func TestTenantFilter(t *testing.T) {
	tests := []struct {
		name         string
		auth         *config.AuthConfig
		tenantID     string
		entityName   string
		existingArgs int
		wantWhere    string
		wantArgsLen  int
	}{
		{
			name:        "nil auth",
			auth:        nil,
			tenantID:    "t1",
			entityName:  "student",
			wantWhere:   "",
			wantArgsLen: 0,
		},
		{
			name:        "strategy none",
			auth:        &config.AuthConfig{Strategy: config.AuthStrategyNone},
			tenantID:    "t1",
			entityName:  "student",
			wantWhere:   "",
			wantArgsLen: 0,
		},
		{
			name:        "empty tenant_id",
			auth:        &config.AuthConfig{Strategy: config.AuthStrategyHeader, RowFilters: []config.RowFilter{{Entity: "student", Where: "tenant_id = :tenant_id"}}},
			tenantID:    "",
			entityName:  "student",
			wantWhere:   "",
			wantArgsLen: 0,
		},
		{
			name:        "no filter for entity",
			auth:        &config.AuthConfig{Strategy: config.AuthStrategyHeader, RowFilters: []config.RowFilter{{Entity: "teacher", Where: "tenant_id = :tenant_id"}}},
			tenantID:    "t1",
			entityName:  "student",
			wantWhere:   "",
			wantArgsLen: 0,
		},
		{
			name:         "basic row_filter — sqlite (?)",
			auth:         &config.AuthConfig{Strategy: config.AuthStrategyHeader, RowFilters: []config.RowFilter{{Entity: "student", Where: "tenant_id = :tenant_id"}}},
			tenantID:     "t1",
			entityName:   "student",
			existingArgs: 0,
			wantWhere:    "tenant_id = ?",
			wantArgsLen:  1,
		},
		{
			name:         "row_filter with existing args — postgres ($N)",
			auth:         &config.AuthConfig{Strategy: config.AuthStrategyHeader, RowFilters: []config.RowFilter{{Entity: "student", Where: "tenant_id = :tenant_id"}}},
			tenantID:     "t1",
			entityName:   "student",
			existingArgs: 2,
			wantWhere:    "tenant_id = $3",
			wantArgsLen:  1,
		},
	}

	// SQLite placeholder: ?
	sqlitePH := func(i int) string { return "?" }
	// Postgres placeholder: $N
	pgPH := func(i int) string { return string([]byte{'$', byte('0' + i)}) }

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			var translate runtime.PlaceholderFunc
			switch tt.existingArgs {
			case 0:
				translate = sqlitePH
			case 2:
				translate = pgPH
			default:
				translate = sqlitePH
			}

			where, args := tenantFilter(tt.entityName, tt.auth, tt.tenantID, tt.existingArgs, translate)
			if where != tt.wantWhere {
				t.Errorf("where = %q, want %q", where, tt.wantWhere)
			}
			if len(args) != tt.wantArgsLen {
				t.Errorf("args len = %d, want %d", tt.wantArgsLen, len(args))
			}
			if len(args) > 0 {
				if args[0] != tt.tenantID {
					t.Errorf("args[0] = %v, want %v", args[0], tt.tenantID)
				}
			}
		})
	}
}

func TestAsPlaceholderFunc_NilAdapter(t *testing.T) {
	f := asPlaceholderFunc(nil)
	got := f(1)
	want := "$1"
	if got != want {
		t.Errorf("asPlaceholderFunc(nil)(1) = %q, want %q", got, want)
	}
	got = f(42)
	want = "$42"
	if got != want {
		t.Errorf("asPlaceholderFunc(nil)(42) = %q, want %q", got, want)
	}
}

func TestAsPlaceholderFunc_SQLite(t *testing.T) {
	adapter := &mockPlaceholderAdapter{ph: "?"}
	f := asPlaceholderFunc(adapter)
	got := f(1)
	want := "?"
	if got != want {
		t.Errorf("asPlaceholderFunc(SQLite)(1) = %q, want %q", got, want)
	}
	got = f(99)
	if got != want {
		t.Errorf("asPlaceholderFunc(SQLite)(99) = %q, want %q", got, want)
	}
}

func TestAsPlaceholderFunc_Postgres(t *testing.T) {
	adapter := &mockPlaceholderAdapter{ph: "$2"}
	f := asPlaceholderFunc(adapter)
	got := f(2)
	want := "$2"
	if got != want {
		t.Errorf("asPlaceholderFunc(PG)(2) = %q, want %q", got, want)
	}
}

// mockPlaceholderAdapter implements runtime.AdapterSubset for row_filter tests
type mockPlaceholderAdapter struct {
	ph string
}

func (m *mockPlaceholderAdapter) QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error) {
	return nil, nil
}
func (m *mockPlaceholderAdapter) QuoteIdentifier(name string) string {
	return `"` + name + `"`
}
func (m *mockPlaceholderAdapter) TranslatePlaceholder(index int) string { return m.ph }
func (m *mockPlaceholderAdapter) PingContext(ctx context.Context) error { return nil }

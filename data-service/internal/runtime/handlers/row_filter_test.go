package handlers

import (
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
			switch {
			case tt.existingArgs == 0:
				translate = sqlitePH
			case tt.existingArgs == 2:
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

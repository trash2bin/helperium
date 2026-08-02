package config

import (
	"testing"
)

func TestIsValidFilterExpression(t *testing.T) {
	tests := []struct {
		name    string
		filter  string
		want    bool
	}{
		// ── Valid ──
		{"empty filter", "", true},
		{"simple equality", "column = ?", true},
		{"not equal", "column != ?", true},
		{"greater than", "column > ?", true},
		{"less than", "column < ?", true},
		{"greater equal", "column >= ?", true},
		{"less equal", "column <= ?", true},
		{"IN clause", "column IN (?, ?)", true},
		{"IS NULL", "column IS NULL", true},
		{"IS NOT NULL", "column IS NOT NULL", true},
		{"LIKE", "column LIKE ?", true},
		{"AND combination", "column = ? AND other = ?", true},
		{"OR combination", "column = ? OR other = ?", true},
		{"AND + IN combo", "column = ? AND other IN (?, ?, ?)", true},
		{"IS NULL OR equality", "column IS NULL OR column = ?", true},
		{"IS NOT NULL AND LIKE", "column IS NOT NULL AND column LIKE ?", true},

		// ── Invalid ──
		{"semicolon multi-statement", "column = ?; DROP TABLE students", false},
		{"DROP keyword", "column = ? OR 1=1 DROP TABLE x", false},
		{"INSERT keyword", "1=1; INSERT INTO x VALUES(1)", false},
		{"UPDATE keyword", "1=1 UPDATE x SET y=1", false},
		{"DELETE keyword", "DELETE FROM x", false},
		{"ALTER keyword", "ALTER TABLE x DROP y", false},
		{"CREATE keyword", "CREATE TABLE x (y int)", false},
		{"TRUNCATE keyword", "TRUNCATE TABLE x", false},
		{"EXEC keyword", "EXEC xp_cmdshell", false},
		{"EXECUTE keyword", "EXECUTE sp_who", false},
		{"UNION SELECT", "column = ? UNION SELECT * FROM x", false},
		{"SQL comment double dash", "column = ? -- comment", false},
		{"SQL comment block", "column = ? /* comment */", false},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := isValidFilterExpression(tt.filter)
			if got != tt.want {
				t.Errorf("isValidFilterExpression(%q) = %v, want %v", tt.filter, got, tt.want)
			}
		})
	}
}

func TestValidate_StatsCounterFilter_RejectsForbiddenSQL(t *testing.T) {
	// Valid config with filter containing DROP → should fail validation
	invalidJSON := []byte(`{
		"version": 1,
		"data_source": {"driver": "sqlite", "dsn": ":memory:"},
		"entities": [{"name": "users", "table": "users", "id_column": "id", "fields": [{"name": "id", "column": "id", "type": "int"}]}],
		"stats": {
			"counters": [
				{"name": "total", "entity": "users", "filter": "1=1; DROP TABLE users"}
			]
		}
	}`)
	if err := Validate(invalidJSON); err == nil {
		t.Error("expected validation error for filter with DROP, got nil")
	}

	// Valid filter should pass
	validJSON := []byte(`{
		"version": 1,
		"data_source": {"driver": "sqlite", "dsn": ":memory:"},
		"entities": [{"name": "users", "table": "users", "id_column": "id", "fields": [{"name": "id", "column": "id", "type": "int"}]}],
		"stats": {
			"counters": [
				{"name": "total", "entity": "users", "filter": "status = ?"}
			]
		}
	}`)
	if err := Validate(validJSON); err != nil {
		t.Errorf("expected no validation error for safe filter, got: %v", err)
	}

	// No filter should pass
	noFilterJSON := []byte(`{
		"version": 1,
		"data_source": {"driver": "sqlite", "dsn": ":memory:"},
		"entities": [{"name": "users", "table": "users", "id_column": "id", "fields": [{"name": "id", "column": "id", "type": "int"}]}],
		"stats": {
			"counters": [
				{"name": "total", "entity": "users"}
			]
		}
	}`)
	if err := Validate(noFilterJSON); err != nil {
		t.Errorf("expected no validation error for filterless counter, got: %v", err)
	}
}

// TestValidate_RowFilterWhere_RejectsForbiddenSQL — C6: RowFilter.Where с
// инъекцией должен валиться в Validate (как counter.Filter).
func TestValidate_RowFilterWhere_RejectsForbiddenSQL(t *testing.T) {
	invalidJSON := []byte(`{
		"version": 1,
		"data_source": {"driver": "sqlite", "dsn": ":memory:"},
		"entities": [{"name": "users", "table": "users", "id_column": "id", "fields": [{"name": "id", "column": "id", "type": "int"}]}],
		"auth": {
			"strategy": "header",
			"row_filters": [{"entity": "users", "where": "1=1; DROP TABLE users"}]
		}
	}`)
	if err := Validate(invalidJSON); err == nil {
		t.Error("expected validation error for row_filter with DROP, got nil")
	}
}

// TestValidate_RowFilterWhere_AcceptSafe — безопасный RowFilter проходит.
func TestValidate_RowFilterWhere_AcceptSafe(t *testing.T) {
	validJSON := []byte(`{
		"version": 1,
		"data_source": {"driver": "sqlite", "dsn": ":memory:"},
		"entities": [{"name": "users", "table": "users", "id_column": "id", "fields": [{"name": "id", "column": "id", "type": "int"}]}],
		"auth": {
			"strategy": "header",
			"row_filters": [{"entity": "users", "where": "tenant_id = :tenant_id"}]
		}
	}`)
	if err := Validate(validJSON); err != nil {
		t.Errorf("expected no validation error for safe row_filter, got: %v", err)
	}
}

// TestValidate_RowFilterEntity_NotFound — RowFilter на несуществующую сущность → ошибка.
func TestValidate_RowFilterEntity_NotFound(t *testing.T) {
	invalidJSON := []byte(`{
		"version": 1,
		"data_source": {"driver": "sqlite", "dsn": ":memory:"},
		"entities": [{"name": "users", "table": "users", "id_column": "id", "fields": [{"name": "id", "column": "id", "type": "int"}]}],
		"auth": {
			"strategy": "header",
			"row_filters": [{"entity": "ghosts", "where": "tenant_id = :tenant_id"}]
		}
	}`)
	if err := Validate(invalidJSON); err == nil {
		t.Error("expected validation error for row_filter on unknown entity, got nil")
	}
}

// TestValidate_HeaderAuth_RequiresRowFilterForEveryEntity — P0-1 fail-closed:
// при strategy=header КАЖДАЯ entity обязана иметь row_filter. Иначе в рантайме
// запрос к непокрытой entity вернёт 403 (мой фикс tenantFilter), что клиент
// обнаружит только в проде. Ловим на онбординге (Validate), а не в рантайме.
func TestValidate_HeaderAuth_RequiresRowFilterForEveryEntity(t *testing.T) {
	tests := []struct {
		name      string
		json      string
		wantError bool
	}{
		{
			name: "header + все entity покрыты row_filters → ok",
			json: `{
				"version": 1,
				"data_source": {"driver": "sqlite", "dsn": ":memory:"},
				"entities": [
					{"name": "users", "table": "users", "id_column": "id", "fields": [{"name": "id", "column": "id", "type": "int"}]},
					{"name": "orders", "table": "orders", "id_column": "id", "fields": [{"name": "id", "column": "id", "type": "int"}]}
				],
				"auth": {
					"strategy": "header",
					"row_filters": [
						{"entity": "users", "where": "tenant_id = :tenant_id"},
						{"entity": "orders", "where": "tenant_id = :tenant_id"}
					]
				}
			}`,
			wantError: false,
		},
		{
			name: "header + entity БЕЗ row_filter → error (fail at onboarding)",
			json: `{
				"version": 1,
				"data_source": {"driver": "sqlite", "dsn": ":memory:"},
				"entities": [
					{"name": "users", "table": "users", "id_column": "id", "fields": [{"name": "id", "column": "id", "type": "int"}]},
					{"name": "orders", "table": "orders", "id_column": "id", "fields": [{"name": "id", "column": "id", "type": "int"}]}
				],
				"auth": {
					"strategy": "header",
					"row_filters": [
						{"entity": "users", "where": "tenant_id = :tenant_id"}
					]
				}
			}`,
			wantError: true,
		},
		{
			name: "header + вообще без row_filters → error",
			json: `{
				"version": 1,
				"data_source": {"driver": "sqlite", "dsn": ":memory:"},
				"entities": [
					{"name": "users", "table": "users", "id_column": "id", "fields": [{"name": "id", "column": "id", "type": "int"}]}
				],
				"auth": {"strategy": "header"}
			}`,
			wantError: true,
		},
		{
			name: "strategy=none + без row_filters → ok (single-tenant)",
			json: `{
				"version": 1,
				"data_source": {"driver": "sqlite", "dsn": ":memory:"},
				"entities": [
					{"name": "users", "table": "users", "id_column": "id", "fields": [{"name": "id", "column": "id", "type": "int"}]}
				],
				"auth": {"strategy": "none"}
			}`,
			wantError: false,
		},
		{
			name: "auth отсутствует → ok (no multi-tenancy)",
			json: `{
				"version": 1,
				"data_source": {"driver": "sqlite", "dsn": ":memory:"},
				"entities": [
					{"name": "users", "table": "users", "id_column": "id", "fields": [{"name": "id", "column": "id", "type": "int"}]}
				]
			}`,
			wantError: false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := Validate([]byte(tt.json))
			if tt.wantError && err == nil {
				t.Error("expected validation error (entity without row_filter under header-auth), got nil")
			}
			if !tt.wantError && err != nil {
				t.Errorf("expected no validation error, got: %v", err)
			}
		})
	}
}

// TestValidate_AuthStrategyNone_DoesNotRequireRowFilters — пункт 1 ревью:
// инвариант в Validate() — «auth задан и strategy != none» (не «== header»).
// Единственная валидная не-none стратегия сегодня — header (покрыта
// TestValidate_HeaderAuth_*). Этот тест фиксирует границу инварианта:
// none освобождает от row_filters, всё остальное (в будущем jwt/api_key) —
// нет. См. types.go: if c.Auth.Strategy != AuthStrategyNone.
func TestValidate_AuthStrategyNone_DoesNotRequireRowFilters(t *testing.T) {
	// none + entity без row_filter → ok (single-tenant, изоляция не включена)
	noneNoFilter := `{
		"version": 1,
		"data_source": {"driver": "sqlite", "dsn": ":memory:"},
		"entities": [{"name": "users", "table": "users", "id_column": "id", "fields": [{"name": "id", "column": "id", "type": "int"}]}],
		"auth": {"strategy": "none"}
	}`
	if err := Validate([]byte(noneNoFilter)); err != nil {
		t.Errorf("strategy=none without row_filters should be valid (single-tenant), got: %v", err)
	}

	// header + entity без row_filter → error (уже покрыто, но подтверждаем контраст)
	headerNoFilter := `{
		"version": 1,
		"data_source": {"driver": "sqlite", "dsn": ":memory:"},
		"entities": [{"name": "users", "table": "users", "id_column": "id", "fields": [{"name": "id", "column": "id", "type": "int"}]}],
		"auth": {"strategy": "header"}
	}`
	if err := Validate([]byte(headerNoFilter)); err == nil {
		t.Error("strategy=header without row_filters must be invalid (fail-closed invariant), got nil")
	}
}

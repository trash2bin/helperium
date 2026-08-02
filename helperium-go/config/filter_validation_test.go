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

package server_test

import (
	"strings"
	"testing"

	"github.com/agent-tutor/agent-tutor-go/config"
	"github.com/agent-tutor/data-service/internal/server"
)

// TestNewRouterFromConfig_InvalidEntity — op=list без entity → ошибка
func TestNewRouterFromConfig_InvalidEntity(t *testing.T) {
	cfg := &config.Config{
		Version: 1,
		DataSource: config.DataSourceConfig{
			Driver: "sqlite",
			DSN:    ":memory:",
		},
		Endpoints: []config.Endpoint{
			{
				Path:   "/students",
				Op:     "list",
				Method: "GET",
				// Entity is empty — should trigger error
			},
		},
	}

	// TenantStore nil, adapter nil, db nil — мы тестируем только раннюю валидацию
	_, err := server.NewRouterFromConfig(nil, cfg, nil, nil, nil, "", nil)
	if err == nil {
		t.Fatal("expected error for list with empty entity, got nil")
	}
	if !strings.Contains(err.Error(), "requires entity") {
		t.Errorf("expected error about missing entity, got: %v", err)
	}
}

// TestNewRouterFromConfig_UnsupportedOp — неизвестная операция → ошибка
func TestNewRouterFromConfig_UnsupportedOp(t *testing.T) {
	cfg := &config.Config{
		Version: 1,
		DataSource: config.DataSourceConfig{
			Driver: "sqlite",
			DSN:    ":memory:",
		},
		Endpoints: []config.Endpoint{
			{
				Path:   "/custom",
				Op:     "delete_all",
				Method: "GET",
			},
		},
	}

	_, err := server.NewRouterFromConfig(nil, cfg, nil, nil, nil, "", nil)
	if err == nil {
		t.Fatal("expected error for unsupported op, got nil")
	}
	if !strings.Contains(err.Error(), "unsupported op") {
		t.Errorf("expected error about unsupported op, got: %v", err)
	}
}

// TestNewRouterFromConfig_InvalidMethod — неизвестный HTTP метод → ошибка
func TestNewRouterFromConfig_InvalidMethod(t *testing.T) {
	cfg := &config.Config{
		Version: 1,
		DataSource: config.DataSourceConfig{
			Driver: "sqlite",
			DSN:    ":memory:",
		},
		Endpoints: []config.Endpoint{
			{
				Path:   "/students",
				Op:     "list",
				Entity: "student",
				Method: "OPTIONS", // Unsupported method
			},
		},
		Entities: []config.Entity{
			{
				Name: "student", Table: "students", IDColumn: "id",
				Fields: []config.EntityField{
					{Name: "id", Column: "id", Type: "int", PrimaryKey: boolPtrT(true)},
				},
			},
		},
	}

	_, err := server.NewRouterFromConfig(nil, cfg, nil, nil, nil, "", nil)
	if err == nil {
		t.Fatal("expected error for unsupported method, got nil")
	}
	if !strings.Contains(err.Error(), "unsupported method") {
		t.Errorf("expected error about unsupported method, got: %v", err)
	}
}

// TestNewRouterFromConfig_CustomQueryNoQueryID — op=custom_query без query_id → ошибка
func TestNewRouterFromConfig_CustomQueryNoQueryID(t *testing.T) {
	cfg := &config.Config{
		Version: 1,
		DataSource: config.DataSourceConfig{
			Driver: "sqlite",
			DSN:    ":memory:",
		},
		Endpoints: []config.Endpoint{
			{
				Path:   "/custom",
				Op:     "custom_query",
				Method: "GET",
				// QueryID is empty — should trigger error
			},
		},
	}

	_, err := server.NewRouterFromConfig(nil, cfg, nil, nil, nil, "", nil)
	if err == nil {
		t.Fatal("expected error for custom_query without query_id, got nil")
	}
	if !strings.Contains(err.Error(), "requires query_id") {
		t.Errorf("expected error about missing query_id, got: %v", err)
	}
}

// TestNewRouterFromConfig_DuplicateEntity — дубликаты сущностей → ошибка
func TestNewRouterFromConfig_DuplicateEntity(t *testing.T) {
	cfg := &config.Config{
		Version: 1,
		DataSource: config.DataSourceConfig{
			Driver: "sqlite",
			DSN:    ":memory:",
		},
		Endpoints: []config.Endpoint{
			{
				Path:   "/test",
				Op:     "list",
				Entity: "dup",
				Method: "GET",
			},
		},
		Entities: []config.Entity{
			{
				Name: "dup", Table: "dups1", IDColumn: "id",
				Fields: []config.EntityField{
					{Name: "id", Column: "id", Type: "int", PrimaryKey: boolPtrT(true)},
				},
			},
			{
				Name: "dup", Table: "dups2", IDColumn: "id",
				Fields: []config.EntityField{
					{Name: "id", Column: "id", Type: "int", PrimaryKey: boolPtrT(true)},
				},
			},
		},
	}

	_, err := server.NewRouterFromConfig(nil, cfg, nil, nil, nil, "", nil)
	if err == nil {
		t.Fatal("expected error for duplicate entity, got nil")
	}
	if !strings.Contains(err.Error(), "duplicate") {
		t.Errorf("expected error about duplicate entity, got: %v", err)
	}
}

// TestNewRouterFromConfig_FindNoEntity — op=find без entity → ошибка
func TestNewRouterFromConfig_FindNoEntity(t *testing.T) {
	cfg := &config.Config{
		Version: 1,
		DataSource: config.DataSourceConfig{
			Driver: "sqlite",
			DSN:    ":memory:",
		},
		Endpoints: []config.Endpoint{
			{
				Path:       "/students/find",
				Op:         "find",
				Method:     "GET",
				SearchField: "name",
			},
		},
	}

	_, err := server.NewRouterFromConfig(nil, cfg, nil, nil, nil, "", nil)
	if err == nil {
		t.Fatal("expected error for find with empty entity, got nil")
	}
	if !strings.Contains(err.Error(), "requires entity") {
		t.Errorf("expected error about missing entity, got: %v", err)
	}
}

// TestNewRouterFromConfig_GetByIDNoEntity — op=get_by_id без entity → ошибка
func TestNewRouterFromConfig_GetByIDNoEntity(t *testing.T) {
	cfg := &config.Config{
		Version: 1,
		DataSource: config.DataSourceConfig{
			Driver: "sqlite",
			DSN:    ":memory:",
		},
		Endpoints: []config.Endpoint{
			{
				Path:   "/students/{id}",
				Op:     "get_by_id",
				Method: "GET",
			},
		},
	}

	_, err := server.NewRouterFromConfig(nil, cfg, nil, nil, nil, "", nil)
	if err == nil {
		t.Fatal("expected error for get_by_id with empty entity, got nil")
	}
	if !strings.Contains(err.Error(), "requires entity") {
		t.Errorf("expected error about missing entity, got: %v", err)
	}
}

func boolPtrT(b bool) *bool { return &b }
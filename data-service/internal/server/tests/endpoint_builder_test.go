package server_test

import (
	"strings"
	"testing"

	"github.com/trash2bin/helperium/data-service/internal/server"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// TestNewRouterFromConfig_InvalidEntity — strategy без entity → ошибка
func TestNewRouterFromConfig_InvalidEntity(t *testing.T) {
	cfg := &config.Config{
		Version: 1,
		DataSource: config.DataSourceConfig{
			Driver: "sqlite",
			DSN:    ":memory:",
		},
		Endpoints: []config.Endpoint{
			{
				Path:     "/students",
				Op:       "strategy",
				Strategy: "grep",
				Method:   "GET",
				// Entity is empty — should trigger error
			},
		},
	}

	// TenantStore nil, adapter nil, db nil — мы тестируем только раннюю валидацию
	_, err := server.NewRouterFromConfig(nil, cfg, nil, nil)
	if err == nil {
		t.Fatal("expected error for strategy with empty entity, got nil")
	}
	if !strings.Contains(err.Error(), "not found for strategy") {
		t.Errorf("expected error about missing entity for strategy, got: %v", err)
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

	_, err := server.NewRouterFromConfig(nil, cfg, nil, nil)
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
				Path:     "/students",
				Op:       "strategy",
				Strategy: "grep",
				Entity:   "student",
				Method:   "OPTIONS", // Unsupported method
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

	_, err := server.NewRouterFromConfig(nil, cfg, nil, nil)
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

	_, err := server.NewRouterFromConfig(nil, cfg, nil, nil)
	if err == nil {
		t.Fatal("expected error for custom_query without query_id, got nil")
	}
	if !strings.Contains(err.Error(), "requires query_id") {
		t.Errorf("expected error about missing query_id, got: %v", err)
	}
}

// TestNewRouterFromConfig_StrategyNoEntity — strategy с Strategy, но без Entity → ошибка
func TestNewRouterFromConfig_StrategyNoEntity(t *testing.T) {
	cfg := &config.Config{
		Version: 1,
		DataSource: config.DataSourceConfig{
			Driver: "sqlite",
			DSN:    ":memory:",
		},
		Endpoints: []config.Endpoint{
			{
				Path:     "/students/search",
				Op:       "strategy",
				Strategy: "grep",
				Method:   "GET",
				// Entity is empty — should trigger error
			},
		},
	}

	_, err := server.NewRouterFromConfig(nil, cfg, nil, nil)
	if err == nil {
		t.Fatal("expected error for strategy with empty entity, got nil")
	}
	if !strings.Contains(err.Error(), "not found for strategy") {
		t.Errorf("expected error about missing entity for strategy, got: %v", err)
	}
}

// TestNewRouterFromConfig_StrategyUnknown — strategy с неизвестным именем → ошибка
func TestNewRouterFromConfig_StrategyUnknown(t *testing.T) {
	cfg := &config.Config{
		Version: 1,
		DataSource: config.DataSourceConfig{
			Driver: "sqlite",
			DSN:    ":memory:",
		},
		Endpoints: []config.Endpoint{
			{
				Path:     "/students",
				Op:       "strategy",
				Strategy: "nonexistent_strategy",
				Entity:   "student",
				Method:   "GET",
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

	_, err := server.NewRouterFromConfig(nil, cfg, nil, nil)
	if err == nil {
		t.Fatal("expected error for unknown strategy, got nil")
	}
	if !strings.Contains(err.Error(), "unknown strategy") {
		t.Errorf("expected error about unknown strategy, got: %v", err)
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

	_, err := server.NewRouterFromConfig(nil, cfg, nil, nil)
	if err == nil {
		t.Fatal("expected error for get_by_id with empty entity, got nil")
	}
	if !strings.Contains(err.Error(), "requires entity") {
		t.Errorf("expected error about missing entity, got: %v", err)
	}
}

func boolPtrT(b bool) *bool { return &b }

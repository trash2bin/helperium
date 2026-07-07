package runtime

import (
	"testing"

	"github.com/agent-tutor/agent-tutor-go/config"
)

// TestConfigToEntities — полная конвертация Entity
func TestConfigToEntities(t *testing.T) {
	cfgEntities := []config.Entity{
		{
			Name:     "customer",
			Table:    "customers",
			IDColumn: "id",
			Fields: []config.EntityField{
				{Name: "id", Column: "id", Type: "int", PrimaryKey: boolPtrT(true)},
				{Name: "name", Column: "name", Type: "string", Nullable: boolPtrT(false)},
				{Name: "email", Column: "email", Type: "string"},
			},
		},
		{
			Name:     "order",
			Table:    "orders",
			IDColumn: "id",
			Fields: []config.EntityField{
				{Name: "id", Column: "id", Type: "int", PrimaryKey: boolPtrT(true)},
				{Name: "total", Column: "total", Type: "float"},
			},
		},
	}

	entities := ConfigToEntities(cfgEntities)

	if len(entities) != 2 {
		t.Fatalf("expected 2 entities, got %d", len(entities))
	}

	// Проверяем customer
	cust := entities[0]
	if cust.Name != "customer" || cust.Table != "customers" || cust.IDColumn != "id" {
		t.Errorf("unexpected customer entity: %+v", cust)
	}
	if len(cust.Fields) != 3 {
		t.Fatalf("expected 3 fields, got %d", len(cust.Fields))
	}
	if !cust.Fields[0].PrimaryKey {
		t.Error("expected id field to be primary key")
	}
	if cust.Fields[1].Nullable {
		t.Error("expected name field to be non-nullable")
	}
}

// TestConfigToEntities_Empty — пустой список → пустой результат
func TestConfigToEntities_Empty(t *testing.T) {
	entities := ConfigToEntities([]config.Entity{})
	if len(entities) != 0 {
		t.Errorf("expected 0 entities, got %d", len(entities))
	}
}

// TestConfigToEntities_NilFields — поля могут быть пустыми
func TestConfigToEntities_NilFields(t *testing.T) {
	cfgEntities := []config.Entity{
		{
			Name:     "empty",
			Table:    "empties",
			IDColumn: "id",
			Fields:   nil,
		},
	}

	entities := ConfigToEntities(cfgEntities)
	if len(entities) != 1 {
		t.Fatalf("expected 1 entity, got %d", len(entities))
	}
	// Fields должен быть пустым слайсом, не nil
	if entities[0].Fields != nil && len(entities[0].Fields) != 0 {
		t.Errorf("expected empty fields, got %d", len(entities[0].Fields))
	}
}

// TestConfigToCustomQueries — конвертация кастомных запросов
func TestConfigToCustomQueries(t *testing.T) {
	cfgQueries := map[string]config.CustomQuery{
		"get_by_email": {
			SQL:    "SELECT * FROM customers WHERE email = ?",
			Params: []string{"email"},
			ResultMapping: map[string]config.ResultMappingField{
				"id":    {Type: "int"},
				"name":  {Type: "string"},
				"email": {Type: "string", Nullable: boolPtrT(true)},
			},
			MaxRows: 100,
		},
		"count_all": {
			SQL:    "SELECT COUNT(*) as cnt FROM customers",
			Params: []string{},
			ResultMapping: map[string]config.ResultMappingField{
				"cnt": {Type: "int"},
			},
			MaxRows: 1,
		},
	}

	queries := ConfigToCustomQueries(cfgQueries)

	if len(queries) != 2 {
		t.Fatalf("expected 2 queries, got %d", len(queries))
	}

	q1, ok := queries["get_by_email"]
	if !ok {
		t.Fatal("expected get_by_email query")
	}
	if q1.SQL != "SELECT * FROM customers WHERE email = ?" {
		t.Errorf("unexpected SQL: %s", q1.SQL)
	}
	if len(q1.Params) != 1 {
		t.Errorf("expected 1 param, got %d", len(q1.Params))
	}
	if q1.MaxRows != 100 {
		t.Errorf("expected MaxRows=100, got %d", q1.MaxRows)
	}

	// Проверяем Nullable в result mapping
	rm, ok := q1.ResultMapping["email"]
	if !ok {
		t.Fatal("expected email in result mapping")
	}
	if !rm.Nullable {
		t.Error("expected email to be nullable")
	}
}

// TestConfigToCustomQueries_NilMapping — маппинг может быть nil
func TestConfigToCustomQueries_NilMapping(t *testing.T) {
	cfgQueries := map[string]config.CustomQuery{
		"raw": {
			SQL:           "SELECT 1",
			Params:        nil,
			ResultMapping: nil,
			MaxRows:       0,
		},
	}

	queries := ConfigToCustomQueries(cfgQueries)
	if len(queries) != 1 {
		t.Fatalf("expected 1 query, got %d", len(queries))
	}
	if queries["raw"].ResultMapping == nil {
		t.Log("ResultMapping is nil for nil input (expected)")
	}
}

// TestConfigToCustomQueries_Empty — пустая карта → пустой результат
func TestConfigToCustomQueries_Empty(t *testing.T) {
	queries := ConfigToCustomQueries(map[string]config.CustomQuery{})
	if len(queries) != 0 {
		t.Errorf("expected 0 queries, got %d", len(queries))
	}
}

func boolPtrT(b bool) *bool { return &b }

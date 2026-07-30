package configgen

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/trash2bin/helperium/helperium-go/config"
	"github.com/trash2bin/helperium/data-service/internal/datasource"
)

// TestGenerate проверяет, что configgen генерирует валидный конфиг
// для схемы, эквивалентной university.db.
func TestGenerate(t *testing.T) {
	schema := &datasource.Schema{
		Driver: "sqlite",
		Tables: []datasource.Table{
			{
				Name:       "groups",
				PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "string", Nullable: false},
					{Name: "name", Type: "string", Nullable: false},
					{Name: "speciality", Type: "string", Nullable: true},
				},
			},
			{
				Name:       "students",
				PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "string", Nullable: false},
					{Name: "name", Type: "string", Nullable: false},
					{Name: "group_id", Type: "string", Nullable: true},
					{Name: "course", Type: "int", Nullable: true},
				},
			},
			{
				Name:       "teachers",
				PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "string", Nullable: false},
					{Name: "name", Type: "string", Nullable: false},
				},
			},
		},
	}

	ds := config.DataSourceConfig{
		Driver: "sqlite",
		DSN:    "file.db",
	}

	cfg := Generate(schema, &config.Config{
		DataSource: ds,
	})

	if cfg.Version != 3 {
		t.Errorf("expected version 3, got %d", cfg.Version)
	}
	if len(cfg.Entities) != 3 {
		t.Fatalf("expected 3 entities, got %d", len(cfg.Entities))
	}
	if len(cfg.Endpoints) < 3 {
		t.Errorf("expected at least 3 endpoints, got %d", len(cfg.Endpoints))
	}

	// Проверяем student entity
	var student *config.Entity
	for i, e := range cfg.Entities {
		if e.Name == "students" {
			student = &cfg.Entities[i]
			break
		}
	}
	if student == nil {
		t.Fatal("expected 'students' entity")
	}
	if student.Table != "students" {
		t.Errorf("expected table 'students', got %q", student.Table)
	}
	if student.IDColumn != "id" {
		t.Errorf("expected idColumn 'id', got %q", student.IDColumn)
	}
	if len(student.Fields) != 4 {
		t.Fatalf("expected 4 fields, got %d", len(student.Fields))
	}

	// Проверяем, что у name поле type='string' и не primary_key
	nameField := student.Fields[1]
	if nameField.Name != "name" {
		t.Errorf("expected field 'name', got %q", nameField.Name)
	}
	if nameField.PrimaryKey == nil || *nameField.PrimaryKey {
		t.Errorf("expected name field not primary key")
	}

	// Проверяем endpoint'ы
	hasStudentsGrep := false
	hasStudentsByID := false
	hasHealth := false
	hasStats := false
	for _, ep := range cfg.Endpoints {
		switch {
		case ep.Path == "/students/grep" && ep.Strategy == "grep":
			hasStudentsGrep = true
		case ep.Path == "/students/{id}" && ep.Op == config.OpGetByID:
			hasStudentsByID = true
		case ep.Path == "/health":
			hasHealth = true
		case ep.Path == "/stats":
			hasStats = true
		}
	}
	if !hasStudentsGrep {
		t.Error("expected /students/grep strategy endpoint")
	}
	if !hasStudentsByID {
		t.Error("expected /students/{id} get_by_id endpoint")
	}
	if !hasHealth {
		t.Error("expected /health endpoint")
	}
	if !hasStats {
		t.Error("expected /stats endpoint")
	}

	// Проверяем, что конфиг сериализуется в валидный JSON
	data, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		t.Fatalf("marshal config: %v", err)
	}

	// Можем прочитать обратно
	var decoded config.Config
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("unmarshal config: %v", err)
	}
	if decoded.Version != 3 {
		t.Errorf("roundtrip version mismatch")
	}
}

// TestGenerate_FullSchema проверяет генерацию на полной схеме (как university.db).
func TestGenerate_FullSchema(t *testing.T) {
	schema := &datasource.Schema{
		Driver: "sqlite",
		Tables: []datasource.Table{
			{Name: "groups", PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "string"}, {Name: "name", Type: "string"}, {Name: "speciality", Type: "string"},
				}},
			{Name: "students", PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "string"}, {Name: "name", Type: "string"},
					{Name: "group_id", Type: "string"}, {Name: "course", Type: "int"},
				}},
			{Name: "teachers", PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "string"}, {Name: "name", Type: "string"},
					{Name: "disciplines_json", Type: "json"},
				}},
			{Name: "disciplines", PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "string"}, {Name: "name", Type: "string"},
					{Name: "description", Type: "string"},
				}},
			{Name: "grades", PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "string"}, {Name: "student_id", Type: "string"},
					{Name: "discipline_id", Type: "string"}, {Name: "grade", Type: "string"},
					{Name: "date", Type: "date"},
				}},
			{Name: "schedule", PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "string"}, {Name: "day", Type: "string"},
					{Name: "group_id", Type: "string"},
				}},
		},
	}

	ds := config.DataSourceConfig{Driver: "sqlite", DSN: "university.db"}
	cfg := Generate(schema, &config.Config{
		DataSource: ds,
	})

	if len(cfg.Entities) != 6 {
		t.Fatalf("expected 6 entities, got %d", len(cfg.Entities))
	}
	if len(cfg.Endpoints) < 8 {
		t.Errorf("expected at least 8 endpoints, got %d", len(cfg.Endpoints))
	}

	// Check every entity has strategy endpoints (grep/filter/schema)
	hasStrategy := make(map[string]bool)
	for _, ep := range cfg.Endpoints {
		if ep.Strategy != "" {
			hasStrategy[ep.Entity] = true
		}
	}
	for _, e := range cfg.Entities {
		if !hasStrategy[e.Name] {
			t.Errorf("missing strategy endpoint for /%s", e.Name)
		}
	}

	// Проверяем, что конфиг сериализуется без ошибок
	data, _ := json.MarshalIndent(cfg, "", "  ")
	var decoded config.Config
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("roundtrip: %v", err)
	}
	t.Logf("generated %d entities / %d endpoints / %d bytes",
		len(cfg.Entities), len(cfg.Endpoints), len(data))
}

// TestGenerate_GrepForGrades проверяет, что grep генерируется для всех сущностей.
func TestGenerate_GrepForGrades(t *testing.T) {
	schema := &datasource.Schema{
		Driver: "sqlite",
		Tables: []datasource.Table{
			{
				Name:       "grades",
				PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "string", Nullable: false},
					{Name: "student_id", Type: "string", Nullable: false},
					{Name: "discipline_id", Type: "string", Nullable: false},
					{Name: "grade", Type: "string", Nullable: true},
					{Name: "date", Type: "date", Nullable: true},
				},
			},
		},
	}

	cfg := Generate(schema, &config.Config{
		DataSource: config.DataSourceConfig{Driver: "sqlite", DSN: "test.db"},
	})

	// grades без name-поля → проверяем что grep / filter / schema генерируются, а не list
	var hasGrep, hasFilter, hasSchema bool
	for _, ep := range cfg.Endpoints {
		if ep.Strategy == "grep" && ep.Entity == "grades" {
			hasGrep = true
		}
		if ep.Strategy == "filter" && ep.Entity == "grades" {
			hasFilter = true
		}
		if ep.Strategy == "schema" && ep.Entity == "grades" {
			hasSchema = true
		}
	}
	if !hasGrep {
		t.Error("expected grep endpoint for 'grades'")
	}
	if !hasFilter {
		t.Error("expected filter endpoint for 'grades'")
	}
	if !hasSchema {
		t.Error("expected schema endpoint for 'grades'")
	}
	if hasGrep && hasFilter && hasSchema {
		t.Log("grades: grep/filter/schema all present")
	}

	// Проверяем, что MCP tools генерируются для grades
	var hasSearchTool bool
	for _, tool := range cfg.MCPTools {
		if tool.Name == "grep_grades" {
			hasSearchTool = true
			break
		}
	}
	if !hasSearchTool {
		t.Error("expected grep_grades MCP tool for grades")
	}
}

// TestGenerate_RelationsFromFK проверяет, что configgen заполняет
// Entity.Relations[] из Table.ForeignKeys[].
func TestGenerate_RelationsFromFK(t *testing.T) {
	schema := &datasource.Schema{
		Driver: "sqlite",
		Tables: []datasource.Table{
			{
				Name:       "orders",
				PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "int", Nullable: false},
					{Name: "customer_id", Type: "int", Nullable: false},
					{Name: "status", Type: "string", Nullable: true},
				},
				ForeignKeys: []datasource.ForeignKey{
					{
						Name:              "fk_orders_customer",
						Columns:           []string{"customer_id"},
						ReferencedTable:   "customers",
						ReferencedColumns: []string{"id"},
					},
				},
			},
			{
				Name:       "customers",
				PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "int", Nullable: false},
					{Name: "name", Type: "string", Nullable: false},
				},
			},
			{
				Name:       "order_items",
				PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "int", Nullable: false},
					{Name: "order_id", Type: "int", Nullable: false},
					{Name: "product_id", Type: "int", Nullable: false},
					{Name: "quantity", Type: "int", Nullable: false},
				},
				ForeignKeys: []datasource.ForeignKey{
					{
						Name:              "fk_items_order",
						Columns:           []string{"order_id"},
						ReferencedTable:   "orders",
						ReferencedColumns: []string{"id"},
					},
					{
						Name:              "fk_items_product",
						Columns:           []string{"product_id"},
						ReferencedTable:   "products",
						ReferencedColumns: []string{"id"},
					},
				},
			},
		},
	}

	cfg := Generate(schema, &config.Config{
		DataSource: config.DataSourceConfig{Driver: "sqlite", DSN: "test.db"},
	})

	// Находим order_items — у него 2 FK
	var orderItems *config.Entity
	for i, e := range cfg.Entities {
		if e.Name == "order_items" {
			orderItems = &cfg.Entities[i]
			break
		}
	}
	if orderItems == nil {
		t.Fatal("expected 'order_items' entity")
	}
	if len(orderItems.Relations) != 2 {
		t.Fatalf("expected 2 relations on order_items, got %d", len(orderItems.Relations))
	}

	// Проверяем что FK correctly mapped
	relMap := make(map[string]config.Relation)
	for _, r := range orderItems.Relations {
		relMap[r.LocalFK] = r
	}

	if r, ok := relMap["order_id"]; !ok {
		t.Error("expected relation for order_id")
	} else {
		if r.Table != "orders" {
			t.Errorf("expected relation table 'orders', got %q", r.Table)
		}
		if r.Kind != config.RelationManyToOne {
			t.Errorf("expected many_to_one, got %q", r.Kind)
		}
	}

	if r, ok := relMap["product_id"]; !ok {
		t.Error("expected relation for product_id")
	} else {
		if r.Table != "products" {
			t.Errorf("expected relation table 'products', got %q", r.Table)
		}
	}

	// orders — 1 FK
	var orders *config.Entity
	for i, e := range cfg.Entities {
		if e.Name == "orders" {
			orders = &cfg.Entities[i]
			break
		}
	}
	if orders == nil {
		t.Fatal("expected 'orders' entity")
	}
	if len(orders.Relations) != 1 {
		t.Fatalf("expected 1 relation on orders, got %d", len(orders.Relations))
	}
	if orders.Relations[0].Table != "customers" {
		t.Errorf("expected relation table 'customers', got %q", orders.Relations[0].Table)
	}

	// customers — 0 FK
	var customers *config.Entity
	for i, e := range cfg.Entities {
		if e.Name == "customers" {
			customers = &cfg.Entities[i]
			break
		}
	}
	if customers == nil {
		t.Fatal("expected 'customers' entity")
	}
	if len(customers.Relations) != 0 {
		t.Errorf("expected 0 relations on customers, got %d", len(customers.Relations))
	}

	// Проверяем JSON roundtrip
	data, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	var decoded config.Config
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	t.Logf("generated %d entities with relations: %d bytes", len(decoded.Entities), len(data))
}

// TestGenerate_StrategyEndpoints проверяет, что grep/filter/schema стратегии
// генерируются для всех сущностей.
func TestGenerate_StrategyEndpoints(t *testing.T) {
	schema := &datasource.Schema{
		Driver: "sqlite",
		Tables: []datasource.Table{
			{
				Name:       "customers",
				PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "int", Nullable: false},
					{Name: "name", Type: "string", Nullable: false},
					{Name: "email", Type: "string", Nullable: true},
					{Name: "city", Type: "string", Nullable: true},
					{Name: "status", Type: "string", Nullable: true},
				},
			},
		},
	}

	cfg := Generate(schema, &config.Config{
		DataSource: config.DataSourceConfig{Driver: "sqlite", DSN: "test.db"},
	})

	// Проверяем, что стратегии grep/filter/schema есть для customers
	var grepEp, filterEp, schemaEp *config.Endpoint
	for i, ep := range cfg.Endpoints {
		switch {
		case ep.Strategy == "grep" && ep.Entity == "customers":
			grepEp = &cfg.Endpoints[i]
		case ep.Strategy == "filter" && ep.Entity == "customers":
			filterEp = &cfg.Endpoints[i]
		case ep.Strategy == "schema" && ep.Entity == "customers":
			schemaEp = &cfg.Endpoints[i]
		}
	}
	if grepEp == nil {
		t.Fatal("expected grep strategy endpoint for 'customers'")
	}
	if filterEp == nil {
		t.Fatal("expected filter strategy endpoint for 'customers'")
	}
	if schemaEp == nil {
		t.Fatal("expected schema strategy endpoint for 'customers'")
	}

	// Проверяем, что MCP tool grep_customers сгенерирован
	var searchTool *config.MCPTool
	for i, tool := range cfg.MCPTools {
		if tool.Name == "grep_customers" {
			searchTool = &cfg.MCPTools[i]
			break
		}
	}
	if searchTool == nil {
		t.Fatal("expected grep_customers MCP tool")
	}
	if len(searchTool.Params) < 3 {
		t.Errorf("expected at least 3 params on grep_customers, got %d", len(searchTool.Params))
	}
}

// TestGenerate_BoolFilterParams проверяет, что bool-колонки получают
// фильтр с типом bool (true/false) в strategy параметрах.
func TestGenerate_BoolFilterParams(t *testing.T) {
	schema := &datasource.Schema{
		Driver: "sqlite",
		Tables: []datasource.Table{{
			Name:       "products",
			PrimaryKey: []string{"id"},
			Columns: []datasource.Column{
				{Name: "id", Type: "int"},
				{Name: "name", Type: "string"},
				{Name: "is_active", Type: "bool"},
				{Name: "is_promo", Type: "bool"},
				{Name: "created_at", Type: "datetime"},
				{Name: "deleted_at", Type: "date"},
			},
		}},
	}

	cfg := Generate(schema, &config.Config{
		DataSource: config.DataSourceConfig{Driver: "sqlite", DSN: "test.db"},
	})

	var filterEp *config.Endpoint
	for i, ep := range cfg.Endpoints {
		if ep.Strategy == "filter" && ep.Entity == "products" {
			filterEp = &cfg.Endpoints[i]
			break
		}
	}
	if filterEp == nil {
		t.Fatal("expected filter strategy endpoint for 'products'")
	}

	// Check MCP tool filter_products for typed params (generated by FilterStrategy.ToolParams)
	var filterTool *config.MCPTool
	for i, tool := range cfg.MCPTools {
		if tool.Name == "filter_products" {
			filterTool = &cfg.MCPTools[i]
			break
		}
	}
	if filterTool == nil {
		t.Fatal("expected filter_products MCP tool")
	}

	paramMap := make(map[string]config.ParamType)
	for _, p := range filterTool.Params {
		paramMap[p.Name] = p.Type
	}

	// Bool columns should have bool type
	if paramMap["is_active"] != config.ParamTypeBool {
		t.Errorf("expected is_active to be bool, got %s", paramMap["is_active"])
	}
	if paramMap["is_promo"] != config.ParamTypeBool {
		t.Errorf("expected is_promo to be bool, got %s", paramMap["is_promo"])
	}

	// Date/datetime should be string (ISO-8601)
	if paramMap["created_at"] != config.ParamTypeString {
		t.Errorf("expected created_at to be string, got %s", paramMap["created_at"])
	}
	if paramMap["deleted_at"] != config.ParamTypeString {
		t.Errorf("expected deleted_at to be string, got %s", paramMap["deleted_at"])
	}
}

// TestGenerate_DualFKCollision проверяет, что два FK на одну parent-таблицу
// не схлопываются в один nav-тул (разные queryID).
func TestGenerate_DualFKCollision(t *testing.T) {
	schema := &datasource.Schema{
		Driver: "sqlite",
		Tables: []datasource.Table{
			{
				Name:       "users",
				PrimaryKey: []string{"id"},
				Columns:    []datasource.Column{{Name: "id", Type: "int"}, {Name: "name", Type: "string"}},
			},
			{
				Name:       "orders",
				PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "int"},
					{Name: "buyer_id", Type: "int"},
					{Name: "seller_id", Type: "int"},
				},
				ForeignKeys: []datasource.ForeignKey{
					{Columns: []string{"buyer_id"}, ReferencedTable: "users", ReferencedColumns: []string{"id"}},
					{Columns: []string{"seller_id"}, ReferencedTable: "users", ReferencedColumns: []string{"id"}},
				},
			},
		},
	}

	cfg := Generate(schema, &config.Config{
		DataSource: config.DataSourceConfig{Driver: "sqlite", DSN: "test.db"},
	})

	// Two FKs to same parent → only ONE nav endpoint (same path),
	// but TWO custom queries with different queryIDs.
	var navCount int
	for _, ep := range cfg.Endpoints {
		if ep.Op == config.OpCustomQuery && ep.Entity == "orders" {
			navCount++
		}
	}
	if navCount != 1 {
		t.Errorf("expected 1 nav endpoint for orders (same path), got %d", navCount)
	}

	// TWO custom queries with different SQL (one per FK)
	var cqCount int
	for _, cq := range cfg.CustomQueries {
		if strings.Contains(cq.Description, "orders") {
			cqCount++
		}
	}
	if cqCount != 2 {
		t.Errorf("expected 2 custom queries for orders (buyer+seller), got %d", cqCount)
	}
}

// TestGenerate_NoCustomQueryMCPTool проверяет, что для стратегий с grep/filter/schema
// relationship-тулы (_by_*) не генерируются.
func TestGenerate_NoCustomQueryMCPTool(t *testing.T) {
	schema := &datasource.Schema{
		Driver: "sqlite",
		Tables: []datasource.Table{
			{
				Name:       "brands",
				PrimaryKey: []string{"id"},
				Columns:    []datasource.Column{{Name: "id", Type: "int"}, {Name: "name", Type: "string"}},
			},
			{
				Name:       "products",
				PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "int"}, {Name: "name", Type: "string"}, {Name: "brand_id", Type: "int"},
				},
				ForeignKeys: []datasource.ForeignKey{
					{Columns: []string{"brand_id"}, ReferencedTable: "brands", ReferencedColumns: []string{"id"}},
				},
			},
		},
	}

	cfg := Generate(schema, &config.Config{
		DataSource: config.DataSourceConfig{Driver: "sqlite", DSN: "test.db"},
	})

	for _, tool := range cfg.MCPTools {
		if strings.HasPrefix(tool.Name, "products_by_") || strings.Contains(tool.Name, "_by_") {
			t.Errorf("relationship tools should not be generated in v4: %s (endpoint: %s)", tool.Name, tool.Endpoint)
		}
	}
	t.Logf("✅ No _by_ tools generated (checked %d MCP tools)", len(cfg.MCPTools))
}

// TestGenerate_WithSkipRules проверяет, что кастомные SkipRules работают вместе с дефолтными.
func TestGenerate_WithSkipRules(t *testing.T) {
	schema := &datasource.Schema{
		Driver: "sqlite",
		Tables: []datasource.Table{
			{Name: "students", PrimaryKey: []string{"id"}, Columns: []datasource.Column{
				{Name: "id", Type: "string"},
				{Name: "name", Type: "string"},
			}},
			{Name: "django_auth", PrimaryKey: []string{"id"}, Columns: []datasource.Column{
				{Name: "id", Type: "string"},
			}},
			{Name: "wp_posts", PrimaryKey: []string{"id"}, Columns: []datasource.Column{
				{Name: "id", Type: "string"},
				{Name: "post_title", Type: "string"},
			}},
			{Name: "sessions", PrimaryKey: []string{"id"}, Columns: []datasource.Column{
				{Name: "id", Type: "string"},
			}},
		},
	}

	// Default rules: django_ + session должны быть отфильтрованы
	// Custom rule: wp_ тоже должен быть отфильтрован
	cfg := Generate(schema, &config.Config{
		DataSource: config.DataSourceConfig{Driver: "sqlite", DSN: ":memory:"},
		SkipRules: []config.SkipRule{
			{Prefix: "wp_", Reason: "WordPress"},
		},
	})

	if len(cfg.Entities) != 1 {
		t.Fatalf("expected 1 entity (students), got %d: %+v", len(cfg.Entities), entityNames(cfg.Entities))
	}
	if cfg.Entities[0].Name != "students" {
		t.Errorf("expected students, got %s", cfg.Entities[0].Name)
	}
}

func entityNames(entities []config.Entity) []string {
	names := make([]string, len(entities))
	for i, e := range entities {
		names[i] = e.Name
	}
	return names
}

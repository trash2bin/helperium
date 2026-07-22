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

	if cfg.Version != 2 {
		t.Errorf("expected version 2, got %d", cfg.Version)
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
	hasStudentsFind := false
	hasStudentsByID := false
	hasHealth := false
	hasStats := false
	for _, ep := range cfg.Endpoints {
		switch {
		case ep.Path == "/students" && ep.Op == config.OpFind:
			hasStudentsFind = true
		case ep.Path == "/students/{id}" && ep.Op == config.OpGetByID:
			hasStudentsByID = true
		case ep.Path == "/health":
			hasHealth = true
		case ep.Path == "/stats":
			hasStats = true
		}
	}
	if !hasStudentsFind {
		t.Error("expected /students find endpoint")
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
	if decoded.Version != 2 {
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

	// Check every entity has an endpoint
	has := make(map[string]bool)
	for _, ep := range cfg.Endpoints {
		has[ep.Path] = true
	}
	for _, e := range cfg.Entities {
		if _, ok := has["/"+e.Name]; !ok && e.Name != "grades" && e.Name != "schedule" {
			t.Errorf("missing /%s endpoint", e.Name)
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

// TestGenerate_ListEndpoint проверяет, что list_{entity} генерируется
// для сущностей без name-поля (grades, schedule и т.д.).
func TestGenerate_ListEndpoint(t *testing.T) {
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

	// grades не имеет name-поля → должен получить list endpoint
	var hasList bool
	var listParams []config.EndpointParam
	for _, ep := range cfg.Endpoints {
		if ep.Op == config.OpList && ep.Entity == "grades" {
			hasList = true
			listParams = ep.Params
			break
		}
	}
	if !hasList {
		t.Error("expected list endpoint for 'grades' entity (no name field)")
	}
	if len(listParams) == 0 {
		t.Error("expected filter params on list endpoint")
	}

	// Проверяем, что filter params содержат все колонки кроме PK
	paramNames := make(map[string]bool)
	for _, p := range listParams {
		paramNames[p.Name] = true
	}
	if !paramNames["student_id"] {
		t.Error("expected 'student_id' filter param")
	}
	if !paramNames["discipline_id"] {
		t.Error("expected 'discipline_id' filter param")
	}
	if !paramNames["grade"] {
		t.Error("expected 'grade' filter param")
	}
	if paramNames["id"] {
		t.Error("should not have 'id' as filter param (it's PK)")
	}

	// Проверяем, что MCP tools генерируются для grades
	// (grades не имеет name-поля, search заменяет list)
	var hasSearchTool bool
	for _, tool := range cfg.MCPTools {
		if tool.Name == "search_grades" {
			hasSearchTool = true
			break
		}
	}
	if !hasSearchTool {
		t.Error("expected search_grades MCP tool for grades (replaces list_grades)")
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

// TestGenerate_FindWithFilters проверяет, что find_{entity} получает
// фильтры по всем колонкам (не только name).
func TestGenerate_FindWithFilters(t *testing.T) {
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

	// customers имеет name-поле → должен получить find endpoint с фильтрами
	var findEp *config.Endpoint
	for i, ep := range cfg.Endpoints {
		if ep.Op == config.OpFind && ep.Entity == "customers" {
			findEp = &cfg.Endpoints[i]
			break
		}
	}
	if findEp == nil {
		t.Fatal("expected find endpoint for 'customers'")
	}
	if len(findEp.Params) == 0 {
		t.Error("expected filter params on find endpoint")
	}

	// Проверяем, что все не-PK колонки есть в params
	paramNames := make(map[string]bool)
	for _, p := range findEp.Params {
		paramNames[p.Name] = true
	}
	if !paramNames["email"] {
		t.Error("expected 'email' filter param")
	}
	if !paramNames["city"] {
		t.Error("expected 'city' filter param")
	}
	if !paramNames["status"] {
		t.Error("expected 'status' filter param")
	}
	if paramNames["id"] {
		t.Error("should not have 'id' as filter param (it's PK)")
	}

	// Проверяем, что MCP tool получает фильтры через search
	// (customers имеет search стратегию, find_customers больше не генерируется)
	var searchTool *config.MCPTool
	for i, tool := range cfg.MCPTools {
		if tool.Name == "search_customers" {
			searchTool = &cfg.MCPTools[i]
			break
		}
	}
	if searchTool == nil {
		t.Fatal("expected search_customers MCP tool (replaces find_customers)")
	}
	if len(searchTool.Params) < 3 {
		t.Errorf("expected at least 3 params on search_customers, got %d", len(searchTool.Params))
	}
}

// TestGenerate_BoolFilterParams проверяет, что bool-колонки получают
// фильтр с типом bool (true/false) в find/list параметрах.
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

	var findEp *config.Endpoint
	for i, ep := range cfg.Endpoints {
		if ep.Op == config.OpFind && ep.Entity == "products" {
			findEp = &cfg.Endpoints[i]
			break
		}
	}
	if findEp == nil {
		t.Fatal("expected find endpoint for 'products'")
	}

	paramMap := make(map[string]config.ParamType)
	for _, p := range findEp.Params {
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

// TestGenerate_CustomQueryToolNameCollapse проверяет, что двойные подчёркивания
// схлопываются в имени MCP-тула для custom queries.
func TestGenerate_CustomQueryToolNameCollapse(t *testing.T) {
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
		if tool.Endpoint == "/brands/{id}/products" {
			// Should be products_by_brands, NOT query__brands_id_products
			if strings.Contains(tool.Name, "__") {
				t.Errorf("tool name has double underscore: %s", tool.Name)
			}
			if !strings.HasPrefix(tool.Name, "products_by_") {
				t.Errorf("expected tool name to start with 'products_by_', got %s", tool.Name)
			}
			t.Logf("custom query tool: %s", tool.Name)
			return
		}
	}
	t.Error("expected custom query MCP tool for /brands/{id}/products")
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

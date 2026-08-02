package configgen

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/trash2bin/helperium/data-service/internal/datasource"
	"github.com/trash2bin/helperium/helperium-go/config"
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

	if cfg.Version != 4 {
		t.Errorf("expected version 4, got %d", cfg.Version)
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
	if decoded.Version != 4 {
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

	// Bool columns: is_active (business flag) should be present;
	// is_promo (marketing noise) should be filtered out.
	if paramMap["is_active"] != config.ParamTypeBool {
		t.Errorf("expected is_active to be bool, got %s", paramMap["is_active"])
	}
	if _, ok := paramMap["is_promo"]; ok {
		t.Errorf("expected is_promo to be excluded from filter params (marketing flag)")
	}

	// System datetime/date fields are excluded from MCP tool schema
	// to keep the param list lean. Use distinct_* or schema_* for discovery.
	if _, ok := paramMap["created_at"]; ok {
		t.Errorf("expected created_at to be excluded from filter params (system field)")
	}
	if _, ok := paramMap["deleted_at"]; ok {
		t.Errorf("expected deleted_at to be excluded from filter params (system field)")
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
func TestGenerate_DisplayNameOnStrategyTools(t *testing.T) {
	schema := &datasource.Schema{
		Driver: "sqlite",
		Tables: []datasource.Table{{
			Name:       "catalog_product",
			PrimaryKey: []string{"id"},
			Columns: []datasource.Column{
				{Name: "id", Type: "int"},
				{Name: "name", Type: "string"},
				{Name: "price", Type: "float"},
				{Name: "category_id", Type: "int"},
			},
		}},
	}

	cfg := Generate(schema, &config.Config{
		DataSource:      config.DataSourceConfig{Driver: "sqlite", DSN: "test.db"},
		DisplayPrefixes: []string{"catalog_"},
		CustomPlurals:   map[string]string{"catalog_product": "products"},
	})

	for _, tool := range cfg.MCPTools {
		switch {
		case strings.HasPrefix(tool.Name, "grep_"):
			if tool.DisplayName == "" {
				t.Errorf("grep tool %q must have non-empty DisplayName", tool.Name)
			} else {
				t.Logf("  %s → display_name=%q", tool.Name, tool.DisplayName)
			}
		case strings.HasPrefix(tool.Name, "filter_"):
			if tool.DisplayName == "" {
				t.Errorf("filter tool %q must have non-empty DisplayName", tool.Name)
			} else {
				t.Logf("  %s → display_name=%q", tool.Name, tool.DisplayName)
			}
		case strings.HasPrefix(tool.Name, "schema_"):
			if tool.DisplayName == "" {
				t.Errorf("schema tool %q must have non-empty DisplayName", tool.Name)
			} else {
				t.Logf("  %s → display_name=%q", tool.Name, tool.DisplayName)
			}
		case strings.HasPrefix(tool.Name, "get_"):
			// get_* уже имел display_name — проверяем не сломался
			if tool.DisplayName == "" {
				t.Errorf("get tool %q must have non-empty DisplayName", tool.Name)
			}
		}
	}
}

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
	t.Logf(" No _by_ tools generated (checked %d MCP tools)", len(cfg.MCPTools))
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

	// Default rules: django_ + wp_ (custom) должны быть отфильтрованы.
	// session/sessions НЕ в дефолтах (P1-4: общеупотребимое бизнес-слово),
	// но тест проверяет, что кастомное правило тоже работает.
	cfg := Generate(schema, &config.Config{
		DataSource: config.DataSourceConfig{Driver: "sqlite", DSN: ":memory:"},
		SkipRules: []config.SkipRule{
			{Prefix: "wp_", Reason: "WordPress"},
			{Prefix: "session", Reason: "Custom: sessions not business"},
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

// TestDefaultFilterableFieldRules проверяет, что дефолтные правила фильтруемых полей
// содержат ожидаемые имена.
func TestDefaultFilterableFieldRules(t *testing.T) {
	rules := DefaultFilterableFieldRules()
	if len(rules) == 0 {
		t.Fatal("expected non-empty DefaultFilterableFieldRules")
	}

	// Проверяем, что ключевые поля проходят (только allow-правила —
	// block-only правила не делают поле filterable, см. IsFilterableField).
	testFields := []struct {
		name  string
		match bool
	}{
		{"name", true},
		{"price", true},
		{"status", true},
		{"unknown_field", false},
	}

	for _, tc := range testFields {
		matched := false
		for _, r := range rules {
			hasAllow := len(r.AllowNames) > 0 || len(r.AllowSuffix) > 0 || len(r.AllowContains) > 0
			if !hasAllow {
				continue // block-only: не allow-матчер
			}
			if r.Matches(tc.name) {
				matched = true
				break
			}
		}
		if matched != tc.match {
			t.Errorf("DefaultFilterableFieldRules: %s match=%v, want %v", tc.name, matched, tc.match)
		}
	}
}

// TestDefaultSearchableFieldRules проверяет, что дефолтные правила поисковых полей
// блокируют image/seo/json и пропускают обычные строковые поля.
func TestDefaultSearchableFieldRules(t *testing.T) {
	rules := DefaultSearchableFieldRules()
	if len(rules) == 0 {
		t.Fatal("expected non-empty DefaultSearchableFieldRules")
	}

	testFields := []struct {
		name  string
		block bool
	}{
		{"name", false}, // обычное поле — не блокируется
		{"description", false},
		{"article", false},
		{"main_image", true},   // _image suffix — блокируется
		{"photo_url", true},    // _url suffix — блокируется
		{"seo_title", true},    // seo contains — блокируется
		{"product_json", true}, // json contains — блокируется
		{"image", true},        // exact name match
		{"thumbnail", true},    // exact name match
	}

	for _, tc := range testFields {
		// All DefaultSearchableFieldRules are block-only (empty Allow*).
		// Matches returns false when blocked by any rule.
		blocked := false
		for _, r := range rules {
			if !r.Matches(tc.name) {
				blocked = true
				break
			}
		}
		if blocked != tc.block {
			t.Errorf("DefaultSearchableFieldRules: %s blocked=%v, want %v", tc.name, blocked, tc.block)
		}
	}
}

// TestDefaultEnumFieldRules проверяет дефолтные правила enum-полей.
func TestDefaultEnumFieldRules(t *testing.T) {
	rules := DefaultEnumFieldRules()
	if len(rules) == 0 {
		t.Fatal("expected non-empty DefaultEnumFieldRules")
	}

	testFields := []struct {
		name  string
		match bool
	}{
		{"order_status", true},
		{"product_type", true},
		{"user_role", true},
		{"city", true},
		{"country", true},
		{"name", false},
		{"price", false},
	}

	for _, tc := range testFields {
		matched := false
		for _, r := range rules {
			if r.Matches(tc.name) {
				matched = true
				break
			}
		}
		if matched != tc.match {
			t.Errorf("DefaultEnumFieldRules: %s match=%v, want %v", tc.name, matched, tc.match)
		}
	}
}

// TestResolveFieldRules проверяет resolveFieldRules helper.
func TestResolveFieldRules(t *testing.T) {
	defaults := DefaultFilterableFieldRules()

	// Без disabled и custom — как defaults (2: filterable.common + block_sensitive)
	result := resolveFieldRules(defaults, nil, nil)
	if len(result) != 2 {
		t.Errorf("expected 2 rules (no changes), got %d", len(result))
	}

	// С disabled — фильтруем по ID (block_sensitive остаётся)
	result = resolveFieldRules(defaults, []string{"filterable.common"}, nil)
	if len(result) != 1 {
		t.Errorf("expected 1 rule (disabled filterable.common, block_sensitive stays), got %d", len(result))
	}

	// С custom — дополняем
	custom := []config.FieldRule{
		{AllowNames: []string{"rating", "discount"}, Reason: "Custom fields"},
	}
	result = resolveFieldRules(defaults, nil, custom)
	if len(result) != 3 {
		t.Errorf("expected 3 rules (default + block_sensitive + custom), got %d: %v", len(result), result)
	}
	// Последнее правило — custom
	if len(result[2].AllowNames) != 2 || result[2].AllowNames[0] != "rating" {
		t.Errorf("expected custom rule with [rating, discount], got %v", result[2].AllowNames)
	}
}

// TestResolveFieldRules_ExactIDMatch — disabled матчится по полному ID,
// не по префиксу/Reason (M7).
func TestResolveFieldRules_ExactIDMatch(t *testing.T) {
	defaults := DefaultFilterableFieldRules()

	// Префикс ID "filterable" НЕ отключает "filterable.common" (exact match).
	result := resolveFieldRules(defaults, []string{"filterable"}, nil)
	if len(result) != 2 {
		t.Errorf("expected 2 rules (prefix does not disable), got %d", len(result))
	}

	// Полный ID отключает (block_sensitive остаётся).
	result = resolveFieldRules(defaults, []string{"filterable.common"}, nil)
	if len(result) != 1 {
		t.Errorf("expected 1 rule (exact ID disables filterable.common), got %d", len(result))
	}

	// Перефразировка Reason не ломает отключение (матч по ID).
	reworded := []config.FieldRule{{ID: "filterable.common", Reason: "Другое описание", AllowNames: []string{"name"}}}
	result = resolveFieldRules(reworded, []string{"filterable.common"}, nil)
	if len(result) != 0 {
		t.Errorf("expected 0 rules (ID match works despite reworded Reason), got %d", len(result))
	}
}

// TestResolveFieldRules_DisabledFieldRules_Idempotent — дрейф-тест (M7):
// resolved-дефолт с ID, попавший в custom при следующем rewrite, должен
// отфильтровываться по disabled ID — иначе дефолт растёт поверх себя
// (rewrite1→default×2, rewrite2→default×3).
func TestResolveFieldRules_DisabledFieldRules_Idempotent(t *testing.T) {
	defaults := DefaultFilterableFieldRules()
	disabled := []string{"filterable.common"}

	// Первый rewrite: Generate персистит resolved-список (default × 1).
	result1 := resolveFieldRules(defaults, disabled, nil)
	if len(result1) != 1 || result1[0].ID != "filterable.block_sensitive" {
		t.Fatalf("expected 1 rule (block_sensitive stays after disabling filterable.common), got %d", len(result1))
	}

	// Симуляция Hydrate: resolved-список (включая дефолт, если бы он не был
	// отфильтрован) передаётся как custom. Здесь дефолт уже отфильтрован,
	// но проверим идиоматично: если custom содержит resolved-дефолт с ID —
	// он отфильтровывается, не дублируется.
	resolvedDefault := append([]config.FieldRule{}, defaults...)
	result2 := resolveFieldRules(defaults, disabled, resolvedDefault)
	// Остаётся только block_sensitive (filterable.common отключён, его
	// resolved-копия в custom тоже отфильтрована по ID).
	if len(result2) != 1 || result2[0].ID != "filterable.block_sensitive" {
		t.Errorf("M7 drift: expected 1 rule (block_sensitive only), got %d: %v", len(result2), result2)
	}

	// Контроль: без disabled custom-правило сохраняется.
	custom := []config.FieldRule{{ID: "custom.rating", AllowNames: []string{"rating"}}}
	result3 := resolveFieldRules(defaults, disabled, custom)
	// block_sensitive не отключён и всегда в resolved + custom.rating.
	if len(result3) != 2 || result3[1].ID != "custom.rating" {
		t.Errorf("expected custom rule kept (block_sensitive + custom.rating), got %v", result3)
	}
}

// TestResolveFieldRules_CustomDuplicateOfDefault_NoDrift — M7: custom-правило
// с ID, совпадающим с дефолтным, НЕ должно дублироваться при повторных
// resolve. Сценарий rewrite-дрейфа: Generate пишет resolved (defaults+custom)
// в конфиг → Hydrate передаёт как custom → defaults растут поверх себя.
func TestResolveFieldRules_CustomDuplicateOfDefault_NoDrift(t *testing.T) {
	defaults := DefaultFilterableFieldRules()
	if len(defaults) == 0 {
		t.Fatal("no default filterable rules")
	}

	// Симуляция первого rewrite: resolved = defaults + custom.
	custom := []config.FieldRule{
		{ID: "filterable.rating", AllowNames: []string{"rating"}}, // дубликат дефолта
		{ID: "custom.myrule", AllowNames: []string{"score"}},      // настоящее custom
	}

	// resolve без disabled.
	resolved := resolveFieldRules(defaults, nil, custom)

	// Дефолтов должно быть ровно по одному (не два).
	defaultCount := 0
	for _, r := range resolved {
		if r.ID == "filterable.common" {
			defaultCount++
		}
	}
	if defaultCount != 1 {
		t.Errorf("M7 drift: default rule present %d times, want 1: %v", defaultCount, resolved)
	}

	// custom.myrule должен сохраниться.
	customKept := false
	for _, r := range resolved {
		if r.ID == "custom.myrule" {
			customKept = true
		}
	}
	if !customKept {
		t.Errorf("custom rule custom.myrule should be kept, got %v", resolved)
	}

	// Идемпотентность: повторный resolve с resolved-as-custom не растёт.
	resolved2 := resolveFieldRules(defaults, nil, resolved)
	if len(resolved2) != len(resolved) {
		t.Errorf("M7 drift on 2nd resolve: %d → %d rules", len(resolved), len(resolved2))
	}
}

func TestGenerate_WithFieldRules(t *testing.T) {
	schema := &datasource.Schema{
		Driver: "sqlite",
		Tables: []datasource.Table{
			{Name: "products", PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "int"},
					{Name: "name", Type: "string"},
					{Name: "rating", Type: "float"},
					{Name: "internal_note", Type: "string"},
				}},
		},
	}

	cfg := Generate(schema, &config.Config{
		DataSource: config.DataSourceConfig{Driver: "sqlite", DSN: "test.db"},
		FilterableRules: []config.FieldRule{
			{AllowNames: []string{"rating"}, Reason: "Custom filterable: rating"},
		},
	})

	// Проверяем, что filter_products сгенерирован (rating — filterable)
	var hasFilter bool
	for _, ep := range cfg.Endpoints {
		if ep.Strategy == "filter" && ep.Entity == "products" {
			hasFilter = true
			break
		}
	}
	if !hasFilter {
		t.Error("expected filter_products endpoint — rating is filterable")
	}

	// Проверяем, что grep_products сгенерирован (name — string)
	var hasGrep bool
	for _, ep := range cfg.Endpoints {
		if ep.Strategy == "grep" && ep.Entity == "products" {
			hasGrep = true
			break
		}
	}
	if !hasGrep {
		t.Error("expected grep_products endpoint — name is string")
	}
}

// TestGenerate_DisabledFieldRules проверяет отключение дефолтных FieldRules.
func TestGenerate_DisabledFieldRules(t *testing.T) {
	schema := &datasource.Schema{
		Driver: "sqlite",
		Tables: []datasource.Table{
			{Name: "products", PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "int"},
					{Name: "name", Type: "string"},
					{Name: "price", Type: "float"},
					{Name: "brand_id", Type: "int"},
				}},
		},
	}

	cfg := Generate(schema, &config.Config{
		DataSource:                     config.DataSourceConfig{Driver: "sqlite", DSN: "test.db"},
		DisabledDefaultFilterableRules: []string{"filterable.common"},
	})

	// brand_id — FK, implicitly filterable (not from rules)
	// name, price — NOT filterable because we disabled the default rule
	// So filter endpoint should still exist because brand_id works implicitly
	var hasFilter bool
	for _, ep := range cfg.Endpoints {
		if ep.Strategy == "filter" && ep.Entity == "products" {
			hasFilter = true
			break
		}
	}
	if !hasFilter {
		t.Error("expected filter_products endpoint — brand_id is implicit FK")
	}

	t.Logf(" filter_products endpoint present despite disabled default filterable rules")
}

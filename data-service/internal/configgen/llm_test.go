package configgen

import (
	"fmt"
	"strings"
	"testing"

	"github.com/trash2bin/helperium/data-service/internal/datasource"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// Фаза 1 приёмка: hints доменно-нейтральны — нет захардкоженных
// автозапчастей (Bosch/KYB/brake pads) и нет неподставленного литерала {entity}.
func TestSchemaForLLM_HintsAreDomainNeutral(t *testing.T) {
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
					{Name: "id", Type: "int"},
					{Name: "name", Type: "string"},
					{Name: "description", Type: "string"},
					{Name: "category", Type: "string"},
					{Name: "brand_id", Type: "int"},
					{Name: "price", Type: "float"},
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
	result := GenerateSchemaForLLM(schema, cfg)

	if len(result.WorkflowHints) == 0 {
		t.Fatal("expected workflow hints, got none")
	}

	// Захардкоженные автозапчасти не должны присутствовать.
	for _, forbidden := range []string{"Bosch", "KYB", "brake pads", "Brembo", "TRW", "oil filter", "muffler"} {
		for _, hint := range result.WorkflowHints {
			if strings.Contains(hint, forbidden) {
				t.Errorf("hint contains hardcoded domain term %q: %q", forbidden, hint)
			}
		}
	}

	// Неподставленный литерал {entity} не должен присутствовать.
	for _, hint := range result.WorkflowHints {
		if strings.Contains(hint, "{entity}") {
			t.Errorf("hint contains unsubstituted literal {entity}: %q", hint)
		}
	}

	// Анти-переборный hint присутствует.
	hasSearchFirst := false
	for _, hint := range result.WorkflowHints {
		if strings.Contains(strings.ToUpper(hint), "NEVER") && strings.Contains(strings.ToLower(hint), "id") {
			hasSearchFirst = true
			break
		}
	}
	if !hasSearchFirst {
		t.Errorf("expected anti-enumeration (search-first) hint, got: %v", result.WorkflowHints)
	}
}

// Фаза 2.5 приёмка (деконсолидация filter): число MCP-тулов = N filter_* + 5 db_*.
// filter деконсолидирован (имена полей нужны модели прямо в схеме тула),
// остальные db_* — константны.
func TestMCPTools_ConstantCount(t *testing.T) {
	for _, n := range []int{1, 10, 100} {
		schema := &datasource.Schema{Driver: "sqlite"}
		for i := 0; i < n; i++ {
			schema.Tables = append(schema.Tables, datasource.Table{
				Name:       fmt.Sprintf("entity_%02d", i),
				PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "int"},
					{Name: "name", Type: "string"},
					{Name: "status", Type: "string"},
				},
			})
		}
		cfg := Generate(schema, &config.Config{
			DataSource: config.DataSourceConfig{Driver: "sqlite", DSN: "test.db"},
		})
		// N filter_* + 5 db_* (без db_filter).
		expected := n + 5
		if len(cfg.MCPTools) != expected {
			t.Errorf("N=%d: expected %d MCP tools (N filter_* + 5 db_*), got %d",
				n, expected, len(cfg.MCPTools))
		}
		// Ровно N filter_* и ровно 5 db_* (db_map/db_describe/db_search/db_get/db_related).
		var filterCount, dbCount int
		for _, tool := range cfg.MCPTools {
			if strings.HasPrefix(tool.Name, "filter_") {
				filterCount++
			}
			if strings.HasPrefix(tool.Name, "db_") {
				dbCount++
			}
		}
		if filterCount != n {
			t.Errorf("N=%d: expected %d filter_* tools, got %d", n, n, filterCount)
		}
		if dbCount != 5 {
			t.Errorf("N=%d: expected 5 db_* tools (no db_filter), got %d", n, dbCount)
		}
	}
}

// Фаза 2 приёмка: entity — обычный string, не enum (на большой БД enum
// расдул бы манифест). Проверяем, что у db_search нет enum-ограничения.
func TestMCPTools_EntityIsPlainStringNotEnum(t *testing.T) {
	schema := &datasource.Schema{
		Driver: "sqlite",
		Tables: []datasource.Table{
			{Name: "products", PrimaryKey: []string{"id"},
				Columns: []datasource.Column{{Name: "id", Type: "int"}, {Name: "name", Type: "string"}}},
		},
	}
	cfg := Generate(schema, &config.Config{
		DataSource: config.DataSourceConfig{Driver: "sqlite", DSN: "test.db"},
	})

	var dbSearch *config.MCPTool
	for i := range cfg.MCPTools {
		if cfg.MCPTools[i].Name == "db_search" {
			dbSearch = &cfg.MCPTools[i]
			break
		}
	}
	if dbSearch == nil {
		t.Fatal("db_search missing")
	}
	for _, p := range dbSearch.Params {
		if p.Name == "entity" {
			if p.Type != config.ParamTypeString {
				t.Errorf("entity param must be plain string (not enum), got %q", p.Type)
			}
		}
	}
}

func toolNames(tools []config.MCPTool) []string {
	names := make([]string, 0, len(tools))
	for _, t := range tools {
		names = append(names, t.Name)
	}
	return names
}
func TestStrategyToolDescriptions_DomainNeutralAndNoDeletedTools(t *testing.T) {
	schema := &datasource.Schema{
		Driver: "sqlite",
		Tables: []datasource.Table{
			{
				Name:       "products",
				PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "int"},
					{Name: "name", Type: "string"},
					{Name: "category", Type: "string"},
				},
			},
		},
	}
	cfg := Generate(schema, &config.Config{
		DataSource: config.DataSourceConfig{Driver: "sqlite", DSN: "test.db"},
	})

	// Собираем ВСЕ строки манифеста (описания + параметры) и hints.
	var allText []string
	for _, tool := range cfg.MCPTools {
		allText = append(allText, tool.Description)
		for _, p := range tool.Params {
			allText = append(allText, p.Description)
		}
	}
	result := GenerateSchemaForLLM(schema, cfg)
	allText = append(allText, result.WorkflowHints...)

	// (а) Нет ссылок на удалённые тулы.
	for _, forbidden := range []string{"distinct_", "get_", "count_"} {
		for _, txt := range allText {
			if strings.Contains(txt, forbidden) {
				t.Errorf("manifest/hints reference deleted tool prefix %q: %q", forbidden, txt)
			}
		}
	}
	// (б) Нет неподставленного {entity}-литерала.
	for _, txt := range allText {
		if strings.Contains(txt, "{entity}") {
			t.Errorf("manifest/hints contain unsubstituted {entity}: %q", txt)
		}
	}
	// (в) Нет доменных автозапчастей.
	for _, forbidden := range []string{"Brembo", "Bosch", "KYB", "muffler", "brake pads", "TRW", "oil filter"} {
		for _, txt := range allText {
			if strings.Contains(txt, forbidden) {
				t.Errorf("manifest/hints contain domain term %q: %q", forbidden, txt)
			}
		}
	}

	// (г) Фаза 2.5: hints ссылаются ТОЛЬКО на существующие db_* тулы.
	// Никаких grep_/filter_/schema_ (удалены), никаких голых глаголов
	// "search text first" без имени тула — тупая модель должна видеть точное имя.
	existingTools := make(map[string]bool)
	for _, tool := range cfg.MCPTools {
		existingTools[tool.Name] = true
	}
	for _, hint := range result.WorkflowHints {
		// Каждый hint должен упоминать реальный тул или паттерн filter_<entity>.
		// filter_<entity> валиден (Фаза 2.5: filter пер-энтити, имя зависит от entity).
		mentionsTool := strings.Contains(hint, "filter_<entity>")
		for toolName := range existingTools {
			if strings.Contains(hint, toolName) {
				mentionsTool = true
				break
			}
		}
		if !mentionsTool {
			t.Errorf("hint does not reference any existing tool or filter_<entity>: %q", hint)
		}
		// Никаких удалённых префиксов тулов в hints (filter_<entity> — паттерн, не удалённый тул).
		for _, forbidden := range []string{"grep_", "schema_", "distinct_", "count_", "get_"} {
			if strings.Contains(hint, forbidden) {
				t.Errorf("hint references removed tool prefix %q: %q", forbidden, hint)
			}
		}
		// db_filter (консолидированный) удалён — hints не должны на него ссылаться.
		if strings.Contains(hint, "db_filter") {
			t.Errorf("hint references removed consolidated db_filter: %q", hint)
		}
	}
}

// Фаза 1 приёмка: SearchFields заполнен из searchable-полей (string, не PK, не tenant).
func TestSchemaForLLM_SearchFieldsFilled(t *testing.T) {
	schema := &datasource.Schema{
		Driver: "sqlite",
		Tables: []datasource.Table{
			{
				Name:       "products",
				PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "int"},
					{Name: "name", Type: "string"},
					{Name: "description", Type: "string"},
					{Name: "price", Type: "float"},   // не searchable (не string)
					{Name: "image_url", Type: "string"}, // заблокировано дефолтным searchable-правилом (image/seo/json)
				},
			},
		},
	}
	cfg := Generate(schema, &config.Config{
		DataSource: config.DataSourceConfig{Driver: "sqlite", DSN: "test.db"},
	})
	result := GenerateSchemaForLLM(schema, cfg)

	if len(result.Entities) != 1 {
		t.Fatalf("expected 1 entity, got %d", len(result.Entities))
	}
	e := result.Entities[0]

	// name и description — searchable. image_url — блокируется дефолтным правилом
	// (ищем по подстроке searchable-полей, т.к. дефолтные правила могут резать).
	if e.SearchFields == "" {
		t.Errorf("SearchFields must not be empty (was always \"\" before Phase 1): %q", e.SearchFields)
	}
	if !strings.Contains(e.SearchFields, "name") {
		t.Errorf("SearchFields should contain 'name', got %q", e.SearchFields)
	}
	if !strings.Contains(e.SearchFields, "description") {
		t.Errorf("SearchFields should contain 'description', got %q", e.SearchFields)
	}
}

// Фаза 1 приёмка: get_by_id/count/distinct не эмитятся в MCP-манифест.
func TestGenerateMCPTools_NoGetByIDCountDistinct(t *testing.T) {
	schema := &datasource.Schema{
		Driver: "sqlite",
		Tables: []datasource.Table{
			{
				Name:       "products",
				PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "int"},
					{Name: "name", Type: "string"},
					{Name: "category", Type: "string"},
					{Name: "price", Type: "float"},
				},
			},
		},
	}
	cfg := Generate(schema, &config.Config{
		DataSource: config.DataSourceConfig{Driver: "sqlite", DSN: "test.db"},
	})

	for _, tool := range cfg.MCPTools {
		if strings.HasPrefix(tool.Name, "get_") ||
			strings.HasPrefix(tool.Name, "count_") ||
			strings.HasPrefix(tool.Name, "distinct_") {
			t.Errorf("tool %q must NOT be in MCP manifest (anti-enumeration policy)", tool.Name)
		}
	}

	// Консолидированные тулы (Фаза 2): 5 db_* (без db_filter — он деконсолидирован).
	names := make(map[string]bool)
	for _, tool := range cfg.MCPTools {
		names[tool.Name] = true
	}
	for _, expected := range []string{"db_map", "db_describe", "db_search", "db_get", "db_related"} {
		if !names[expected] {
			t.Errorf("expected consolidated tool %q, got %v", expected, names)
		}
	}
	// db_filter НЕ в консолидированных (пер-энтити filter_products вместо него).
	if names["db_filter"] {
		t.Errorf("db_filter must NOT be consolidated (filter is per-entity in Phase 2.5)")
	}
	// filter_products — пер-энтити (имена полей в схеме тула).
	if !names["filter_products"] {
		t.Errorf("filter_products must be emitted (per-entity filter, Phase 2.5)")
	}
	// grep_/schema_ по-прежнему консолидированы (не эмитятся per-entity).
	for _, forbidden := range []string{"grep_products", "schema_products"} {
		if names[forbidden] {
			t.Errorf("per-entity tool %q must NOT be emitted (consolidated in Phase 2)", forbidden)
		}
	}

	// REST-эндпоинты get_by_id/count/distinct ОСТАЮТСЯ (совместимость).
	ops := make(map[config.Op]int)
	for _, ep := range cfg.Endpoints {
		ops[ep.Op]++
	}
	if ops[config.OpGetByID] == 0 {
		t.Error("REST get_by_id endpoint must remain (compat)")
	}
	if ops[config.OpCount] == 0 {
		t.Error("REST count endpoint must remain (compat)")
	}
}

// Фаза 2 приёмка: LLMToolPolicy.ExposeGetByID=true возвращает get_* в манифест (opt-in).
func TestGenerateMCPTools_LLMToolPolicyOptIn(t *testing.T) {
	schema := &datasource.Schema{
		Driver: "sqlite",
		Tables: []datasource.Table{
			{
				Name:       "products",
				PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "int"},
					{Name: "name", Type: "string"},
				},
			},
		},
	}

	// default policy — get_* нет.
	cfgDefault := Generate(schema, &config.Config{
		DataSource: config.DataSourceConfig{Driver: "sqlite", DSN: "test.db"},
	})
	for _, tool := range cfgDefault.MCPTools {
		if strings.HasPrefix(tool.Name, "get_") {
			t.Errorf("default policy must NOT expose get_*: %s", tool.Name)
		}
	}

	// opt-in: ExposeGetByID=true → get_* есть.
	cfgOptIn := Generate(schema, &config.Config{
		DataSource: config.DataSourceConfig{Driver: "sqlite", DSN: "test.db"},
		LLMToolPolicy: config.LLMToolPolicy{
			ExposeGetByID: true,
		},
	})
	found := false
	for _, tool := range cfgOptIn.MCPTools {
		if tool.Name == "get_products" {
			found = true
		}
	}
	if !found {
		t.Error("ExposeGetByID=true should expose get_products in MCP manifest")
	}
}

// Фаза 1 приёмка: описания прескриптивные — db_search говорит "search first",
// db_filter — "when you KNOW the value", db_get — анти-перебор.
func TestStrategyToolDescriptions_Prescriptive(t *testing.T) {
	schema := &datasource.Schema{
		Driver: "sqlite",
		Tables: []datasource.Table{
			{
				Name:       "products",
				PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "int"},
					{Name: "name", Type: "string"},
					{Name: "category", Type: "string"},
				},
			},
		},
	}
	cfg := Generate(schema, &config.Config{
		DataSource: config.DataSourceConfig{Driver: "sqlite", DSN: "test.db"},
	})

	descs := make(map[string]string)
	for _, tool := range cfg.MCPTools {
		descs[tool.Name] = tool.Description
	}

	dbSearch, ok := descs["db_search"]
	if !ok {
		t.Fatal("db_search missing")
	}
	if !strings.Contains(strings.ToUpper(dbSearch), "PRIMARY TEXT SEARCH") {
		t.Errorf("db_search description should be prescriptive 'PRIMARY text search': %s", dbSearch)
	}
	if !strings.Contains(strings.ToUpper(dbSearch), "INSTEAD OF GUESSING IDS") {
		t.Errorf("db_search description should mention 'instead of guessing ids': %s", dbSearch)
	}

	dbFilter, ok := descs["filter_products"]
	if !ok {
		t.Fatal("filter_products missing (per-entity filter, Phase 2.5)")
	}
	if !strings.Contains(strings.ToUpper(dbFilter), "WHEN YOU KNOW THE VALUE") {
		t.Errorf("filter_products description should say 'when you KNOW the value': %s", dbFilter)
	}
	if !strings.Contains(strings.ToUpper(dbFilter), "DO NOT GUESS") {
		t.Errorf("filter_products description should say 'do not guess': %s", dbFilter)
	}

	dbGet, ok := descs["db_get"]
	if !ok {
		t.Fatal("db_get missing")
	}
	if !strings.Contains(strings.ToUpper(dbGet), "NEVER ENUMERATE IDS") {
		t.Errorf("db_get description should say 'NEVER enumerate ids': %s", dbGet)
	}
}

// Фаза 2.5 smoke: db_map должен работать БЕЗ introspected schema (schema=nil),
// если cfg.Entities есть (fallback на Relations). Иначе после рестарта
// data-service модель слепа, пока админ не вызовет rewrite.
func TestSchemaForLLM_NilSchema_FallbackToEntities(t *testing.T) {
	tPK := true
	cfg := &config.Config{
		Entities: []config.Entity{
			{
				Name: "products", Table: "products", IDColumn: "id",
				Fields: []config.EntityField{
					{Name: "id", Column: "id", Type: config.FieldTypeInt, PrimaryKey: &tPK},
					{Name: "name", Column: "name", Type: config.FieldTypeString},
					{Name: "brand_id", Column: "brand_id", Type: config.FieldTypeInt},
				},
				Relations: []config.Relation{
					{Field: "brand_id", Kind: config.RelationManyToOne, Table: "brands", LocalFK: "brand_id"},
				},
			},
		},
	}

	// schema = nil (db_map после рестарта, без rewrite).
	result := GenerateSchemaForLLM(nil, cfg)

	if len(result.Entities) != 1 {
		t.Fatalf("expected 1 entity from cfg fallback, got %d", len(result.Entities))
	}
	e := result.Entities[0]
	if !strings.Contains(e.Name, "products") {
		t.Errorf("expected products entity, got %q", e.Name)
	}
	// SearchFields из полей.
	if e.SearchFields == "" {
		t.Errorf("SearchFields should be filled from cfg.Entities, got empty")
	}
	// Relations из cfg.Entities.Relations.
	if len(e.Relations) == 0 {
		t.Errorf("relations should come from cfg.Entities.Relations, got none")
	}
	if e.Relations[0].ReferencedEntity == "" {
		t.Errorf("relation should reference entity, got %+v", e.Relations[0])
	}
}

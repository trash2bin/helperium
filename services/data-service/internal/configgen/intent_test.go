package configgen

import (
	"testing"

	"github.com/trash2bin/helperium/data-service/internal/datasource"
	"github.com/trash2bin/helperium/helperium-go/config"
)

func intentTestSchema() *datasource.Schema {
	return &datasource.Schema{
		Driver: "sqlite",
		Tables: []datasource.Table{
			{
				Name:       "brands",
				PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "int", Nullable: false},
					{Name: "name", Type: "string", Nullable: false},
				},
			},
			{
				Name:       "products",
				PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "int", Nullable: false},
					{Name: "name", Type: "string", Nullable: false},
					{Name: "brand_id", Type: "int", Nullable: true},
					{Name: "price", Type: "float", Nullable: true},
				},
				ForeignKeys: []datasource.ForeignKey{
					{
						Name:              "fk_products_brand",
						Columns:           []string{"brand_id"},
						ReferencedTable:   "brands",
						ReferencedColumns: []string{"id"},
					},
				},
			},
		},
	}
}

// TestExtractIntent_Hydrate_RoundTrip — если intent-поле не попало в ExtractIntent,
// Hydrate(ExtractIntent(want)) разойдётся с want.
func TestExtractIntent_Hydrate_RoundTrip(t *testing.T) {
	intent := &TenantIntent{
		DataSource: config.DataSourceConfig{Driver: config.DriverSQLite, DSN: "test.db", ReadOnly: boolPtr(true)},
		FilterableRules: []config.FieldRule{
			{AllowNames: []string{"custom_field"}, Reason: "test"},
		},
		DisabledDefaultFilterableRules: []string{"filterable.common"},
		SearchableRules: []config.FieldRule{
			{BlockContains: []string{"secret"}, Reason: "test-search"},
		},
		EnumRules: []config.FieldRule{
			{AllowContains: []string{"category"}, Reason: "test-enum"},
		},
		CustomShortNames: map[string]string{"foo": "Foo"},
		CustomQueries: map[string]config.CustomQuery{
			"my_explicit_query": {
				SQL:           "SELECT name FROM products WHERE price > ?",
				Params:        []string{"min_price"},
				MaxRows:       10,
				ResultMapping: map[string]config.ResultMappingField{},
			},
		},
		Auth:          &config.AuthConfig{Strategy: config.AuthStrategyHeader},
		Stats: &config.StatsConfig{Counters: []config.Counter{
			{Name: "products_total", Entity: "products"},
			{Name: "products_expensive", Entity: "products", Filter: "price > 1000"},
		}},
	}

	want := Hydrate(intent, intentTestSchema())
	got := Hydrate(ExtractIntent(want), intentTestSchema())

	// CustomQueries: explicit query пережил round-trip.
	if _, ok := got.CustomQueries["my_explicit_query"]; !ok {
		t.Errorf("explicit custom query lost in round-trip: %v", keys(got.CustomQueries))
	}

	// FilterableRules: кастомное правило на месте.
	foundFilter := false
	for _, r := range got.FilterableRules {
		if r.Reason == "test" {
			foundFilter = true
		}
	}
	if !foundFilter {
		t.Errorf("FilterableRules lost in round-trip: %+v", got.FilterableRules)
	}
	if len(got.DisabledDefaultFilterableRules) == 0 {
		t.Error("DisabledDefaultFilterableRules lost in round-trip")
	}

	// SearchableRules.
	foundSearch := false
	for _, r := range got.SearchableRules {
		if r.Reason == "test-search" {
			foundSearch = true
		}
	}
	if !foundSearch {
		t.Errorf("SearchableRules lost in round-trip: %+v", got.SearchableRules)
	}

	// EnumRules.
	foundEnum := false
	for _, r := range got.EnumRules {
		if r.Reason == "test-enum" {
			foundEnum = true
		}
	}
	if !foundEnum {
		t.Errorf("EnumRules lost in round-trip: %+v", got.EnumRules)
	}

	// CustomShortNames.
	if got.CustomShortNames["foo"] != "Foo" {
		t.Errorf("CustomShortNames lost in round-trip: %v", got.CustomShortNames)
	}

	// Auth.
	if got.Auth == nil || got.Auth.Strategy != config.AuthStrategyHeader {
		t.Errorf("Auth lost in round-trip: %+v", got.Auth)
	}

	// Stats: кастомные counters (включая ручной Filter) пережили round-trip.
	if got.Stats == nil {
		t.Fatal("Stats lost in round-trip")
	}
	foundFilterCounter := false
	for _, c := range got.Stats.Counters {
		if c.Name == "products_expensive" && c.Filter == "price > 1000" {
			foundFilterCounter = true
		}
	}
	if !foundFilterCounter {
		t.Errorf("custom Stats.Counter с Filter потерян в round-trip: %+v", got.Stats.Counters)
	}
}

// TestHydrate_ExplicitCustomQuerySurvives — Hydrate не должен затирать
// explicit custom query FK-производными.
func TestHydrate_ExplicitCustomQuerySurvives(t *testing.T) {
	intent := &TenantIntent{
		DataSource: config.DataSourceConfig{Driver: config.DriverSQLite, DSN: "test.db"},
		CustomQueries: map[string]config.CustomQuery{
			"my_explicit_query": {
				SQL:           "SELECT name FROM products WHERE price > ?",
				Params:        []string{"min_price"},
				MaxRows:       10,
				ResultMapping: map[string]config.ResultMappingField{},
			},
		},
	}
	got := Hydrate(intent, intentTestSchema())

	if _, ok := got.CustomQueries["my_explicit_query"]; !ok {
		t.Errorf("explicit custom query lost: %v", keys(got.CustomQueries))
	}

	// FK-производные (products_by_brands) должны быть сгенерированы.
	foundNav := false
	for k := range got.CustomQueries {
		if k == "products_by_brands_brand_id" {
			foundNav = true
		}
	}
	if !foundNav {
		t.Errorf("FK-derived custom query not generated: %v", keys(got.CustomQueries))
	}
}

// TestExtractIntent_ExcludesFKDerivedQueries — ExtractIntent не должен
// включать FK-производные запросы в explicit CustomQueries.
func TestExtractIntent_ExcludesFKDerivedQueries(t *testing.T) {
	intent := &TenantIntent{
		DataSource: config.DataSourceConfig{Driver: config.DriverSQLite, DSN: "test.db"},
		CustomQueries: map[string]config.CustomQuery{
			"my_explicit_query": {
				SQL:           "SELECT 1",
				MaxRows:       10,
				ResultMapping: map[string]config.ResultMappingField{},
			},
		},
	}
	cfg := Hydrate(intent, intentTestSchema())

	// cfg содержит и explicit, и FK-производные. ExtractIntent должен оставить только explicit.
	extracted := ExtractIntent(cfg)
	if _, ok := extracted.CustomQueries["my_explicit_query"]; !ok {
		t.Errorf("explicit query not in ExtractIntent: %v", keys(extracted.CustomQueries))
	}
	for k := range extracted.CustomQueries {
		if k == "products_by_brands_brand_id" {
			t.Errorf("FK-derived query leaked into ExtractIntent: %s", k)
		}
	}
}

func boolPtr(b bool) *bool { return &b }

func keys(m map[string]config.CustomQuery) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}

// TestRewrite_NewEntityFromSchemaDrift_WithoutRowFilter_RejectsConfig — пункт 3
// ревью: самый вероятный сценарий у живого клиента — не первый онбординг, а
// schema drift ПОСЛЕ него. Стартовый конфиг валиден (header-auth + row_filters
// на все entity). Клиент добавил таблицу в БД (CRM). Rewrite-путь
// (ExtractIntent → Hydrate → Validate — как в tenant_admin.go:520-526) должен
// ОТКЛОНИТЬ новый конфиг, а не молча активировать его с дырой (новая entity
// без row_filter → рантайм 403).
func TestRewrite_NewEntityFromSchemaDrift_WithoutRowFilter_RejectsConfig(t *testing.T) {
	// Схема ДО дрейфа: brands + products.
	schemaBefore := intentTestSchema()

	// Стартовый валидный конфиг: header-auth + row_filters на все entity.
	intent := &TenantIntent{
		DataSource: config.DataSourceConfig{Driver: config.DriverSQLite, DSN: "test.db"},
		Auth: &config.AuthConfig{
			Strategy: config.AuthStrategyHeader,
			RowFilters: []config.RowFilter{
				{Entity: "brands", Where: "tenant_id = :tenant_id"},
				{Entity: "products", Where: "tenant_id = :tenant_id"},
			},
		},
	}

	// 1. Первый rewrite (онбординг) — валиден.
	initial := Hydrate(intent, schemaBefore)
	if err := initial.Validate(); err != nil {
		t.Fatalf("initial config must be valid, got: %v", err)
	}

	// 2. Schema drift: клиент добавил таблицу orders в CRM.
	schemaAfter := intentTestSchema()
	schemaAfter.Tables = append(schemaAfter.Tables, datasource.Table{
		Name:       "orders",
		PrimaryKey: []string{"id"},
		Columns: []datasource.Column{
			{Name: "id", Type: "int", Nullable: false},
			{Name: "customer_id", Type: "int", Nullable: true},
		},
	})

	// 3. Rewrite-путь как в tenant_admin.go: ExtractIntent → Hydrate → Validate.
	newCfg := Hydrate(ExtractIntent(initial), schemaAfter)
	if err := newCfg.Validate(); err == nil {
		t.Error("SECURITY: rewrite with new entity 'orders' WITHOUT row_filter must be REJECTED. "+
			"Fail-closed expected: header-auth requires row_filter for every entity. "+
			"Silently activating this config would 403 all /orders requests in production.")
	}
}

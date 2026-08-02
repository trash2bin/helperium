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

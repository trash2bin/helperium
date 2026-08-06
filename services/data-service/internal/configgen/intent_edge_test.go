package configgen

import (
	"testing"

	"github.com/trash2bin/helperium/helperium-go/config"
)

// TestHydrate_StaleDerivedQuerySkipped — H-1: протухший FK-derived запрос,
// чей SQL совпадает с авто-паттерном, НЕ должен маскироваться под explicit.
func TestHydrate_StaleDerivedQuerySkipped(t *testing.T) {
	// Ситуация: FK удалили из БД, но старый derived-запрос остался в intent
	// (ExtractIntent принял его за explicit, т.к. ключ выпал из derived-набора).
	// SQL у него — ровно тот, что генерит buildNavigationEndpoints.
	intent := &TenantIntent{
		DataSource: config.DataSourceConfig{Driver: config.DriverSQLite, DSN: "test.db"},
		CustomQueries: map[string]config.CustomQuery{
			"products_by_brands_brand_id": {
				SQL:           "SELECT t.* FROM products t WHERE t.brand_id = ?",
				Params:        []string{"brand_id"},
				MaxRows:       1000,
				Description:   "All products linked to a brands",
				ResultMapping: map[string]config.ResultMappingField{},
			},
		},
	}

	got := Hydrate(intent, intentTestSchema())

	// Fresh derived (products_by_brands_brand_id) есть в новом конфиге.
	fresh, ok := got.CustomQueries["products_by_brands_brand_id"]
	if !ok {
		t.Fatalf("fresh derived query not generated: %v", keys(got.CustomQueries))
	}
	// И оно НЕ перезаписано stale-версией из intent (SQL одинаков, значит skip).
	if fresh.SQL != "SELECT t.* FROM products t WHERE t.brand_id = ?" {
		t.Errorf("fresh derived query was overwritten by stale version: %q", fresh.SQL)
	}
}

// TestHydrate_UserQueryWithCollidingIDKept — M-4: пользовательский запрос,
// чей id коллизирует с derived-ключом, но SQL отличается — сохраняется.
func TestHydrate_UserQueryWithCollidingIDKept(t *testing.T) {
	intent := &TenantIntent{
		DataSource: config.DataSourceConfig{Driver: config.DriverSQLite, DSN: "test.db"},
		CustomQueries: map[string]config.CustomQuery{
			"products_by_brands_brand_id": {
				SQL:           "SELECT id, name, price FROM products WHERE brand_id = ? AND price > 100",
				Params:        []string{"brand_id"},
				MaxRows:       50,
				Description:   "Дорогие продукты бренда (кастом)",
				ResultMapping: map[string]config.ResultMappingField{},
			},
		},
	}

	got := Hydrate(intent, intentTestSchema())

	// Пользовательский SQL должен победить (перезаписал fresh derived).
	q, ok := got.CustomQueries["products_by_brands_brand_id"]
	if !ok {
		t.Fatalf("user query lost: %v", keys(got.CustomQueries))
	}
	if q.SQL != "SELECT id, name, price FROM products WHERE brand_id = ? AND price > 100" {
		t.Errorf("user query overwritten by derived: %q", q.SQL)
	}
}

// TestHydrate_StatsCounterDroppedIfEntityMissing — C-1.2: кастомный counter
// на удалённую сущность отбрасывается, а не валит весь конфиг.
func TestHydrate_StatsCounterDroppedIfEntityMissing(t *testing.T) {
	intent := &TenantIntent{
		DataSource: config.DataSourceConfig{Driver: config.DriverSQLite, DSN: "test.db"},
		Stats: &config.StatsConfig{Counters: []config.Counter{
			{Name: "products_total", Entity: "products"},
			{Name: "ghost_total", Entity: "ghost_table"}, // не существует в схеме
		}},
	}

	got := Hydrate(intent, intentTestSchema())

	if got.Stats == nil {
		t.Fatal("Stats nil")
	}
	for _, c := range got.Stats.Counters {
		if c.Name == "ghost_total" {
			t.Errorf("counter referencing missing entity not dropped: %+v", c)
		}
	}
	// Валидация не должна падать.
	if err := got.Validate(); err != nil {
		t.Errorf("config invalid after dropping ghost counter: %v", err)
	}
}

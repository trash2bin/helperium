package configgen

import (
	"strings"
	"testing"

	"github.com/trash2bin/helperium/helperium-go/config"
)

// TestBuildNavigationEndpoints_SkipsUnsafeIdentifiers — Задача 2:
// имена таблиц/колонок с небезопасными символами не должны попадать
// в custom_query SQL (иначе сломанный SQL или инъекция из имени БД).
func TestBuildNavigationEndpoints_SkipsUnsafeIdentifiers(t *testing.T) {
	entities := []config.Entity{
		{
			Name:      "products",
			Table:     "products",
			IDColumn:  "id",
			Relations: []config.Relation{{Field: "brand_id", Kind: config.RelationManyToOne, Table: "brands", LocalFK: "brand_id"}},
		},
		{Name: "brands", Table: "brands", IDColumn: "id"},
	}

	// Безопасные имена — связь генерируется.
	eps, queries := buildNavigationEndpoints(entities)
	if len(queries) != 1 {
		t.Fatalf("expected 1 custom query for safe identifiers, got %d", len(queries))
	}
	if len(eps) != 1 {
		t.Fatalf("expected 1 nav endpoint for safe identifiers, got %d", len(eps))
	}

	// Небезопасное имя таблицы — связь пропускается (нет битого SQL).
	unsafe := []config.Entity{
		{
			Name:      "products",
			Table:     `products"; DROP TABLE x; --`,
			IDColumn:  "id",
			Relations: []config.Relation{{Field: "brand_id", Kind: config.RelationManyToOne, Table: "brands", LocalFK: "brand_id"}},
		},
		{Name: "brands", Table: "brands", IDColumn: "id"},
	}
	_, queriesUnsafeTable := buildNavigationEndpoints(unsafe)
	if len(queriesUnsafeTable) != 0 {
		t.Errorf("unsafe table name produced a custom query: %d", len(queriesUnsafeTable))
	}

	// Небезопасное имя FK-колонки — связь пропускается.
	unsafeFK := []config.Entity{
		{
			Name:      "products",
			Table:     "products",
			IDColumn:  "id",
			Relations: []config.Relation{{Field: "brand_id", Kind: config.RelationManyToOne, Table: "brands", LocalFK: `brand_id"; DROP`}},
		},
		{Name: "brands", Table: "brands", IDColumn: "id"},
	}
	_, queriesUnsafeFK := buildNavigationEndpoints(unsafeFK)
	if len(queriesUnsafeFK) != 0 {
		t.Errorf("unsafe FK column produced a custom query: %d", len(queriesUnsafeFK))
	}

	// Ни один сгенерированный SQL не должен содержать небезопасных символов.
	_, safeQueries := buildNavigationEndpoints(entities)
	for id, q := range safeQueries {
		if strings.ContainsAny(q.SQL, `";--`) {
			t.Errorf("query %q contains unsafe characters: %q", id, q.SQL)
		}
	}
}

// TestTitleCase_Unicode — Задача 3: кириллица не ломается (s[:1] — байт, не руна).
func TestTitleCase_Unicode(t *testing.T) {
	cases := map[string]string{
		"":        "",
		"товары":  "Товары",
		"заказы":  "Заказы",
		"product": "Product",
		"cart":    "Cart",
	}
	for in, want := range cases {
		got := titleCase(in)
		if got != want {
			t.Errorf("titleCase(%q) = %q, want %q", in, got, want)
		}
	}
}

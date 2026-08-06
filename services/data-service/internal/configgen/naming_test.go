package configgen

import (
	"testing"

	"github.com/trash2bin/helperium/helperium-go/config"
)

// ── Фаза 2.5 fix: display-name resolution ──────────────────────────────
//
// Бенч показал: db_map отдаёт display-имя первым ("Brand (catalog_brand)"),
// модель копирует "Brand" в entity-параметр db_search/db_get/db_describe,
// и /q/* отвечает 404 unknown_entity (177 вхождений в bench-логах).
// Фикс: CanonicalEntityName резолвит display-имя обратно в canonical
// (catalog_*), чтобы /q/* принимал оба варианта.

func TestCanonicalEntityName_ResolvesDisplayName(t *testing.T) {
	entityMap := map[string]config.Entity{
		"catalog_brand":    {Name: "catalog_brand"},
		"catalog_product":  {Name: "catalog_product"},
		"catalog_category": {Name: "catalog_category"},
		"catalog_cartitem": {Name: "catalog_cartitem"},
	}
	prefixes := DefaultDisplayPrefixes()

	cases := []struct {
		name string
		want string
	}{
		{"Brand", "catalog_brand"},
		{"brand", "catalog_brand"}, // case-fold
		{"Product", "catalog_product"},
		{"Category", "catalog_category"},
		{"Cart item", "catalog_cartitem"}, // special-case из shortBusinessName
		// Полное display-имя из db_map ("Brand (catalog_brand)").
		{"Brand (catalog_brand)", "catalog_brand"},
		{"brand (catalog_brand)", "catalog_brand"},
		// Canonical остаётся canonical.
		{"catalog_brand", "catalog_brand"},
		{"catalog_product", "catalog_product"},
	}
	for _, tc := range cases {
		got := CanonicalEntityName(tc.name, prefixes, nil, entityMap)
		if got != tc.want {
			t.Errorf("CanonicalEntityName(%q) = %q, want %q", tc.name, got, tc.want)
		}
	}
}

func TestCanonicalEntityName_UnknownReturnsEmpty(t *testing.T) {
	entityMap := map[string]config.Entity{
		"catalog_brand": {Name: "catalog_brand"},
	}
	got := CanonicalEntityName("Ghost", DefaultDisplayPrefixes(), nil, entityMap)
	if got != "" {
		t.Errorf("expected empty for unknown display name, got %q", got)
	}
}

func TestCanonicalEntityName_CustomShortNames(t *testing.T) {
	entityMap := map[string]config.Entity{
		"catalog_part": {Name: "catalog_part"},
	}
	custom := map[string]string{
		"catalog_part": "Detail",
	}
	// CustomShortNames имеют приоритет в shortBusinessName → display "Detail (catalog_part)".
	got := CanonicalEntityName("Detail", DefaultDisplayPrefixes(), custom, entityMap)
	if got != "catalog_part" {
		t.Errorf("CanonicalEntityName(Detail) = %q, want catalog_part", got)
	}
}

func TestCanonicalEntityName_PluralizedDisplayName(t *testing.T) {
	entityMap := map[string]config.Entity{
		"catalog_product": {Name: "catalog_product"},
	}
	// Модель может прислать "Products" (множественное число display).
	got := CanonicalEntityName("Products", DefaultDisplayPrefixes(), nil, entityMap)
	if got != "catalog_product" {
		t.Errorf("CanonicalEntityName(Products) = %q, want catalog_product", got)
	}
}

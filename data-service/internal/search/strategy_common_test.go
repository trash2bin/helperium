package search

import (
	"testing"

	"github.com/trash2bin/helperium/helperium-go/config"
)

// ── Фаза 2.5 fix: selectClause compact использует name-preference helper ─
// и skip'ает строковый PK (раньше [id,id] для string-id сущностей).
func TestSelectClause_Compact_PrefersNameAndSkipsStringPK(t *testing.T) {
	tPK := true
	// String PK + article раньше name — раньше дал бы [id, id] (баг).
	entity := config.Entity{
		IDColumn: "id",
		Fields: []config.EntityField{
			{Name: "sku", Column: "sku", Type: config.FieldTypeString, PrimaryKey: &tPK},
			{Name: "article", Column: "article", Type: config.FieldTypeString},
			{Name: "name", Column: "name", Type: config.FieldTypeString},
		},
	}
	cl := selectClause(entity, map[string][]string{}, &testAdapter{})
	if len(cl.Columns) != 2 {
		t.Fatalf("compact selectClause must have exactly 2 columns (id + name), got %v", cl.Columns)
	}
	// Первая — id (не строковый PK sku!).
	if cl.Columns[0] != `"id"` {
		t.Errorf("first column must be id, got %v", cl.Columns)
	}
	// Вторая — name (предпочтение name, не article).
	if cl.Columns[1] != `"name"` {
		t.Errorf("second column must be name (prefer name over article), got %v", cl.Columns)
	}
}

func TestSelectClause_Compact_NoNameField_FirstString(t *testing.T) {
	tPK := true
	entity := config.Entity{
		IDColumn: "id",
		Fields: []config.EntityField{
			{Name: "id", Column: "id", Type: config.FieldTypeInt, PrimaryKey: &tPK},
			{Name: "code", Column: "code", Type: config.FieldTypeString},
			{Name: "desc", Column: "desc", Type: config.FieldTypeString},
		},
	}
	cl := selectClause(entity, map[string][]string{}, &testAdapter{})
	if len(cl.Columns) != 2 {
		t.Fatalf("compact selectClause must have 2 columns, got %v", cl.Columns)
	}
	if cl.Columns[1] != `"code"` {
		t.Errorf("second column must be first string (code), got %v", cl.Columns)
	}
}

func TestParseOffset_Capped(t *testing.T) {
	tests := []struct {
		name  string
		query map[string][]string
		want  int
	}{
		{
			name:  "no offset returns 0",
			query: map[string][]string{},
			want:  0,
		},
		{
			name:  "regular offset",
			query: map[string][]string{"offset": {"50"}},
			want:  50,
		},
		{
			name:  "huge offset capped at 100000",
			query: map[string][]string{"offset": {"99999999"}},
			want:  100000,
		},
		{
			name:  "exactly at cap",
			query: map[string][]string{"offset": {"100000"}},
			want:  100000,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := parseOffset(tt.query)
			if got != tt.want {
				t.Errorf("parseOffset(%v) = %d, want %d", tt.query, got, tt.want)
			}
		})
	}
}

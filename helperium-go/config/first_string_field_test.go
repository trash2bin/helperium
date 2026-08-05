package config

import "testing"

// ── Фаза 2.5 fix: name-preference preview ──────────────────────────────
//
// Бенч (scout-1): preview показывает артикул (EXT-01392) вместо названия
// товара, потому что FirstStringFieldColumn берёт ПЕРВУЮ строковую колонку
// по схеме, а у Django article стоит раньше name. Фикс: предпочитать
// name/title/full_name, иначе первая строковая.
//
// Дополнительно: selectClause compact должен использовать тот же helper
// (а не дублировать логику без skip строкового PK → [id,id]).

func TestFirstStringFieldColumn_PrefersName(t *testing.T) {
	tPK := true
	e := Entity{
		Fields: []EntityField{
			{Name: "id", Column: "id", Type: FieldTypeInt, PrimaryKey: &tPK},
			{Name: "article", Column: "article", Type: FieldTypeString},
			{Name: "name", Column: "name", Type: FieldTypeString},
		},
	}
	// name должен выиграть, хотя article идёт первым.
	if got := e.FirstStringFieldColumn(); got != "name" {
		t.Errorf("FirstStringFieldColumn() = %q, want 'name' (prefer name over article)", got)
	}
}

func TestFirstStringFieldColumn_TitleFullName(t *testing.T) {
	tPK := true
	e := Entity{
		Fields: []EntityField{
			{Name: "id", Column: "id", Type: FieldTypeInt, PrimaryKey: &tPK},
			{Name: "slug", Column: "slug", Type: FieldTypeString},
			{Name: "full_name", Column: "full_name", Type: FieldTypeString},
		},
	}
	if got := e.FirstStringFieldColumn(); got != "full_name" {
		t.Errorf("FirstStringFieldColumn() = %q, want 'full_name'", got)
	}

	e2 := Entity{
		Fields: []EntityField{
			{Name: "id", Column: "id", Type: FieldTypeInt, PrimaryKey: &tPK},
			{Name: "article", Column: "article", Type: FieldTypeString},
			{Name: "title", Column: "title", Type: FieldTypeString},
		},
	}
	if got := e2.FirstStringFieldColumn(); got != "title" {
		t.Errorf("FirstStringFieldColumn() = %q, want 'title'", got)
	}
}

func TestFirstStringFieldColumn_FallsBackToFirstString(t *testing.T) {
	tPK := true
	e := Entity{
		Fields: []EntityField{
			{Name: "id", Column: "id", Type: FieldTypeInt, PrimaryKey: &tPK},
			{Name: "code", Column: "code", Type: FieldTypeString},
			{Name: "desc", Column: "desc", Type: FieldTypeString},
		},
	}
	// Нет name/title/full_name → первая строковая (code).
	if got := e.FirstStringFieldColumn(); got != "code" {
		t.Errorf("FirstStringFieldColumn() = %q, want 'code'", got)
	}
}

func TestFirstStringFieldColumn_SkipsStringPK(t *testing.T) {
	tPK := true
	e := Entity{
		Fields: []EntityField{
			{Name: "student_id", Column: "student_id", Type: FieldTypeString, PrimaryKey: &tPK},
			{Name: "full_name", Column: "full_name", Type: FieldTypeString},
		},
	}
	// Строковый PK не должен стать name-колонкой (selectClause compact [id,id] баг).
	if got := e.FirstStringFieldColumn(); got != "full_name" {
		t.Errorf("FirstStringFieldColumn() = %q, want 'full_name' (skip string PK)", got)
	}
}

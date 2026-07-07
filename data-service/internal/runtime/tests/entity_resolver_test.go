package runtime_test

import (
	"testing"

	"github.com/agent-tutor/data-service/internal/runtime"
)

// TestEntityResolver_AllEntities - тестирует возврат всех сущностей
func TestEntityResolver_AllEntities(t *testing.T) {
	// Создаем резолвер через NewEntityResolver чтобы не доступ к непроэкспортированным полям
	entities := []runtime.Entity{
		{Name: "customer", Table: "customers"},
		{Name: "order", Table: "orders"},
		{Name: "product", Table: "products"},
	}
	resolver, err := runtime.NewEntityResolver(entities)
	if err != nil {
		t.Fatalf("Failed to create entity resolver: %v", err)
	}

	result := resolver.AllEntities()
	if len(result) != 3 {
		t.Errorf("AllEntities() length = %d, want 3", len(result))
	}

	// Проверяем, что все ожидаемые имена присутствуют (порядок не гарантируется)
	expected := map[string]bool{"customer": true, "order": true, "product": true}
	for _, name := range result {
		if !expected[name] {
			t.Errorf("unexpected entity name: %s", name)
		}
		delete(expected, name)
	}

	// Проверяем, что все ожидаемые имена были найдены
	if len(expected) != 0 {
		t.Errorf("missing entity names: %v", expected)
	}
}

// TestEntityResolver_ColumnFor - тестирует поиск имени колонки по публичному полю
func TestEntityResolver_ColumnFor(t *testing.T) {
	tests := []struct {
		name           string
		entities       []runtime.Entity
		publicField    string
		expectedColumn string
		found          bool
	}{
		{"exact match", []runtime.Entity{{Fields: []runtime.EntityField{{Name: "ID", Column: "id"}}}}, "ID", "id", true},
		{"case sensitive", []runtime.Entity{{Fields: []runtime.EntityField{{Name: "id", Column: "identifier"}}}}, "ID", "", false},
		{"not found", []runtime.Entity{{Fields: []runtime.EntityField{{Name: "name", Column: "name"}}}}, "id", "", false},
		{"multiple fields", []runtime.Entity{{Fields: []runtime.EntityField{
			{Name: "first_name", Column: "first_name"},
			{Name: "last_name", Column: "last_name"},
			{Name: "email", Column: "email_address"},
		}}}, "email", "email_address", true},
		{"empty fields", []runtime.Entity{{Fields: []runtime.EntityField{}}}, "any", "", false},
		{"nil entity", []runtime.Entity{{}}, "field", "", false},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			resolver, err := runtime.NewEntityResolver(tt.entities)
			if err != nil {
				t.Fatalf("Failed to create entity resolver: %v", err)
			}

			// Для простоты берем первую сущность (она же единственная в наших тестах)
			entity := tt.entities[0]
			column, found := resolver.ColumnFor(entity, tt.publicField)
			if found != tt.found {
				t.Errorf("ColumnFor(%q, %q) found = %v, want %v", entity.Name, tt.publicField, found, tt.found)
				return
			}
			if found && column != tt.expectedColumn {
				t.Errorf("ColumnFor(%q, %q) = %q, want %q", entity.Name, tt.publicField, column, tt.expectedColumn)
			}
		})
	}
}

// TestEntityResolver_PublicFor - тестирует поиск публичного поля по имени колонки
func TestEntityResolver_PublicFor(t *testing.T) {
	tests := []struct {
		name           string
		entities       []runtime.Entity
		column         string
		expectedField  string
		found          bool
	}{
		{"exact match", []runtime.Entity{{Fields: []runtime.EntityField{{Name: "user_id", Column: "user_id"}}}}, "user_id", "user_id", true},
		{"case sensitive", []runtime.Entity{{Fields: []runtime.EntityField{{Name: "userId", Column: "user_id"}}}}, "USER_ID", "", false},
		{"not found", []runtime.Entity{{Fields: []runtime.EntityField{{Name: "name", Column: "name"}}}}, "id", "", false},
		{"multiple fields", []runtime.Entity{{Fields: []runtime.EntityField{
			{Name: "first_name", Column: "fname"},
			{Name: "last_name", Column: "lname"},
			{Name: "email", Column: "email_addr"},
		}}}, "email_addr", "email", true},
		{"empty fields", []runtime.Entity{{Fields: []runtime.EntityField{}}}, "any", "", false},
		{"nil entity", []runtime.Entity{{}}, "column", "", false},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			resolver, err := runtime.NewEntityResolver(tt.entities)
			if err != nil {
				t.Fatalf("Failed to create entity resolver: %v", err)
			}

			// Для простоты берем первую сущность (она же единственная в наших тестах)
			entity := tt.entities[0]
			field, found := resolver.PublicFor(entity, tt.column)
			if found != tt.found {
				t.Errorf("PublicFor(%q, %q) found = %v, want %v", entity.Name, tt.column, found, tt.found)
				return
			}
			if found && field != tt.expectedField {
				t.Errorf("PublicFor(%q, %q) = %q, want %q", entity.Name, tt.column, field, tt.expectedField)
			}
		})
	}
}

// TestEntityResolver_CachingBehavior - тестирует, что методы не изменяют состояние
func TestEntityResolver_CachingBehavior(t *testing.T) {
	originalEntities := []runtime.Entity{
		{
			Name: "test",
			Fields: []runtime.EntityField{
				{Name: "id", Column: "ident"},
				{Name: "name", Column: "full_name"},
			},
		},
	}
	resolver, err := runtime.NewEntityResolver(originalEntities)
	if err != nil {
		t.Fatalf("Failed to create entity resolver: %v", err)
	}

	// Выполняем несколько операций
	_, _ = resolver.ColumnFor(originalEntities[0], "id")
	_, _ = resolver.PublicFor(originalEntities[0], "ident")
	_ = resolver.AllEntities()

	// Проверяем, что исходные сущности не изменились
	// (мы не можем напрямую проверить внутреннее состояние резолвера,
	//  но мы можем проверить, что результаты функций остаются одинаковыми)
	
	// Первый вызов
	col1, _ := resolver.ColumnFor(originalEntities[0], "id")
	pub1, _ := resolver.PublicFor(originalEntities[0], "ident")
	ents1 := resolver.AllEntities()
	
	// Второй вызов
	col2, _ := resolver.ColumnFor(originalEntities[0], "id")
	pub2, _ := resolver.PublicFor(originalEntities[0], "ident")
	ents2 := resolver.AllEntities()
	
	// Результаты должны быть одинаковыми
	if col1 != col2 {
		t.Errorf("ColumnFor returned different results: %s vs %s", col1, col2)
	}
	if pub1 != pub2 {
		t.Errorf("PublicFor returned different results: %s vs %s", pub1, pub2)
	}
	if len(ents1) != len(ents2) {
		t.Errorf("AllEntities returned different lengths: %d vs %d", len(ents1), len(ents2))
	}
	
	// Проверяем значения
	if col1 != "ident" {
		t.Errorf("ColumnFor(\"id\") = %s, want \"ident\"", col1)
	}
	if pub1 != "id" {
		t.Errorf("PublicFor(\"ident\") = %s, want \"id\"", pub1)
	}
}

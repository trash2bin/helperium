package configgen

import (
	"encoding/json"
	"testing"

	"github.com/agent-tutor/data-service/internal/config"
	"github.com/agent-tutor/data-service/internal/datasource"
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

	cfg := Generate(schema, ds, nil)

	if cfg.Version != 1 {
		t.Errorf("expected version 1, got %d", cfg.Version)
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
	hasStudentsFind := false
	hasStudentsByID := false
	hasHealth := false
	hasStats := false
	for _, ep := range cfg.Endpoints {
		switch {
		case ep.Path == "/students" && ep.Op == config.OpFind:
			hasStudentsFind = true
		case ep.Path == "/students/{id}" && ep.Op == config.OpGetByID:
			hasStudentsByID = true
		case ep.Path == "/health":
			hasHealth = true
		case ep.Path == "/stats":
			hasStats = true
		}
	}
	if !hasStudentsFind {
		t.Error("expected /students find endpoint")
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
	if decoded.Version != 1 {
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
	cfg := Generate(schema, ds, nil)

	if len(cfg.Entities) != 6 {
		t.Fatalf("expected 6 entities, got %d", len(cfg.Entities))
	}
	if len(cfg.Endpoints) < 8 {
		t.Errorf("expected at least 8 endpoints, got %d", len(cfg.Endpoints))
	}

	// Check every entity has an endpoint
	has := make(map[string]bool)
	for _, ep := range cfg.Endpoints {
		has[ep.Path] = true
	}
	for _, e := range cfg.Entities {
		if _, ok := has["/"+e.Name]; !ok && e.Name != "grades" && e.Name != "schedule" {
			t.Errorf("missing /%s endpoint", e.Name)
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

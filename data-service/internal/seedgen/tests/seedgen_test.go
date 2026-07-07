package seedgen_test

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/agent-tutor/data-service/internal/seedgen"
)

// TestLoad_ValidSeed — загрузка корректного seed.json
func TestLoad_ValidSeed(t *testing.T) {
	dir := t.TempDir()

	seedJSON := `{
		"groups": [
			{"id": "g1", "name": "Group A", "speciality": "CS"}
		],
		"students": [
			{"id": "s1", "name": "John", "group_id": "g1"}
		],
		"teachers": [],
		"disciplines": [],
		"schedule": [],
		"grades": []
	}`

	seedPath := filepath.Join(dir, "seed.json")
	if err := os.WriteFile(seedPath, []byte(seedJSON), 0644); err != nil {
		t.Fatalf("write seed.json: %v", err)
	}

	seed, err := seedgen.Load(seedPath)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if seed == nil {
		t.Fatal("expected non-nil seed")
	}
	if len(seed.Groups) != 1 {
		t.Errorf("expected 1 group, got %d", len(seed.Groups))
	}
	if len(seed.Students) != 1 {
		t.Errorf("expected 1 student, got %d", len(seed.Students))
	}
	if seed.Groups[0].Name != "Group A" {
		t.Errorf("expected 'Group A', got %s", seed.Groups[0].Name)
	}
}

// TestLoad_EmptySeed — пустой seed без данных
func TestLoad_EmptySeed(t *testing.T) {
	dir := t.TempDir()
	seedPath := filepath.Join(dir, "seed.json")
	os.WriteFile(seedPath, []byte(`{"groups":[],"students":[],"teachers":[],"disciplines":[],"schedule":[],"grades":[]}`), 0644)

	seed, err := seedgen.Load(seedPath)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if seed == nil {
		t.Fatal("expected non-nil seed")
	}
}

// TestLoad_FileNotFound
func TestLoad_FileNotFound(t *testing.T) {
	_, err := seedgen.Load("/nonexistent/path/seed.json")
	if err == nil {
		t.Fatal("expected error for nonexistent file")
	}
}

// TestLoad_InvalidJSON
func TestLoad_InvalidJSON(t *testing.T) {
	dir := t.TempDir()
	seedPath := filepath.Join(dir, "seed.json")
	os.WriteFile(seedPath, []byte("{bad json}"), 0644)

	_, err := seedgen.Load(seedPath)
	if err == nil {
		t.Fatal("expected error for invalid JSON")
	}
}

// TestLoad_AllFields — все поля заполнены
func TestLoad_AllFields(t *testing.T) {
	dir := t.TempDir()

	seedJSON := `{
		"groups": [{"id":"g1","name":"G1","speciality":"CS"}],
		"students": [{"id":"s1","name":"Alice","group_id":"g1","course":1}],
		"teachers": [{"id":"t1","name":"Dr. X","disciplines":["math"]}],
		"disciplines": [{"id":"d1","name":"Math","description":"Algebra"}],
		"schedule": [{"id":"sch1","group_id":"g1","day":"mon","lessons":[{"discipline_id":"d1","discipline_name":"Math","teacher_name":"Dr. X","type":"lecture","room":101,"time_slot":"10:00","week_type":"all"}]}],
		"grades": [{"id":"gr1","student_id":"s1","discipline_id":"d1","grade":"5","date":"2024-01-01"}]
	}`

	seedPath := filepath.Join(dir, "seed.json")
	os.WriteFile(seedPath, []byte(seedJSON), 0644)

	seed, err := seedgen.Load(seedPath)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if len(seed.Groups) != 1 || len(seed.Students) != 1 || len(seed.Teachers) != 1 ||
		len(seed.Disciplines) != 1 || len(seed.Schedule) != 1 || len(seed.Grades) != 1 {
		t.Errorf("expected all 6 fields to have 1 entry each, got groups=%d students=%d teachers=%d disciplines=%d schedule=%d grades=%d",
			len(seed.Groups), len(seed.Students), len(seed.Teachers), len(seed.Disciplines), len(seed.Schedule), len(seed.Grades))
	}
}

// TestLoad_NonJSON — файл существует, но не JSON
func TestLoad_NonJSON(t *testing.T) {
	dir := t.TempDir()
	seedPath := filepath.Join(dir, "seed.json")
	os.WriteFile(seedPath, []byte("this is not json"), 0644)

	_, err := seedgen.Load(seedPath)
	if err == nil {
		t.Fatal("expected error for non-JSON content")
	}
}

// TestErrDatabaseNotEmpty — проверяем что ошибка определена
func TestErrDatabaseNotEmpty(t *testing.T) {
	if seedgen.ErrDatabaseNotEmpty == nil {
		t.Fatal("ErrDatabaseNotEmpty should be defined")
	}
	if seedgen.ErrDatabaseNotEmpty.Error() == "" {
		t.Fatal("ErrDatabaseNotEmpty should have a message")
	}
}

// Package server_test — integration тесты data-service через config-driven роутер.
package server_test

import (
	"context"
	"database/sql"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"

	_ "modernc.org/sqlite"

	"github.com/agent-tutor/data-service/internal/config"
	"github.com/agent-tutor/data-service/internal/seedgen"
	"github.com/agent-tutor/data-service/internal/server"
)

// testSchema — DDL для in-memory SQLite в тестах.
const testSchema = `
CREATE TABLE IF NOT EXISTS groups (
    id TEXT PRIMARY KEY,
    name TEXT,
    speciality TEXT
);
CREATE TABLE IF NOT EXISTS students (
    id TEXT PRIMARY KEY,
    name TEXT,
    group_id TEXT,
    course INTEGER,
    FOREIGN KEY (group_id) REFERENCES groups (id)
);
CREATE TABLE IF NOT EXISTS teachers (
    id TEXT PRIMARY KEY,
    name TEXT,
    disciplines_json TEXT
);
CREATE TABLE IF NOT EXISTS disciplines (
    id TEXT PRIMARY KEY,
    name TEXT,
    description TEXT
);
CREATE TABLE IF NOT EXISTS grades (
    id TEXT PRIMARY KEY,
    student_id TEXT,
    discipline_id TEXT,
    grade TEXT,
    date TEXT,
    FOREIGN KEY (student_id) REFERENCES students (id),
    FOREIGN KEY (discipline_id) REFERENCES disciplines (id)
);
CREATE TABLE IF NOT EXISTS schedule (
    id TEXT PRIMARY KEY,
    day TEXT,
    group_id TEXT,
    lessons_json TEXT,
    FOREIGN KEY (group_id) REFERENCES groups (id)
);
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source_path TEXT NOT NULL UNIQUE,
    mime_type TEXT NOT NULL,
    discipline_id TEXT,
    created_at TEXT NOT NULL,
    metadata_json TEXT,
    FOREIGN KEY (discipline_id) REFERENCES disciplines (id)
);
`

// loadTestSeed заливает компактный тестовый seed (seedgen.TestSeed) в in-memory DB.
func loadTestSeed(t *testing.T, db *sql.DB) {
	t.Helper()

	if err := seedgen.Apply(context.Background(), sqlExecAdapter{db}, seedgen.TestSeed); err != nil {
		t.Fatalf("seedgen.Apply: %v", err)
	}
}

// sqlExecAdapter — заглушка для тестов, оборачивает *sql.DB в ExecContext + QueryRowContext.
type sqlExecAdapter struct{ *sql.DB }

func (a sqlExecAdapter) Close() error { return a.DB.Close() }

// testDB открывает in-memory SQLite и заливает TestSeed.
func testDB(t *testing.T) *sql.DB {
	t.Helper()

	db, err := sql.Open("sqlite", ":memory:?_journal_mode=WAL&_foreign_keys=on")
	if err != nil {
		t.Fatalf("open in-memory db: %v", err)
	}

	if _, err := db.ExecContext(context.Background(), testSchema); err != nil {
		db.Close()
		t.Fatalf("apply schema: %v", err)
	}

	loadTestSeed(t, db)
	return db
}

// testConfig возвращает конфиг, эквивалентный TestSeed.
func testConfig(t *testing.T) *config.Config {
	t.Helper()

	return &config.Config{
		Version: 1,
		DataSource: config.DataSourceConfig{
			Driver:   "sqlite",
			DSN:      ":memory:",
			PoolSize: intPtr(1),
			ReadOnly: boolPtr(true),
		},
		Entities: []config.Entity{
			{
				Name: "group", Table: "groups", IDColumn: "id",
				Fields: []config.EntityField{
					{Name: "id", Column: "id", Type: "string", Nullable: boolPtr(false), PrimaryKey: boolPtr(true)},
					{Name: "name", Column: "name", Type: "string", Nullable: boolPtr(false)},
					{Name: "speciality", Column: "speciality", Type: "string", Nullable: boolPtr(false)},
				},
			},
			{
				Name: "student", Table: "students", IDColumn: "id",
				Fields: []config.EntityField{
					{Name: "id", Column: "id", Type: "string", Nullable: boolPtr(false), PrimaryKey: boolPtr(true)},
					{Name: "full_name", Column: "name", Type: "string", Nullable: boolPtr(false)},
					{Name: "course", Column: "course", Type: "int", Nullable: boolPtr(true)},
				},
			},
			{
				Name: "teacher", Table: "teachers", IDColumn: "id",
				Fields: []config.EntityField{
					{Name: "id", Column: "id", Type: "string", Nullable: boolPtr(false), PrimaryKey: boolPtr(true)},
					{Name: "full_name", Column: "name", Type: "string", Nullable: boolPtr(false)},
				},
			},
			{
				Name: "discipline", Table: "disciplines", IDColumn: "id",
				Fields: []config.EntityField{
					{Name: "id", Column: "id", Type: "string", Nullable: boolPtr(false), PrimaryKey: boolPtr(true)},
					{Name: "name", Column: "name", Type: "string", Nullable: boolPtr(false)},
					{Name: "description", Column: "description", Type: "string", Nullable: boolPtr(false)},
				},
			},
			{
				Name: "grade", Table: "grades", IDColumn: "id",
				Fields: []config.EntityField{
					{Name: "id", Column: "id", Type: "string", Nullable: boolPtr(false), PrimaryKey: boolPtr(true)},
					{Name: "student_id", Column: "student_id", Type: "string", Nullable: boolPtr(false)},
					{Name: "discipline_id", Column: "discipline_id", Type: "string", Nullable: boolPtr(false)},
					{Name: "grade", Column: "grade", Type: "string", Nullable: boolPtr(false)},
					{Name: "date", Column: "date", Type: "date", Nullable: boolPtr(false)},
				},
			},
			{
				Name: "schedule", Table: "schedule", IDColumn: "id",
				Fields: []config.EntityField{
					{Name: "id", Column: "id", Type: "string", Nullable: boolPtr(false), PrimaryKey: boolPtr(true)},
					{Name: "day", Column: "day", Type: "string", Nullable: boolPtr(false)},
					{Name: "group_id", Column: "group_id", Type: "string", Nullable: boolPtr(false)},
				},
			},
		},
		Endpoints: []config.Endpoint{
			{Method: "GET", Path: "/health", Op: "builtin_health"},
			{Method: "GET", Path: "/stats", Op: "builtin_stats"},
			{Method: "GET", Path: "/students/{id}", Op: "get_by_id", Entity: "student"},
			{Method: "GET", Path: "/students", Op: "find", Entity: "student", SearchField: "full_name", QueryParam: "name"},
			{Method: "GET", Path: "/students/{id}/grades", Op: "custom_query", QueryID: "student_grades",
				Params: []config.EndpointParam{{Name: "id", In: "path", Required: boolPtr(true)}}},
			{Method: "GET", Path: "/groups/{id}/schedule", Op: "custom_query", QueryID: "group_schedule",
				Params: []config.EndpointParam{{Name: "id", In: "path", Required: boolPtr(true)}}},
			{Method: "GET", Path: "/grades", Op: "custom_query", QueryID: "all_grades"},
			{Method: "GET", Path: "/schedule", Op: "custom_query", QueryID: "all_schedule"},
			{Method: "GET", Path: "/disciplines", Op: "list", Entity: "discipline"},
			{Method: "GET", Path: "/teachers", Op: "find", Entity: "teacher", SearchField: "full_name", QueryParam: "name"},
			{Method: "GET", Path: "/students/{id}/disciplines", Op: "custom_query", QueryID: "student_disciplines",
				Params: []config.EndpointParam{{Name: "id", In: "path", Required: boolPtr(true)}}},
		},
		CustomQueries: map[string]config.CustomQuery{
			"student_grades": {
				SQL:    "SELECT g.id, g.student_id, g.discipline_id, COALESCE(d.name, 'Unknown') AS discipline_name, g.grade, g.date FROM grades g LEFT JOIN disciplines d ON d.id = g.discipline_id WHERE g.student_id = ? ORDER BY g.date DESC",
				Params: []string{"id"},
				ResultMapping: map[string]config.ResultMappingField{
					"id":              {Type: "string"},
					"student_id":      {Type: "string"},
					"discipline_id":   {Type: "string"},
					"discipline_name": {Type: "string", Nullable: boolPtr(true)},
					"grade":           {Type: "string"},
					"date":            {Type: "date"},
				},
				MaxRows: 500,
			},
			"group_schedule": {
				SQL:    "SELECT s.id, s.day, s.group_id, g.name AS group_name, g.speciality, s.lessons_json FROM schedule s LEFT JOIN groups g ON g.id = s.group_id WHERE s.group_id = ?",
				Params: []string{"id"},
				ResultMapping: map[string]config.ResultMappingField{
					"id":           {Type: "string"},
					"day":          {Type: "string"},
					"group_id":     {Type: "string"},
					"group_name":   {Type: "string", Nullable: boolPtr(true)},
					"speciality":   {Type: "string", Nullable: boolPtr(true)},
					"lessons_json": {Type: "string"},
				},
				MaxRows: 1000,
			},
			"all_grades": {
				SQL: "SELECT g.id, g.student_id, COALESCE(s.name, 'Unknown') AS student_name, g.discipline_id, COALESCE(d.name, 'Unknown') AS discipline_name, g.grade, g.date FROM grades g LEFT JOIN students s ON s.id = g.student_id LEFT JOIN disciplines d ON d.id = g.discipline_id ORDER BY g.date DESC LIMIT 80",
				ResultMapping: map[string]config.ResultMappingField{
					"id":              {Type: "string"},
					"student_id":      {Type: "string"},
					"student_name":    {Type: "string", Nullable: boolPtr(true)},
					"discipline_id":   {Type: "string"},
					"discipline_name": {Type: "string", Nullable: boolPtr(true)},
					"grade":           {Type: "string"},
					"date":            {Type: "date"},
				},
				MaxRows: 80,
			},
			"all_schedule": {
				SQL: "SELECT s.id, s.day, s.group_id, g.name AS group_name, g.speciality, s.lessons_json FROM schedule s LEFT JOIN groups g ON g.id = s.group_id ORDER BY g.name, s.day",
				ResultMapping: map[string]config.ResultMappingField{
					"id":           {Type: "string"},
					"day":          {Type: "string"},
					"group_id":     {Type: "string", Nullable: boolPtr(true)},
					"group_name":   {Type: "string", Nullable: boolPtr(true)},
					"speciality":   {Type: "string", Nullable: boolPtr(true)},
					"lessons_json": {Type: "string"},
				},
				MaxRows: 5000,
			},
			"student_disciplines": {
				SQL: "SELECT d.id, d.name, d.description FROM disciplines d WHERE d.id IN (SELECT DISTINCT json_extract(value, '$.discipline_id') FROM schedule s, json_each(s.lessons_json) WHERE s.group_id = (SELECT group_id FROM students WHERE id = ?) AND json_extract(value, '$.discipline_id') IS NOT NULL)",
				Params: []string{"id"},
				ResultMapping: map[string]config.ResultMappingField{
					"id":          {Type: "string"},
					"name":        {Type: "string"},
					"description": {Type: "string"},
				},
				MaxRows: 100,
			},
		},
		Stats: &config.StatsConfig{
			Counters: []config.Counter{
				{Name: "students", Entity: "student"},
				{Name: "teachers", Entity: "teacher"},
				{Name: "disciplines", Entity: "discipline"},
				{Name: "grades", Entity: "grade"},
				{Name: "schedule", Entity: "schedule"},
			},
		},
	}
}

// newTestServer создаёт тестовый HTTP-сервер с config-driven роутером.
func newTestServer(t *testing.T) *httptest.Server {
	t.Helper()
	sqlDB := testDB(t)
	cfg := testConfig(t)
	adapter := &testSQLite{db: sqlDB}

	router, err := server.NewRouterFromConfig(cfg, adapter, adapter, nil, "")
	if err != nil {
		t.Fatalf("NewRouterFromConfig: %v", err)
	}

	ts := httptest.NewServer(router)
	t.Cleanup(func() {
		ts.Close()
		sqlDB.Close()
	})
	return ts
}

// testSQLite — обёртка над *sql.DB, реализующая runtime.AdapterSubset.
type testSQLite struct{ db *sql.DB }

func (a *testSQLite) QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error) {
	return a.db.QueryContext(ctx, query, args...)
}
func (a *testSQLite) PingContext(ctx context.Context) error { return a.db.PingContext(ctx) }
func (a *testSQLite) QuoteIdentifier(name string) string   { return `"` + name + `"` }
func (a *testSQLite) TranslatePlaceholder(index int) string { return "?" }

func getJSON[T any](t *testing.T, url string) (int, T) {
	t.Helper()
	resp, err := http.Get(url)
	if err != nil {
		t.Fatalf("GET %s: %v", url, err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatalf("read body %s: %v", url, err)
	}

	var result T
	if err := json.Unmarshal(body, &result); err != nil {
		t.Fatalf("unmarshal %s: %v\nbody: %s", url, err, string(body))
	}

	return resp.StatusCode, result
}

// ══════════════════════════════════════════════════════════════════════
// Health
// ══════════════════════════════════════════════════════════════════════

func TestHealth(t *testing.T) {
	ts := newTestServer(t)

	status, body := getJSON[map[string]string](t, ts.URL+"/health")

	if status != 200 {
		t.Errorf("expected 200, got %d", status)
	}
	if body["status"] != "ok" {
		t.Errorf("expected status ok, got %q", body["status"])
	}
	if body["db"] != "ok" {
		t.Errorf("expected db ok, got %q", body["db"])
	}
}

// ══════════════════════════════════════════════════════════════════════
// Students
// ══════════════════════════════════════════════════════════════════════

func TestGetStudent(t *testing.T) {
	ts := newTestServer(t)

	status, s := getJSON[map[string]any](t, ts.URL+"/students/s1")

	if status != 200 {
		t.Fatalf("expected 200, got %d", status)
	}
	if s["full_name"] != "Иван Петров Иванович" {
		t.Errorf("expected Иван Петров Иванович, got %v", s["full_name"])
	}
	if s["course"] != float64(2) {
		t.Errorf("expected course 2, got %v", s["course"])
	}
}

func TestGetStudentNotFound(t *testing.T) {
	ts := newTestServer(t)

	status, body := getJSON[map[string]string](t, ts.URL+"/students/nonexistent")

	if status != 404 {
		t.Errorf("expected 404, got %d", status)
	}
	// Generic handler возвращает "not_found" вместо старого "not found"
	if body["error"] != "not_found" {
		t.Errorf("expected 'not_found', got %q", body["error"])
	}
}

func TestFindStudentByName(t *testing.T) {
	ts := newTestServer(t)

	status, s := getJSON[map[string]any](t,
		ts.URL+"/students?name="+pathEncode("Мария Сидорова Ивановна"))

	if status != 200 {
		t.Fatalf("expected 200, got %d", status)
	}
	if s["full_name"] != "Мария Сидорова Ивановна" {
		t.Errorf("expected Мария Сидорова Ивановна, got %v", s["full_name"])
	}
	if s["course"] != float64(3) {
		t.Errorf("expected course 3, got %v", s["course"])
	}
}

func TestFindStudentByNameNotFound(t *testing.T) {
	ts := newTestServer(t)

	status, _ := getJSON[map[string]string](t,
		ts.URL+"/students?name=Неизвестный+Студент")

	if status != 404 {
		t.Errorf("expected 404, got %d", status)
	}
}

func TestGetStudentDisciplines(t *testing.T) {
	ts := newTestServer(t)

	status, disciplines := getJSON[[]map[string]any](t,
		ts.URL+"/students/s1/disciplines")

	if status != 200 {
		t.Fatalf("expected 200, got %d", status)
	}
	if len(disciplines) != 3 {
		t.Fatalf("expected 3 disciplines, got %d", len(disciplines))
	}
	if disciplines[0]["name"] != "Алгоритмы и структуры данных" {
		t.Errorf("unexpected first discipline: %v", disciplines[0]["name"])
	}
}

// ══════════════════════════════════════════════════════════════════════
// Grades
// ══════════════════════════════════════════════════════════════════════

func TestGetStudentGrades(t *testing.T) {
	ts := newTestServer(t)

	status, grades := getJSON[[]map[string]any](t,
		ts.URL+"/students/s1/grades")

	if status != 200 {
		t.Fatalf("expected 200, got %d", status)
	}
	if len(grades) != 2 {
		t.Fatalf("expected 2 grades, got %d", len(grades))
	}
	if grades[0]["grade"] != "4" {
		t.Errorf("expected grade 4 (most recent date), got %v", grades[0]["grade"])
	}
	if grades[0]["discipline_name"] != "Базы данных" {
		t.Errorf("expected Базы данных first (date DESC), got %v", grades[0]["discipline_name"])
	}
}

func TestGetStudentGradesAll(t *testing.T) {
	ts := newTestServer(t)

	status, grades := getJSON[[]map[string]any](t,
		ts.URL+"/students/s1/grades")

	if status != 200 {
		t.Fatalf("expected 200, got %d", status)
	}
	// Generic custom_query не фильтрует по ?discipline_id= (фаза 3.3,
	// раньше это был domain-specific handler). Фильтрацию при необходимости
	// настраивают через отдельный custom_query эндпоинт.
	if len(grades) != 2 {
		t.Fatalf("expected 2 grades, got %d", len(grades))
	}
	if grades[0]["discipline_name"] != "Базы данных" {
		t.Errorf("expected Базы данных first (date DESC), got %v", grades[0]["discipline_name"])
	}
}

// ══════════════════════════════════════════════════════════════════════
// Teachers
// ══════════════════════════════════════════════════════════════════════

func TestFindTeacherByName(t *testing.T) {
	ts := newTestServer(t)

	status, teacher := getJSON[map[string]any](t,
		ts.URL+"/teachers?name="+pathEncode("Оксана Ниловна Константинова"))

	if status != 200 {
		t.Fatalf("expected 200, got %d", status)
	}
	if teacher["full_name"] != "Оксана Ниловна Константинова" {
		t.Errorf("unexpected name: %v", teacher["full_name"])
	}
}

// ══════════════════════════════════════════════════════════════════════
// Disciplines
// ══════════════════════════════════════════════════════════════════════

func TestGetAllDisciplines(t *testing.T) {
	ts := newTestServer(t)

	status, disciplines := getJSON[[]map[string]any](t,
		ts.URL+"/disciplines")

	if status != 200 {
		t.Fatalf("expected 200, got %d", status)
	}
	if len(disciplines) != 3 {
		t.Fatalf("expected 3 disciplines, got %d", len(disciplines))
	}
	if disciplines[0]["name"] != "Алгоритмы и структуры данных" {
		t.Errorf("unexpected first: %v", disciplines[0]["name"])
	}
}

// ══════════════════════════════════════════════════════════════════════
// Stats
// ══════════════════════════════════════════════════════════════════════

func TestStats(t *testing.T) {
	ts := newTestServer(t)

	status, stats := getJSON[map[string]any](t, ts.URL+"/stats")

	if status != 200 {
		t.Fatalf("expected 200, got %d", status)
	}
	if stats["students"] == 0 {
		t.Errorf("expected non-zero students, got %v", stats["students"])
	}
	if stats["teachers"] == 0 {
		t.Errorf("expected non-zero teachers, got %v", stats["teachers"])
	}
	if stats["disciplines"] == 0 {
		t.Errorf("expected non-zero disciplines, got %v", stats["disciplines"])
	}
	if stats["grades"] == 0 {
		t.Errorf("expected non-zero grades, got %v", stats["grades"])
	}
}

// ══════════════════════════════════════════════════════════════════════
// Swagger / OpenAPI
// ══════════════════════════════════════════════════════════════════════

func TestOpenAPIJSON(t *testing.T) {
	ts := newTestServer(t)

	resp, err := http.Get(ts.URL + "/openapi.json")
	if err != nil {
		t.Fatalf("GET /openapi.json: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Errorf("expected 200, got %d", resp.StatusCode)
	}

	body, _ := io.ReadAll(resp.Body)
	var spec map[string]any
	if err := json.Unmarshal(body, &spec); err != nil {
		t.Fatalf("invalid JSON: %v", err)
	}

	if spec["openapi"] != "3.1.0" {
		t.Errorf("expected openapi 3.1.0, got %v", spec["openapi"])
	}

	info, ok := spec["info"].(map[string]any)
	if !ok {
		t.Fatal("info should be object")
	}
	if info["title"] != "Data Service" {
		t.Errorf("expected title 'Data Service', got %v", info["title"])
	}
}

func TestSwaggerUI(t *testing.T) {
	ts := newTestServer(t)

	resp, err := http.Get(ts.URL + "/docs")
	if err != nil {
		t.Fatalf("GET /docs: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Errorf("expected 200, got %d", resp.StatusCode)
	}

	ct := resp.Header.Get("Content-Type")
	if len(ct) < 9 || ct[:9] != "text/html" {
		t.Errorf("expected text/html, got %q", ct)
	}
}

// ══════════════════════════════════════════════════════════════════════
// Helpers
// ══════════════════════════════════════════════════════════════════════

func pathEncode(s string) string {
	return url.PathEscape(s)
}

func intPtr(i int) *int    { return &i }
func boolPtr(b bool) *bool { return &b }

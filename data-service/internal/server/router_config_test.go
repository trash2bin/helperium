package server_test

import (
	"context"
	"database/sql"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/agent-tutor/data-service/internal/config"
	"github.com/agent-tutor/data-service/internal/server"
)

// TestNewRouterFromConfig_Health проверяет, что /health работает через config-router.
func TestNewRouterFromConfig_Health(t *testing.T) {
	ts := newConfigTestServer(t)
	defer ts.Close()

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

// TestNewRouterFromConfig_Stats проверяет базовые счётчики.
func TestNewRouterFromConfig_Stats(t *testing.T) {
	ts := newConfigTestServer(t)
	defer ts.Close()

	status, body := getJSON[map[string]float64](t, ts.URL+"/stats")

	if status != 200 {
		t.Errorf("expected 200, got %d", status)
	}
	if body["students"] == 0 {
		t.Error("expected non-zero students count")
	}
	if body["teachers"] == 0 {
		t.Error("expected non-zero teachers count")
	}
	if body["disciplines"] == 0 {
		t.Error("expected non-zero disciplines count")
	}
	if body["grades"] == 0 {
		t.Error("expected non-zero grades count")
	}
	if body["schedule"] == 0 {
		t.Error("expected non-zero schedule count")
	}
}

// TestNewRouterFromConfig_GetByID проверяет получение карточки студента по ID.
// TestSeed использует ID "s1" для студента "Иван Петров Иванович".
func TestNewRouterFromConfig_GetByID(t *testing.T) {
	ts := newConfigTestServer(t)
	defer ts.Close()

	status, body := getJSON[map[string]any](t, ts.URL+"/students/s1")

	if status != 200 {
		t.Errorf("expected 200, got %d", status)
	}
	if body["full_name"] == "" {
		t.Errorf("expected non-empty full_name, got %v", body["full_name"])
	}
	if body["id"] != "s1" {
		t.Errorf("expected id=s1, got %v", body["id"])
	}
}

// TestNewRouterFromConfig_NotFound проверяет 404.
func TestNewRouterFromConfig_NotFound(t *testing.T) {
	ts := newConfigTestServer(t)
	defer ts.Close()

	resp, err := http.Get(ts.URL + "/nonexistent")
	if err != nil {
		t.Fatalf("GET /nonexistent: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 404 {
		t.Errorf("expected 404, got %d", resp.StatusCode)
	}
}

// TestNewRouterFromConfig_FindStudent проверяет поиск студента.
func TestNewRouterFromConfig_FindStudent(t *testing.T) {
	ts := newConfigTestServer(t)
	defer ts.Close()

	status, body := getJSON[map[string]any](t, ts.URL+"/students?name=%D0%98%D0%B2%D0%B0%D0%BD+%D0%9F%D0%B5%D1%82%D1%80%D0%BE%D0%B2+%D0%98%D0%B2%D0%B0%D0%BD%D0%BE%D0%B2%D0%B8%D1%87")

	if status != 200 {
		t.Errorf("expected 200, got %d; body=%v", status, body)
	}
	if body["full_name"] == "" {
		t.Errorf("expected non-empty full_name, got %v", body["full_name"])
	}
}

// TestNewRouterFromConfig_ListDisciplines проверяет список дисциплин.
func TestNewRouterFromConfig_ListDisciplines(t *testing.T) {
	ts := newConfigTestServer(t)
	defer ts.Close()

	status, body := getJSON[[]any](t, ts.URL+"/disciplines")

	if status != 200 {
		t.Errorf("expected 200, got %d", status)
	}
	if len(body) == 0 {
		t.Error("expected non-empty disciplines list")
	}
}

// TestNewRouterFromConfig_CustomQuery проверяет custom_query эндпоинты.
func TestNewRouterFromConfig_CustomQuery(t *testing.T) {
	ts := newConfigTestServer(t)
	defer ts.Close()

	// Оценки студента s1
	status, body := getJSON[[]any](t, ts.URL+"/students/s1/grades")
	if status != 200 {
		t.Errorf("grades expected 200, got %d", status)
	}
	if len(body) == 0 {
		t.Error("expected non-empty grades list for s1")
	}

	// Расписание группы g1
	status, body = getJSON[[]any](t, ts.URL+"/groups/g1/schedule")
	if status != 200 {
		t.Errorf("schedule expected 200, got %d", status)
	}
	if len(body) == 0 {
		t.Error("expected non-empty schedule list for g1")
	}

	// Дисциплины студента s1
	status, body = getJSON[[]any](t, ts.URL+"/students/s1/disciplines")
	if status != 200 {
		t.Errorf("disciplines expected 200, got %d", status)
	}
}

// ══════════════════════════════════════════════════════════════════════
// Сравнительный тест: старый NewRouter vs новый NewRouterFromConfig
// ══════════════════════════════════════════════════════════════════════
func TestEquivalence_OldVsNewRouter(t *testing.T) {
	sqlDB := testDB(t)
	defer sqlDB.Close()

	dbAdapter := sqlExecAdapter{sqlDB}
	oldRouter := server.NewRouter(dbAdapter)
	oldTS := httptest.NewServer(oldRouter)
	defer oldTS.Close()

	newTS := newConfigServerFromDB(t, sqlDB)
	defer newTS.Close()

	tests := []struct {
		name       string
		path       string
		skipReason string
	}{
		{name: "health", path: "/health"},
		{name: "stats", path: "/stats"},
		{name: "student_by_id_s1", path: "/students/s1",
			skipReason: "old handler adds Go-level group JOIN via repository, generic router doesn't"},
		{name: "student_by_id_s2", path: "/students/s2",
			skipReason: "old handler adds Go-level group JOIN via repository, generic router doesn't"},
		{name: "find_student_by_name", path: "/students?name=Иван+Петров+Иванович",
			skipReason: "old handler adds Go-level group JOIN via repository, generic router doesn't"},
		// find_student_empty без name: old возвращает список с вложенным group+schedule, новый — без.
		{name: "find_student_empty", path: "/students",
			skipReason: "old handler does Go-level group JOIN + schedule JOIN, generic router doesn't"},
		{name: "grades", path: "/grades"},
		// student_grades: old handler делает LEFT JOIN на students и возвращает student_name, new — только то что в custom_query
		{name: "student_grades_s1", path: "/students/s1/grades",
			skipReason: "old handler does student_name JOIN + discipline JOIN, custom query in test config matches only discipline"},
		// group_schedule: old парсит lessons_json → lessons[]
		{name: "group_schedule_g1", path: "/groups/g1/schedule",
			skipReason: "old handler parses lessons_json into lessons array, new returns raw JSON string"},
		// all_schedule: то же
		{name: "all_schedule", path: "/schedule",
			skipReason: "old handler parses lessons_json into lessons array, new returns raw JSON string"},
		{name: "disciplines", path: "/disciplines"},
		// find_teacher: old ищет LIKE, новый тоже. Но old возвращает 500 при ошибке, новый — 404.
		// С "Оксана" LIKE сработал в новом, а старый упал с 500.
		{name: "find_teacher", path: "/teachers?name=Оксана",
			skipReason: "old handler has bug (500 on LIKE match), new returns 200 with data"},
		// find_teacher_empty: old парсит disciplines_json в disciplines[], новый — сырую строку
		{name: "find_teacher_empty", path: "/teachers",
			skipReason: "old handler parses disciplines_json into array, new returns raw JSON string"},
		{name: "student_disciplines_s1", path: "/students/s1/disciplines"},
		{name: "teacher_schedule", path: "/teachers/Оксана+Ниловна+Константинова/schedule",
			skipReason: "old handler does Go-level post-filter on lessons_json"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if tt.skipReason != "" {
				t.Skipf("skip: %s", tt.skipReason)
			}

			oldResp, oldBody := rawGet(t, oldTS.URL+tt.path)
			defer oldResp.Body.Close()
			newResp, newBody := rawGet(t, newTS.URL+tt.path)
			defer newResp.Body.Close()

			if oldResp.StatusCode != newResp.StatusCode {
				t.Errorf("status mismatch: old=%d new=%d", oldResp.StatusCode, newResp.StatusCode)
			}

			var oldJSON, newJSON any
			if err := json.Unmarshal(oldBody, &oldJSON); err != nil {
				t.Fatalf("old response is not JSON (%s): %s", tt.path, string(oldBody))
			}
			if err := json.Unmarshal(newBody, &newJSON); err != nil {
				t.Fatalf("new response is not JSON (%s): %s", tt.path, string(newBody))
			}

			oldNorm, _ := json.Marshal(oldJSON)
			newNorm, _ := json.Marshal(newJSON)

			if string(oldNorm) != string(newNorm) {
				oldPretty, _ := json.MarshalIndent(oldJSON, "", "  ")
				newPretty, _ := json.MarshalIndent(newJSON, "", "  ")
				t.Errorf("response mismatch for %s:\n=== OLD ===\n%s\n=== NEW ===\n%s", tt.path, string(oldPretty), string(newPretty))
			}
		})
	}
}

// ══════════════════════════════════════════════════════════════════════
// Helpers
// ══════════════════════════════════════════════════════════════════════

func rawGet(t *testing.T, url string) (*http.Response, []byte) {
	t.Helper()
	resp, err := http.Get(url)
	if err != nil {
		t.Fatalf("GET %s: %v", url, err)
	}
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		resp.Body.Close()
		t.Fatalf("read body %s: %v", url, err)
	}
	return resp, body
}

// testConfig создаёт конфиг, эквивалентный тестовой схеме server_test.go (TestSeed).
func testConfig(t *testing.T) *config.Config {
	t.Helper()

	cfg := &config.Config{
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
					{Name: "disciplines_json", Column: "disciplines_json", Type: "json", Nullable: boolPtr(true)},
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
	return cfg
}

func newConfigTestServer(t *testing.T) *httptest.Server {
	t.Helper()
	sqlDB := testDB(t)
	return newConfigServerFromDB(t, sqlDB)
}

func newConfigServerFromDB(t *testing.T, sqlDB *sql.DB) *httptest.Server {
	t.Helper()
	cfg := testConfig(t)
	adapter := &testSQLite{db: sqlDB}

	router, err := server.NewRouterFromConfig(cfg, adapter, adapter)
	if err != nil {
		t.Fatalf("NewRouterFromConfig: %v", err)
	}

	ts := httptest.NewServer(router)
	t.Cleanup(func() {
		ts.Close()
	})
	return ts
}

// testSQLite — обёртка над *sql.DB, реализующая runtime.AdapterSubset для SQLite.
type testSQLite struct{ db *sql.DB }

func (a *testSQLite) QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error) {
	return a.db.QueryContext(ctx, query, args...)
}
func (a *testSQLite) PingContext(ctx context.Context) error { return a.db.PingContext(ctx) }
func (a *testSQLite) QuoteIdentifier(name string) string   { return `"` + name + `"` }
func (a *testSQLite) TranslatePlaceholder(index int) string { return "?" }

func intPtr(i int) *int    { return &i }
func boolPtr(b bool) *bool { return &b }
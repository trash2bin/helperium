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

	"github.com/agent-tutor/data-service/internal/seedgen"
	"github.com/agent-tutor/data-service/internal/server"
)

// testSchema — DDL для in-memory SQLite в тестах.
// Должен соответствовать схеме из data-service/internal/repository/*.go SQL-запросов.
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
// Тесты ожидают конкретные ID/имена — TestSeed детерминированный.
func loadTestSeed(t *testing.T, db *sql.DB) {
	t.Helper()

	if err := seedgen.Apply(context.Background(), sqlExecAdapter{db}, seedgen.TestSeed); err != nil {
		t.Fatalf("seedgen.Apply: %v", err)
	}
}

// sqlExecAdapter — заглушка для тестов, оборачивает *sql.DB в db.DB с ExecContext.
type sqlExecAdapter struct{ *sql.DB }

func (a sqlExecAdapter) Close() error { return a.DB.Close() }

// testDB открывает in-memory SQLite и заливает fixtures/seed.json.
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

// newTestServer создаёт тестовый HTTP-сервер с in-memory БД.
func newTestServer(t *testing.T) *httptest.Server {
	t.Helper()
	db := testDB(t)
	router := server.NewRouter(sqlExecAdapter{db})
	ts := httptest.NewServer(router)
	t.Cleanup(func() {
		ts.Close()
		db.Close()
	})
	return ts
}

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

	group, ok := s["group"].(map[string]any)
	if !ok {
		t.Fatal("group should be an object")
	}
	if group["name"] != "ИВТ-21" {
		t.Errorf("expected ИВТ-21, got %v", group["name"])
	}
}

func TestGetStudentNotFound(t *testing.T) {
	ts := newTestServer(t)

	status, body := getJSON[map[string]string](t, ts.URL+"/students/nonexistent")

	if status != 404 {
		t.Errorf("expected 404, got %d", status)
	}
	if body["error"] != "not found" {
		t.Errorf("expected 'not found', got %q", body["error"])
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

func TestGetStudentGradesWithFilter(t *testing.T) {
	ts := newTestServer(t)

	status, grades := getJSON[[]map[string]any](t,
		ts.URL+"/students/s1/grades?discipline_id=d2")

	if status != 200 {
		t.Fatalf("expected 200, got %d", status)
	}
	if len(grades) != 1 {
		t.Fatalf("expected 1 grade, got %d", len(grades))
	}
	if grades[0]["discipline_name"] != "Базы данных" {
		t.Errorf("expected Базы данных, got %v", grades[0]["discipline_name"])
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

	disciplines, ok := teacher["disciplines"].([]any)
	if !ok {
		t.Fatal("disciplines should be an array")
	}
	if len(disciplines) != 2 {
		t.Errorf("expected 2 disciplines, got %d", len(disciplines))
	}
}

func TestGetTeacherSchedule(t *testing.T) {
	ts := newTestServer(t)

	status, schedule := getJSON[[]map[string]any](t,
		ts.URL+"/teachers/"+pathEncode("Оксана Ниловна Константинова")+"/schedule")

	if status != 200 {
		t.Fatalf("expected 200, got %d", status)
	}
	if len(schedule) != 1 {
		t.Fatalf("expected 1 entry, got %d", len(schedule))
	}
	lessons, ok := schedule[0]["lessons"].([]any)
	if !ok {
		t.Fatal("lessons should be array")
	}
	if len(lessons) != 2 {
		t.Errorf("expected 2 lessons, got %d", len(lessons))
	}
}

// ══════════════════════════════════════════════════════════════════════
// Schedule
// ══════════════════════════════════════════════════════════════════════

func TestGetGroupSchedule(t *testing.T) {
	ts := newTestServer(t)

	status, schedule := getJSON[[]map[string]any](t,
		ts.URL+"/groups/g1/schedule")

	if status != 200 {
		t.Fatalf("expected 200, got %d", status)
	}
	if len(schedule) != 2 {
		t.Fatalf("expected 2 entries, got %d", len(schedule))
	}
	if schedule[0]["day"] != "Понедельник" {
		t.Errorf("expected Понедельник, got %v", schedule[0]["day"])
	}
}

func TestGetGroupScheduleByDay(t *testing.T) {
	ts := newTestServer(t)

	status, schedule := getJSON[[]map[string]any](t,
		ts.URL+"/groups/g1/schedule?day=%D0%92%D1%82%D0%BE%D1%80%D0%BD%D0%B8%D0%BA") // Вторник

	if status != 200 {
		t.Fatalf("expected 200, got %d", status)
	}
	if len(schedule) != 1 {
		t.Fatalf("expected 1 entry, got %d", len(schedule))
	}
	if schedule[0]["day"] != "Вторник" {
		t.Errorf("expected Вторник, got %v", schedule[0]["day"])
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
	if stats["students"] != float64(2) {
		t.Errorf("expected 2 students, got %v", stats["students"])
	}
	if stats["teachers"] != float64(1) {
		t.Errorf("expected 1 teacher, got %v", stats["teachers"])
	}
	if stats["disciplines"] != float64(3) {
		t.Errorf("expected 3 disciplines, got %v", stats["disciplines"])
	}
	if stats["grades"] != float64(3) {
		t.Errorf("expected 3 grades, got %v", stats["grades"])
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
	if ct[:9] != "text/html" {
		t.Errorf("expected text/html, got %q", ct)
	}
}

// ══════════════════════════════════════════════════════════════════════
// Helpers
// ══════════════════════════════════════════════════════════════════════

func pathEncode(s string) string {
	// Кодируем строку для использования в URL-пути
	// url.PathEscape кодирует пробелы как %20, русские буквы как %D0%...
	return url.PathEscape(s)
}

// Package server_test — scenario-based test helpers.
package server_test

import (
	"context"
	"database/sql"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	_ "modernc.org/sqlite"

	"github.com/agent-tutor/agent-tutor-go/config"
	"github.com/agent-tutor/data-service/internal/datasource"
	"github.com/agent-tutor/data-service/internal/seedgen"
	"github.com/agent-tutor/data-service/internal/server"
)

// loadScenario reads config.json (+ optional seed.json) from scenario dir,
// creates in-memory SQLite DB, applies DDL + seed.
//
// If seed.json is absent but a <name>.data.db (or data.db) file exists,
// the helper opens that file directly (e.g. shop scenario — pre-materialized DB).
func loadScenario(t testing.TB, dir string) (*config.Config, *sql.DB) {
	t.Helper()

	// config.Load() needs specs/config.schema.json relative to CWD.
	// Point it to the project root via CONFIG_SCHEMA env.
	// dir is ../../../../testdata/scenarios/<name> from data-service/internal/server/tests/
	// Go up 4 levels to reach project root.
	schemaPath, err := filepath.Abs(filepath.Join(dir, "..", "..", "..", "..", "specs", "config.schema.json"))
	if err == nil {
		t.Setenv("CONFIG_SCHEMA", schemaPath)
	}

	cfg, err := config.Load(filepath.Join(dir, "config.json"))
	if err != nil {
		t.Fatalf("load config: %v", err)
	}

	seedPath := filepath.Join(dir, "seed.json")
	dbPath := filepath.Join(dir, "data.db")

	var seed *seedgen.Seed
	if fileExists(seedPath) {
		seed, err = seedgen.Load(seedPath)
		if err != nil {
			t.Fatalf("load seed: %v", err)
		}
	}

	var db *sql.DB
	var dsn string
	switch {
	case seed != nil:
		// Path 1: in-memory DB + DDL from config + seed data
		db, err = sql.Open("sqlite", ":memory:?_journal_mode=WAL&_foreign_keys=on")
		if err != nil {
			t.Fatalf("open db: %v", err)
		}
		ddl, err := seedgen.GenerateDDL(cfg.Entities, "sqlite")
		if err != nil {
			t.Fatalf("generate DDL: %v", err)
		}
		// Override DSN in config to in-memory
		cfg.DataSource.DSN = ":memory:"
		if err := seedgen.ApplyWithDDL(context.Background(), sqlExecAdapter{db}, ddl, seed, seedgen.SQLitePlaceholder, "sqlite"); err != nil {
			t.Fatalf("apply seed: %v", err)
		}
	case fileExists(dbPath):
		// Path 2: pre-materialized file-based DB (e.g. shop scenario)
		// Use absolute path so data-service reads it regardless of CWD.
		absDB, _ := filepath.Abs(dbPath)
		dsn = "file:" + absDB + "?_journal_mode=WAL&_foreign_keys=on"
		db, err = sql.Open("sqlite", dsn)
		if err != nil {
			t.Fatalf("open db: %v", err)
		}
		cfg.DataSource.DSN = dsn
	default:
		t.Fatalf("scenario %q has neither seed.json nor data.db — cannot materialize", dir)
	}

	return cfg, db
}

// fileExists reports whether path is a regular file (or symlink to one).
func fileExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}

// buildTestRouter creates a httptest.Server from config + *sql.DB.
func buildTestRouter(t testing.TB, cfg *config.Config, db *sql.DB) *httptest.Server {
	t.Helper()
	adapter := &testSQLite{db: db}
	store := server.NewTenantStore(datasource.NewDefaultRegistry(), "")
	router, err := server.NewRouterFromConfig(store, cfg, adapter, adapter, nil, "", nil, nil)
	if err != nil {
		t.Fatalf("NewRouterFromConfig: %v", err)
	}
	// Register pre-built instance directly — skip AddTenant which would open a new
	// connection, losing the already-seeded in-memory DB.
	inst := &server.TenantInstance{
		ID:         "default",
		Config:     cfg,
		AdapterSub: adapter,
		Router:     router,
	}
	if err := store.RegisterTenantInstance(inst); err != nil {
		t.Fatalf("RegisterTenantInstance: %v", err)
	}
	// Wrap with middleware that injects X-Tenant-ID: default for tests that don't set it.
	// TenantIDMiddleware captures the header; if absent we inject "default".
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Tenant-ID") == "" && r.URL.Query().Get("tenant") == "" {
			r.Header.Set("X-Tenant-ID", "default")
		}
		server.TenantIDMiddleware("X-Tenant-ID")(store).ServeHTTP(w, r)
	})
	ts := httptest.NewServer(handler)
	t.Cleanup(func() { ts.Close() })
	return ts
}

// ── Reusable subtest helpers ──

func testHealth(t *testing.T, ts *httptest.Server) {
	status, body := getJSON[map[string]string](t, ts.URL+"/health")
	if status != 200 {
		t.Errorf("expected 200, got %d", status)
	}
	if body["status"] != "ok" {
		t.Errorf("expected status ok, got %q", body["status"])
	}
}

func testStudents(t *testing.T, ts *httptest.Server) {
	// Get student by id
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

	// Not found
	status, body := getJSON[map[string]string](t, ts.URL+"/students/nonexistent")
	if status != 404 {
		t.Errorf("expected 404, got %d", status)
	}
	if body["error"] != "not_found" {
		t.Errorf("expected 'not_found', got %q", body["error"])
	}

	// Find by name
	status, s = getJSON[map[string]any](t,
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

	// Find not found
	status, _ = getJSON[map[string]string](t,
		ts.URL+"/students?name=Неизвестный+Студент")
	if status != 404 {
		t.Errorf("expected 404, got %d", status)
	}

	// Student disciplines
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

func testGrades(t *testing.T, ts *httptest.Server) {
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

func testTeachers(t *testing.T, ts *httptest.Server) {
	status, teacher := getJSON[map[string]any](t,
		ts.URL+"/teachers?name="+pathEncode("Оксана Ниловна Константинова"))
	if status != 200 {
		t.Fatalf("expected 200, got %d", status)
	}
	if teacher["full_name"] != "Оксана Ниловна Константинова" {
		t.Errorf("unexpected name: %v", teacher["full_name"])
	}
}

func testDisciplines(t *testing.T, ts *httptest.Server) {
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

func testStats(t *testing.T, ts *httptest.Server) {
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

func testOpenAPI(t *testing.T, ts *httptest.Server) {
	// /openapi.json
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

	// /docs (Swagger UI)
	resp, err = http.Get(ts.URL + "/docs")
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

func testCustomQuery(t *testing.T, ts *httptest.Server) {
	// Grades for student s1
	status, body := getJSON[[]map[string]any](t, ts.URL+"/students/s1/grades")
	if status != 200 {
		t.Errorf("grades expected 200, got %d", status)
	}
	if len(body) != 2 {
		t.Errorf("expected 2 grades, got %d", len(body))
	}

	// Schedule for group g1
	status, body = getJSON[[]map[string]any](t, ts.URL+"/groups/g1/schedule")
	if status != 200 {
		t.Errorf("schedule expected 200, got %d", status)
	}
	if len(body) == 0 {
		t.Error("expected non-empty schedule list for g1")
	}

	// Disciplines for student s1
	status, body = getJSON[[]map[string]any](t, ts.URL+"/students/s1/disciplines")
	if status != 200 {
		t.Errorf("disciplines expected 200, got %d", status)
	}
	if len(body) == 0 {
		t.Error("expected non-empty disciplines list for s1")
	}
}

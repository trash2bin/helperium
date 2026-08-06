// Package server_test — config-driven тесты для data-service роутера.
package server_test

import (
	"net/http"
	"net/url"
	"testing"
)

// ══════════���═══════════════════════════════════════════════════════════
// Config-driven роутер тесты
// ══════════════════════════════════════════════════════════════════════

func TestNewRouterFromConfig_Health(t *testing.T) {
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

func TestNewRouterFromConfig_Stats(t *testing.T) {
	ts := newTestServer(t)

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

func TestNewRouterFromConfig_GetByID(t *testing.T) {
	ts := newTestServer(t)

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

func TestNewRouterFromConfig_NotFound(t *testing.T) {
	ts := newTestServer(t)

	resp, err := http.Get(ts.URL + "/nonexistent")
	if err != nil {
		t.Fatalf("GET /nonexistent: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 404 {
		t.Errorf("expected 404, got %d", resp.StatusCode)
	}
}

func TestNewRouterFromConfig_FindStudent(t *testing.T) {
	ts := newTestServer(t)

	status, body := getJSON[[]map[string]any](t, ts.URL+"/students/search?search="+url.QueryEscape("Иван Петров Иванович"))

	if status != 200 {
		t.Errorf("expected 200, got %d; body=%v", status, body)
	}
	if len(body) == 0 {
		t.Errorf("expected at least 1 result")
	}
	if body[0]["full_name"] == "" {
		t.Errorf("expected non-empty full_name, got %v", body[0]["full_name"])
	}
}

func TestNewRouterFromConfig_ListDisciplines(t *testing.T) {
	ts := newTestServer(t)

	status, body := getJSON[[]map[string]any](t, ts.URL+"/disciplines")

	if status != 200 {
		t.Errorf("expected 200, got %d", status)
	}
	if len(body) == 0 {
		t.Error("expected non-empty disciplines list")
	}
}

func TestNewRouterFromConfig_CustomQuery(t *testing.T) {
	ts := newTestServer(t)

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
	status, _ = getJSON[[]any](t, ts.URL+"/students/s1/disciplines")
	if status != 200 {
		t.Errorf("disciplines expected 200, got %d", status)
	}
}

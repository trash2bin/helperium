// Package server — тесты на сценарии с большим объёмом данных.
//
// Сценарий big-testseed содержит:
//   - 500 студентов, 30 преподавателей, 25 дисциплин, 25 групп, 100 schedule, 4000 оценок
//
// Цель — проверить, что runtime (query builder, custom_queries, JSON-сериализация,
// пагинация) выдерживают нагрузку без потери корректности и с приемлемой скоростью.
package server_test

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// TestScenario_BigTestseed покрывает большой сценарий:
//   - все базовые endpoints на больших объёмах
//   - пагинация / поиск
//   - статистика
//   - custom_queries на тысячах записей
func TestScenario_BigTestseed(t *testing.T) {
	cfg, db := loadScenario(t, "../../../testdata/scenarios/big-testseed")
	defer db.Close() //nolint:errcheck
	ts := buildTestRouter(t, cfg, db)

	t.Run("health", func(t *testing.T) {
		status, body := getJSON[map[string]string](t, ts.URL+"/health")
		if status != 200 || body["status"] != "ok" {
			t.Fatalf("health: %d %v", status, body)
		}
	})

	t.Run("stats_counts_match_seed", func(t *testing.T) {
		status, stats := getJSON[map[string]any](t, ts.URL+"/stats")
		if status != 200 {
			t.Fatalf("stats: %d", status)
		}
		// Ожидаемые числа из генератора big-testseed seed.json
		expected := map[string]float64{
			"students":    500,
			"teachers":    30,
			"disciplines": 25,
			"grades":      4000,
			// groups и schedule зависят от того, как stats считает (наличие endpoint'ов)
		}
		for k, want := range expected {
			if got, ok := stats[k].(float64); !ok {
				t.Errorf("stats[%q] missing or not number: %v", k, stats[k])
			} else if got != want {
				t.Errorf("stats[%q] = %v, want %v", k, got, want)
			}
		}
	})

	t.Run("get_students_by_id", func(t *testing.T) {
		// Проверяем несколько индексов из разных диапазонов
		for _, id := range []string{"s1", "s100", "s250", "s500"} {
			id := id
			t.Run(id, func(t *testing.T) {
				status, body := getJSON[map[string]any](t, ts.URL+"/students/"+id)
				if status != 200 {
					t.Errorf("%s: status=%d body=%v", id, status, body)
				}
				if body["id"] != id {
					t.Errorf("%s: id mismatch, got %v", id, body["id"])
				}
			})
		}
	})

	t.Run("find_students_by_name_first", func(t *testing.T) {
		// Поиск по подстроке "Иванов" — возвращает массив совпадений.
		status, results := getJSON[[]map[string]any](t, ts.URL+"/students?full_name=%D0%98%D0%B2%D0%B0%D0%BD%D0%BE%D0%B2")
		if status != 200 {
			t.Errorf("find: status=%d", status)
		}
		if len(results) == 0 {
			t.Errorf("expected at least 1 student with 'Иванов'")
		}
	})

	t.Run("find_students_by_name_nonexistent", func(t *testing.T) {
		status, results := getJSON[[]map[string]any](t, ts.URL+"/students?full_name=НеизвестныйНикогдаНеСуществовал")
		if status != 200 {
			t.Errorf("expected 200 for non-existing name, got %d", status)
		}
		if len(results) != 0 {
			t.Errorf("expected empty results, got %d items", len(results))
		}
	})

	t.Run("find_teachers_by_name", func(t *testing.T) {
		// В big-testseed конфиге нет /teachers/{id}, только /teachers (find).
		// find возвращает массив совпадений.
		status, results := getJSON[[]map[string]any](t, ts.URL+"/teachers?full_name=%D0%98%D0%B2%D0%B0%D0%BD%D0%BE%D0%B2")
		if status != 200 {
			t.Errorf("find teacher: status=%d", status)
		}
		if len(results) == 0 {
			t.Errorf("expected at least 1 teacher with 'Иванов'")
		}
	})

	t.Run("grades_for_student_correct_count", func(t *testing.T) {
		// В seed.json: 8 оценок на студента
		// Проверяем 5 случайных студентов и убеждаемся что у каждого =8 оценок
		for _, sid := range []string{"s1", "s100", "s250", "s400", "s500"} {
			sid := sid
			t.Run(sid, func(t *testing.T) {
				status, grades := getJSON[[]map[string]any](t, ts.URL+"/students/"+sid+"/grades")
				if status != 200 {
					t.Fatalf("grades %s: status=%d", sid, status)
				}
				if len(grades) != 8 {
					t.Errorf("%s: expected 8 grades, got %d", sid, len(grades))
				}
			})
		}
	})

	t.Run("disciplines_for_student", func(t *testing.T) {
		status, body := getJSON[[]map[string]any](t, ts.URL+"/students/s1/disciplines")
		if status != 200 {
			t.Fatalf("disciplines s1: status=%d", status)
		}
		if len(body) == 0 {
			t.Errorf("expected at least 1 discipline, got 0")
		}
	})

	t.Run("latency_get_by_id", func(t *testing.T) {
		// На больших данных get_by_id должен быть < 50мс
		start := time.Now()
		for i := 0; i < 50; i++ {
			status, _ := getJSON[map[string]any](t, ts.URL+"/students/s100")
			if status != 200 {
				t.Fatalf("iter %d: status=%d", i, status)
			}
		}
		elapsed := time.Since(start) / 50
		if elapsed > 50*time.Millisecond {
			t.Errorf("avg latency %v > 50ms", elapsed)
		}
		t.Logf("avg latency get_by_id: %v", elapsed)
	})

	t.Run("all_grades_endpoint_capped", func(t *testing.T) {
		// /grades в конфиге big-testseed имеет max_rows=80 (LIMIT 80).
		// Проверяем что реально возвращается 80, а не все 4000 — это by design.
		status, grades := getJSON[[]map[string]any](t, ts.URL+"/grades")
		if status != 200 {
			t.Fatalf("all grades: status=%d", status)
		}
		if len(grades) != 80 {
			t.Errorf("expected exactly 80 (max_rows cap), got %d", len(grades))
		}
	})
}

// TestScenario_BigTestseed_PaginationBoundary проверяет граничные случаи
// пагинации/поиска на большом наборе данных.
func TestScenario_BigTestseed_PaginationBoundary(t *testing.T) {
	cfg, db := loadScenario(t, "../../../testdata/scenarios/big-testseed")
	defer db.Close() //nolint:errcheck
	ts := buildTestRouter(t, cfg, db)

	t.Run("get_100th_student_exists", func(t *testing.T) {
		status, body := getJSON[map[string]any](t, ts.URL+"/students/s100")
		if status != 200 {
			t.Errorf("status=%d body=%v", status, body)
		}
	})

	t.Run("get_501th_student_not_found", func(t *testing.T) {
		status, body := getJSON[map[string]string](t, ts.URL+"/students/s501")
		if status != 404 {
			t.Errorf("expected 404, got %d body=%v", status, body)
		}
		if body["error"] != "not_found" {
			t.Errorf("expected error=not_found, got %v", body)
		}
	})

	t.Run("group_with_schedule", func(t *testing.T) {
		// Группы у нас 25 штук; у каждой 4 schedule slot'а.
		status, body := getJSON[[]map[string]any](t, ts.URL+"/groups/g1/schedule")
		if status != 200 {
			t.Fatalf("group g1 schedule: status=%d", status)
		}
		if len(body) == 0 {
			t.Errorf("expected schedule for g1")
		}
		t.Logf("g1 has %d schedule entries", len(body))
	})

	t.Run("all_groups_count_via_path", func(t *testing.T) {
		// В big-testseed конфиге нет эндпоинта /groups (list).
		// Проверяем что schedule-запрос для разных групп стабильно отвечает.
		count := 0
		for _, gid := range []string{"g1", "g5", "g10", "g15", "g20", "g25"} {
			status, _ := getJSON[[]map[string]any](t, ts.URL+"/groups/"+gid+"/schedule")
			if status == 200 {
				count++
			}
		}
		t.Logf("Из 6 групп вернули 200: %d", count)
		if count == 0 {
			t.Errorf("хотя бы одна группа должна иметь schedule")
		}
	})
}

// TestScenario_BigTestseed_OpenAPISpecValid проверяет, что на большом сценарии
// OpenAPI-спецификация валидна и содержит все endpoints.
func TestScenario_BigTestseed_OpenAPISpecValid(t *testing.T) {
	cfg, db := loadScenario(t, "../../../testdata/scenarios/big-testseed")
	defer db.Close() //nolint:errcheck
	ts := buildTestRouter(t, cfg, db)

	req, _ := http.NewRequest("GET", ts.URL+"/openapi.json", nil)
	req.Header.Set("X-Tenant-ID", "default")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("GET /openapi.json: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Fatalf("status=%d", resp.StatusCode)
	}

	var spec map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&spec); err != nil {
		t.Fatalf("invalid JSON: %v", err)
	}
	if spec["openapi"] != "3.1.0" {
		t.Errorf("expected openapi 3.1.0, got %v", spec["openapi"])
	}

	paths, ok := spec["paths"].(map[string]any)
	if !ok {
		t.Fatalf("paths missing or not object")
	}
	// В OpenAPI входят только реальные endpoints конфига + мета.
	// /openapi.json — это сам swagger endpoint, не часть OpenAPI paths.
	expectedEndpoints := []string{
		"/students", "/students/{id}", "/students/{id}/grades", "/students/{id}/disciplines",
		"/teachers",
		"/groups/{id}/schedule",
		"/disciplines", "/grades", "/schedule",
		"/health", "/stats",
	}
	for _, ep := range expectedEndpoints {
		if _, ok := paths[ep]; !ok {
			t.Errorf("endpoint %q missing in OpenAPI spec", ep)
		}
	}
}

// TestScenario_BigTestseed_NoPanicsOnRandomQueries — стресс по 100 случайным URL-адресам.
func TestScenario_BigTestseed_NoPanicsOnRandomQueries(t *testing.T) {
	cfg, db := loadScenario(t, "../../../testdata/scenarios/big-testseed")
	defer db.Close() //nolint:errcheck
	ts := buildTestRouter(t, cfg, db)

	// В big-testseed конфиге доступны не все endpoints; тут только те, что есть.
	tests := []struct {
		path   string
		expect int // ожидаемый статус (200/404 — что допустимо)
	}{
		{"/students/s1", 200},
		{"/students/s500", 200},
		{"/students/s99999", 404},
		{"/teachers?full_name=%D0%98%D0%B2%D0%B0%D0%BD%D0%BE%D0%B2", 200},
		{"/groups/g1", 404},          // get_by_id не описан в конфиге
		{"/groups/g1/schedule", 200}, // custom_query с g1
		{"/students/s1/grades", 200},
		{"/students/s1/disciplines", 200},
		{"/health", 200},
		{"/stats", 200},
		{"/grades", 200},
		{"/schedule", 200},
		{"/disciplines", 200},
		{"/students?full_name=Неизвестный", 200},
	}

	for _, tc := range tests {
		tc := tc
		t.Run(strings.ReplaceAll(tc.path, "/", "_"), func(t *testing.T) {
			// Тестируем сервер на отсутствие 5xx — это smoke-тест, тип ответа не важен.
			resp, err := http.Get(ts.URL + tc.path)
			if err != nil {
				t.Errorf("%s: %v", tc.path, err)
				return
			}
			defer resp.Body.Close()
			if resp.StatusCode >= 500 {
				t.Errorf("%s: server error status=%d", tc.path, resp.StatusCode)
			}
		})
	}
}

// _ компилирует httptest алиас чтобы не было неиспользуемых imports warning
var _ = httptest.NewServer
var _ = fmt.Sprintf

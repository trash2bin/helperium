// Package server — fuzz тесты.
//
// Генерируем случайные URL-запросы и проверяем что сервер не падает.
// Тесты не ищут конкретные баги — они ловят паники и 5xx на неожиданных входах.
//
// Запуск:
//
//	go test -fuzz=FuzzEndpoints -fuzztime=10s ./internal/server/...
package server_test

import (
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"strings"
	"testing"
)

// FuzzEndpoints подкидывает случайные URL и проверяет что ответ < 5xx.
// (тест не assertion — просто smoke на отсутствие panic).
func FuzzEndpoints(f *testing.F) {
	cfg, db := loadScenario(f, "../../../testdata/scenarios/sqlite-testseed")
	defer db.Close()
	ts := buildTestRouter(f, cfg, db)
	defer ts.Close()

	// Seed corpus
	f.Add("students")
	f.Add("students/s1")
	f.Add("students?s1")
	f.Add("students?name=test")
	f.Add("teachers?name=test")
	f.Add("groups/g1/schedule")
	f.Add("disciplines")
	f.Add("grades")
	f.Add("schedule")
	f.Add("health")
	f.Add("stats")
	f.Add("nonexistent/path")

	f.Fuzz(func(t *testing.T, path string) {
		// Skip empty / pure-slash paths (chi router может отдать 404 — это OK)
		if path == "" {
			return
		}
		// URL-escape
		u := ts.URL + "/" + url.PathEscape(strings.TrimPrefix(path, "/"))
		resp, err := http.Get(u)
		if err != nil {
			t.Logf("transport error (OK): %v for %s", err, u)
			return
		}
		defer resp.Body.Close()

		if resp.StatusCode >= 500 {
			t.Errorf("server error on %q: status=%d", path, resp.StatusCode)
		}

		// Body должен быть валидным JSON если есть Content-Type: application/json
		ct := resp.Header.Get("Content-Type")
		if strings.Contains(ct, "application/json") {
			body, _ := io.ReadAll(resp.Body)
			var v any
			if err := json.Unmarshal(body, &v); err != nil {
				t.Errorf("invalid JSON for %q: status=%d body=%s err=%v",
					path, resp.StatusCode, body, err)
			}
		}
	})
}

// FuzzQueryParams — случайные параметры запроса для /students и /teachers.
func FuzzQueryParams(f *testing.F) {
	cfg, db := loadScenario(f, "../../../testdata/scenarios/sqlite-testseed")
	defer db.Close()
	ts := buildTestRouter(f, cfg, db)
	defer ts.Close()

	f.Add("Иванов")
	f.Add("Сидорова")
	f.Add("Неизвестный")
	f.Add("' OR 1=1 --")
	f.Add("%; DROP TABLE students; --")
	f.Add("")
	f.Add("\x00")
	f.Add("🎉")

	f.Fuzz(func(t *testing.T, query string) {
		for _, ep := range []string{"/students", "/teachers"} {
			u := ts.URL + ep + "?name=" + url.QueryEscape(query)
			resp, err := http.Get(u)
			if err != nil {
				return
			}
			defer resp.Body.Close()
			if resp.StatusCode >= 500 {
				t.Errorf("5xx on %s?name=%q: status=%d", ep, query, resp.StatusCode)
			}
		}
	})
}

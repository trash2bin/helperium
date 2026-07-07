// Package server — бенчмарки типовых операций data-service.
//
// Бенчмарки не assert-ятся (go test -bench), они только замеряют
// производительность. Запускаются через:
//
//	go test -bench=. -benchmem ./internal/server/...
//
// Данные: big-testseed (500 студентов, 4000 оценок) — даёт реалистичную
// нагрузку для замеров.
package server_test

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
)

func benchmarkSetup(b *testing.B) *httptest.Server {
	b.Helper()
	cfg, db := loadScenario(&testing.T{}, "../../../testdata/scenarios/big-testseed")
	b.Cleanup(func() { db.Close() })
	ts := buildTestRouter(&testing.T{}, cfg, db)
	b.Cleanup(func() { ts.Close() })
	return ts
}

// BenchmarkGetByID_Genuine уникальных ID — 500 студентов
func BenchmarkGetByID(b *testing.B) {
	ts := benchmarkSetup(b)
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		id := fmt.Sprintf("s%d", (i%500)+1)
		resp, err := http.Get(ts.URL + "/students/" + id)
		if err != nil {
			b.Fatal(err)
		}
		resp.Body.Close()
		if resp.StatusCode != 200 {
			b.Errorf("status=%d", resp.StatusCode)
		}
	}
}

// BenchmarkGetNonExistent — частый случай (404)
func BenchmarkGetNonExistent(b *testing.B) {
	ts := benchmarkSetup(b)
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		id := fmt.Sprintf("s%d", 1000+i)
		resp, err := http.Get(ts.URL + "/students/" + id)
		if err != nil {
			b.Fatal(err)
		}
		resp.Body.Close()
	}
}

// BenchmarkFindByName — поиск по имени (LIKE-операция)
func BenchmarkFindByName(b *testing.B) {
	ts := benchmarkSetup(b)
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		resp, err := http.Get(ts.URL + "/students?name=%D0%98%D0%B2%D0%B0%D0%BD%D0%BE%D0%B2")
		if err != nil {
			b.Fatal(err)
		}
		resp.Body.Close()
	}
}

// BenchmarkCustomQuery_GradesForStudent — custom query с JOIN
func BenchmarkCustomQuery_GradesForStudent(b *testing.B) {
	ts := benchmarkSetup(b)
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		id := fmt.Sprintf("s%d", (i%500)+1)
		resp, err := http.Get(ts.URL + "/students/" + id + "/grades")
		if err != nil {
			b.Fatal(err)
		}
		resp.Body.Close()
	}
}

// BenchmarkCustomQuery_AllGrades — тяжёлый запрос (LIMIT 80)
func BenchmarkCustomQuery_AllGrades(b *testing.B) {
	ts := benchmarkSetup(b)
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		resp, err := http.Get(ts.URL + "/grades")
		if err != nil {
			b.Fatal(err)
		}
		resp.Body.Close()
	}
}

// BenchmarkCustomQuery_ScheduleForGroup — JSON-парсинг lessons
func BenchmarkCustomQuery_ScheduleForGroup(b *testing.B) {
	ts := benchmarkSetup(b)
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		id := fmt.Sprintf("g%d", (i%25)+1)
		resp, err := http.Get(ts.URL + "/groups/" + id + "/schedule")
		if err != nil {
			b.Fatal(err)
		}
		resp.Body.Close()
	}
}

// BenchmarkHealth — самый дешёвый endpoint
func BenchmarkHealth(b *testing.B) {
	ts := benchmarkSetup(b)
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		resp, err := http.Get(ts.URL + "/health")
		if err != nil {
			b.Fatal(err)
		}
		resp.Body.Close()
	}
}

// BenchmarkOpenAPI — генерация OpenAPI спек (дорогая операция)
func BenchmarkOpenAPI(b *testing.B) {
	ts := benchmarkSetup(b)
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		resp, err := http.Get(ts.URL + "/openapi.json")
		if err != nil {
			b.Fatal(err)
		}
		resp.Body.Close()
	}
}

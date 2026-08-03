// router_contract_test.go — Gap A: spec.go (OpenAPI) ↔ реальный chi-роутер.
//
// Проверяет, что каждый маршрут в Router() отражён в openapi.GenerateSpec(),
// и наоборот. Историческая проблема: spec.go — ручной хардкод (addGet/addPost),
// и маршрут мог появиться/исчезнуть в server.go без правки спеки (или наоборот),
// что ломало фронт (контрактный тест contract.test.js требует путь в спеке).
//
// Статические/system-пути исключаются из сравнения:
//   /health, /i18n.json, /openapi.json, /metrics, /* (static catch-all)
package server

import (
	"net/http"
	"sort"
	"strings"
	"testing"

	"github.com/go-chi/chi/v5"
	"github.com/trash2bin/helperium/admin-dashboard/internal/openapi"
)

// excludedStaticPaths — пути, которые сознательно не попадают в OpenAPI спеки
// (статический фронт, метрики, system-эндпоинты без API-контракта).
// /metrics и /* регистрируются через r.Handle — chi.Walk отдаёт для них все методы,
// поэтому исключаем любой метод на этих путях.
func excludedStaticPaths() map[string]bool {
	return map[string]bool{
		"GET /health":       true,
		"GET /i18n.json":    true,
		"GET /openapi.json": true,
		"GET /metrics":      true,
		"POST /metrics":     true,
		"PUT /metrics":      true,
		"DELETE /metrics":   true,
		"PATCH /metrics":    true,
		"HEAD /metrics":     true,
		"OPTIONS /metrics":  true,
		"GET /*":            true,
		"POST /*":           true,
		"PUT /*":            true,
		"DELETE /*":         true,
		"PATCH /*":          true,
		"HEAD /*":           true,
		"OPTIONS /*":        true,
	}
}

// routerRoutes собирает все маршруты из Router() через chi.Walk.
func routerRoutes(t *testing.T) map[string]bool {
	t.Helper()
	s := New(Options{Addr: ":0"})
	router := s.Router()

	routes := map[string]bool{}
	if err := chi.Walk(router, func(method, route string, _ http.Handler, _ ...func(http.Handler) http.Handler) error {
		key := strings.ToUpper(method) + " " + normalizeChiRoute(route)
		routes[key] = true
		return nil
	}); err != nil {
		t.Fatalf("chi.Walk failed: %v", err)
	}
	return routes
}

// normalizeChiRoute приводит chi-пути ({id}) к виду OpenAPI ({id}).
func normalizeChiRoute(route string) string {
	return route
}

// specRoutes собирает "METHOD /path" из GenerateSpec().
func specRoutes(t *testing.T) map[string]bool {
	t.Helper()
	spec := openapi.GenerateSpec()
	routes := map[string]bool{}

	paths, _ := spec["paths"].(map[string]any)
	for path, methods := range paths {
		methodsMap, _ := methods.(map[string]any)
		if methodsMap == nil {
			continue
		}
		for method := range methodsMap {
			switch method {
			case "get", "post", "put", "delete", "patch":
				routes[strings.ToUpper(method)+" "+path] = true
			}
		}
	}
	return routes
}

// diffMaps возвращает элементы в a, которых нет в b.
func diffMaps(a, b map[string]bool) []string {
	var out []string
	for k := range a {
		if !b[k] {
			out = append(out, k)
		}
	}
	sort.Strings(out)
	return out
}

func TestRouterMatchesOpenAPISpec(t *testing.T) {
	router := routerRoutes(t)
	spec := specRoutes(t)
	excluded := excludedStaticPaths()

	// Фильтруем router: только стандартные методы + не исключённые
	routerFiltered := map[string]bool{}
	for k := range router {
		if excluded[k] {
			continue
		}
		method := strings.SplitN(k, " ", 2)[0]
		switch method {
		case "GET", "POST", "PUT", "DELETE", "PATCH":
			routerFiltered[k] = true
		}
	}

	// 1. Каждый маршрут роутера (кроме исключений) должен быть в спеке
	missingInSpec := diffMaps(routerFiltered, spec)
	if len(missingInSpec) > 0 {
		t.Errorf("ROUTES IN ROUTER BUT NOT IN OpenAPI SPEC (add to spec.go):\n  %s",
			strings.Join(missingInSpec, "\n  "))
	}

	// 2. Каждый путь в спеке должен быть в роутере
	//    (spec содержит только /api/* и system — все должны быть в роутере)
	missingInRouter := diffMaps(spec, router)
	if len(missingInRouter) > 0 {
		t.Errorf("PATHS IN OpenAPI SPEC BUT NOT IN ROUTER (remove from spec.go or add route):\n  %s",
			strings.Join(missingInRouter, "\n  "))
	}
}

// TestRouterStaticExclusionsAreReal — проверяет, что исключаемые пути реально
// существуют в роутере (защита от "я добавил статику, забыл исключить").
func TestRouterStaticExclusionsAreReal(t *testing.T) {
	router := routerRoutes(t)
	for k := range excludedStaticPaths() {
		if !router[k] && k != "HEAD /*" && k != "OPTIONS /*" && k != "PATCH /*" && k != "PUT /*" && k != "DELETE /*" && k != "POST /*" {
			t.Errorf("excluded path %q not actually in router — remove from excludedStaticPaths()", k)
		}
	}
}

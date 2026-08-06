// proxy_contract_test.go — Gap B: прокси-эндпоинты admin-dashboard ↔ upstream-сервисы.
//
// Каждый эндпоинт админки, помеченный withProxyTo(...) + withProxyTarget(...),
// объявляет реальный upstream-метод и путь (x-upstream-method / x-upstream-path),
// который хендлер шлёт на целевой сервис. Этот тест проверяет, что такой путь
// реально существует в OpenAPI-спеке целевого сервиса:
//
//   - data-service  → openapigen.GenerateSystemSpec(..., hasAdmin=true) (импорт, без HTTP)
//   - api-service   → specs/api.openapi.yaml (реальная спека FastAPI)
//   - rag-service   → specs/rag.openapi.yaml (реальная спека FastAPI)
//
// Падает с понятным диффом, если кто-то переименовал/удалил ручку на стороне
// upstream, а прокси об этом не узнал (исторический источник багов: например,
// POST /admin/tenants/{id}/delete не существует в data-service — там DELETE
// /admin/tenants/{id}).
package openapi

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/trash2bin/helperium/helperium-go/openapigen"
)

// upstreamSpecs собирает пути из актуальной спеки каждого upstream-сервиса
// как map["METHOD /path"] → true.
func upstreamSpecs(t *testing.T) map[string]map[string]bool {
	t.Helper()

	// data-service: импортируем openapigen напрямую (монорепо через go.work).
	dsSpec := openapigen.GenerateSystemSpec("http://localhost:8084", "Data Service", "1.0.0", true)
	dsPaths := map[string]bool{}
	collectPaths(dsSpec, dsPaths)

	// api-service и rag-service: парсим YAML-спеки (мини-парсер, только paths).
	repoRoot := filepath.Clean(filepath.Join("..", "..", "..", ".."))
	apiPaths := map[string]bool{}
	parseYamlPaths(t, filepath.Join(repoRoot, "specs", "api.openapi.yaml"), apiPaths)
	ragPaths := map[string]bool{}
	parseYamlPaths(t, filepath.Join(repoRoot, "specs", "rag.openapi.yaml"), ragPaths)

	return map[string]map[string]bool{
		"data-service": dsPaths,
		"api-service":  apiPaths,
		"rag-service":  ragPaths,
	}
}

// collectPaths извлекает "METHOD /path" из OpenAPI-спеки (map[string]any).
func collectPaths(spec map[string]any, out map[string]bool) {
	paths, _ := spec["paths"].(map[string]any)
	if paths == nil {
		return
	}
	for path, methods := range paths {
		methodsMap, _ := methods.(map[string]any)
		if methodsMap == nil {
			continue
		}
		for method := range methodsMap {
			if !isHTTPMethod(method) {
				continue
			}
			out[strings.ToUpper(method)+" "+normalizePath(path)] = true
		}
	}
}

func isHTTPMethod(m string) bool {
	switch m {
	case "get", "post", "put", "delete", "patch", "head", "options":
		return true
	}
	return false
}

// normalizePath приводит {param} → {id} для сопоставления.
func normalizePath(p string) string {
	p = strings.ReplaceAll(p, "{", "{")
	// Заменяем любой {имя} на {id}
	var b strings.Builder
	i := 0
	for i < len(p) {
		if p[i] == '{' {
			j := i + 1
			for j < len(p) && p[j] != '}' {
				j++
			}
			b.WriteString("{id}")
			i = j + 1
		} else {
			b.WriteByte(p[i])
			i++
		}
	}
	return strings.TrimSuffix(b.String(), "/")
}

// proxyTargets извлекает все операции с x-proxy-to + x-upstream-*.
type proxyTarget struct {
	adminMethod string
	adminPath   string
	service     string
	upMethod    string
	upPath      string
	headers     []string
	operationID string
}

func proxyTargets(t *testing.T, spec map[string]any) []proxyTarget {
	t.Helper()
	var out []proxyTarget

	paths, _ := spec["paths"].(map[string]any)
	for path, methods := range paths {
		methodsMap, _ := methods.(map[string]any)
		if methodsMap == nil {
			continue
		}
		for method, rawOp := range methodsMap {
			if !isHTTPMethod(method) {
				continue
			}
			op, _ := rawOp.(map[string]any)
			if op == nil {
				continue
			}
			service, _ := op["x-proxy-to"].(string)
			upMethod, _ := op["x-upstream-method"].(string)
			upPath, _ := op["x-upstream-path"].(string)
			if service == "" || upMethod == "" || upPath == "" {
				continue
			}
			var headers []string
			if h, ok := op["x-upstream-headers"].([]string); ok {
				headers = h
			}
			oid, _ := op["operationId"].(string)
			out = append(out, proxyTarget{
				adminMethod: strings.ToUpper(method),
				adminPath:   path,
				service:     service,
				upMethod:    strings.ToUpper(upMethod),
				upPath:      normalizePath(upPath),
				headers:     headers,
				operationID: oid,
			})
		}
	}
	return out
}

func TestProxyTargetsExistInUpstreamSpecs(t *testing.T) {
	spec := GenerateSpec()
	targets := proxyTargets(t, spec)
	if len(targets) == 0 {
		t.Fatal("no proxy targets found in spec — did withProxyTarget get wired?")
	}

	upstream := upstreamSpecs(t)

	for _, tg := range targets {
		// Для data-service upstream-путь с query (?tenant={id}) — убираем query при проверке
		upPath := strings.Split(tg.upPath, "?")[0]

		svcPaths, ok := upstream[tg.service]
		if !ok {
			t.Errorf("x-proxy-to=%q for %s %s: unknown service", tg.service, tg.adminMethod, tg.adminPath)
			continue
		}

		key := tg.upMethod + " " + upPath
		if !svcPaths[key] {
			t.Errorf(
				"PROXY CONTRACT VIOLATION\n"+
					"  admin: %s %s (operationId=%s)\n"+
					"  proxy → %s %s\n"+
					"  %s: NOT in upstream OpenAPI spec.\n"+
					"  Fix: sync withProxyTarget(...) in spec.go OR fix the handler in server.go",
				tg.adminMethod, tg.adminPath, tg.operationID,
				tg.upMethod, tg.upPath,
				tg.service,
			)
		}
	}
}

// TestProxyTargetsDeclaredHeaders — каждый x-upstream-headers должен быть
// непустым и осмысленным (это документирует контракт для код-ревью).
func TestProxyTargetsDeclaredHeaders(t *testing.T) {
	spec := GenerateSpec()
	targets := proxyTargets(t, spec)

	for _, tg := range targets {
		if len(tg.headers) == 0 {
			// Не все прокси шлют кастомные заголовки (RAG — без Bearer). Разрешаем пустые,
			// но для data-service/api-service ожидаем Authorization.
			if tg.service == "data-service" || tg.service == "api-service" {
				t.Errorf("%s %s (%s): expected Authorization in x-upstream-headers", tg.adminMethod, tg.adminPath, tg.operationID)
			}
		}
	}
}

// ── Mini YAML paths parser (только для OpenAPI paths, без external deps) ──

func parseYamlPaths(t *testing.T, file string, out map[string]bool) {
	t.Helper()
	data, err := os.ReadFile(file)
	if err != nil {
		t.Fatalf("read spec %s: %v", file, err)
	}
	lines := strings.Split(string(data), "\n")

	// Ищем секцию paths: (top-level), затем пути с отступом 2 пробела
	inPaths := false
	for i := 0; i < len(lines); i++ {
		line := lines[i]
		trimmed := strings.TrimSpace(line)
		if trimmed == "" || strings.HasPrefix(trimmed, "#") {
			continue
		}
		indent := len(line) - len(strings.TrimLeft(line, " "))
		if !inPaths {
			if trimmed == "paths:" {
				inPaths = true
			}
			continue
		}
		// Внутри paths
		if indent == 0 {
			break // вышли из paths
		}
		if indent == 2 && strings.HasPrefix(trimmed, "/") {
			// Это путь — формат "/path:"
			path := strings.TrimSuffix(trimmed, ":")
			// Читаем методы под ним (отступ 4)
			j := i + 1
			for j < len(lines) && (strings.TrimSpace(lines[j]) == "" || strings.HasPrefix(strings.TrimSpace(lines[j]), "#")) {
				j++
			}
			for j < len(lines) {
				l := lines[j]
				lt := strings.TrimSpace(l)
				if lt == "" || strings.HasPrefix(lt, "#") {
					j++
					continue
				}
				li := len(l) - len(strings.TrimLeft(l, " "))
				if li <= indent {
					break
				}
				if li == 4 && strings.HasSuffix(lt, ":") && isHTTPMethod(strings.TrimSuffix(lt, ":")) {
					out[strings.ToUpper(strings.TrimSuffix(lt, ":"))+" "+normalizePath(path)] = true
				}
				j++
			}
		}
	}
}

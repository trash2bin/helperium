// Package openapigen генерирует OpenAPI 3.1.0 спецификацию из конфига data-service.
//
// Вместо статического openapi.json — runtime-генерация из cfg.Endpoints на КАЖДЫЙ запрос.
// Что в конфиге, то и в /openapi.json. Поменял конфиг — спека сама подстроилась.
//
// OpenAPI генерируется runtime из cfg.Endpoints — статическая спецификация
// specs/data-service.openapi.yaml удалена в рамках чистки legacy-артефактов.
package openapigen

import (
	"strings"

	"github.com/agent-tutor/agent-tutor-go/config"
)

// Generate создаёт OpenAPI 3.1.0 спецификацию из конфига data-service.
//
// hasAdmin=true — добавляет /admin/discover в спеки.
func Generate(cfg *config.Config, host, title, version string, hasAdmin bool) map[string]any {
	return map[string]any{
		"openapi": "3.1.0",
		"info": map[string]any{
			"title":       title,
			"description": "Runtime-generated OpenAPI спецификация data-service. Полностью определяется конфигом — никакого хардкода.",
			"version":     version,
		},
		"servers": []map[string]any{
			{"url": host, "description": "data-service"},
		},
		"paths":      buildPaths(cfg, hasAdmin),
		"components": buildComponents(cfg),
		"security": []map[string]any{
			{"BearerAuth": []string{}},
		},
	}
}

// GenerateSystemSpec создаёт OpenAPI 3.1.0 спецификацию только с системными и админ-эндпоинтами.
// Используется когда тенант не указан — показывает только то, что доступно без выбора БД.
func GenerateSystemSpec(host, title, version string, hasAdmin bool) map[string]any {
	return map[string]any{
		"openapi": "3.1.0",
		"info": map[string]any{
			"title":       title,
			"description": "System OpenAPI спецификация — выберите тенант (?tenant=...) для получения полной схемы.",
			"version":     version,
		},
		"servers": []map[string]any{
			{"url": host, "description": "data-service"},
		},
		"paths":      buildSystemPaths(hasAdmin),
		"components": buildSystemComponents(),
		"security": []map[string]any{
			{"BearerAuth": []string{}},
		},
	}
}

func buildSystemPaths(hasAdmin bool) map[string]any {
	paths := make(map[string]any)

	systemTag := []string{"System"}
	adminTag := []string{"Admin"}
	adminSec := []map[string]any{{"BearerAuth": []string{}}}

	// System endpoints
	paths["/health"] = map[string]any{
		"get": map[string]any{
			"summary":     "Health check",
			"operationId": "health_check",
			"tags":        systemTag,
			"responses": map[string]any{
				"200": map[string]any{
					"description": "Успешный ответ",
					"content": map[string]any{
						"application/json": map[string]any{
							"schema": map[string]any{"$ref": "#/components/schemas/HealthResponse"},
						},
					},
				},
			},
		},
	}

	paths["/stats"] = map[string]any{
		"get": map[string]any{
			"summary":     "Service stats",
			"operationId": "service_stats",
			"tags":        systemTag,
			"responses": map[string]any{
				"200": map[string]any{
					"description": "Статистика запросов",
					"content": map[string]any{
						"application/json": map[string]any{
							"schema": map[string]any{"type": "object", "additionalProperties": map[string]any{"type": "integer"}},
						},
					},
				},
			},
		},
	}

	// Admin endpoints
	if hasAdmin {
		paths["/admin/tenants"] = map[string]any{
			"get": map[string]any{
				"summary":     "Список всех тенантов",
				"operationId": "admin_list_tenants",
				"tags":        adminTag,
				"security":    adminSec,
				"responses": map[string]any{
					"200": map[string]any{
						"description": "Список тенантов",
						"content": map[string]any{
							"application/json": map[string]any{
								"schema": map[string]any{"type": "array", "items": map[string]any{"$ref": "#/components/schemas/TenantResponse"}},
							},
						},
					},
				},
			},
			"post": map[string]any{
				"summary":     "Добавить новый тенант",
				"operationId": "admin_add_tenant",
				"tags":        adminTag,
				"security":    adminSec,
				"requestBody": map[string]any{
					"required": true,
					"content": map[string]any{
						"application/json": map[string]any{
							"schema": map[string]any{"$ref": "#/components/schemas/TenantRequest"},
						},
					},
				},
				"responses": map[string]any{
					"201": map[string]any{
						"description": "Тенант создан",
						"content": map[string]any{
							"application/json": map[string]any{
								"schema": map[string]any{"type": "object"},
							},
						},
					},
				},
				"409": errorResponse("Тенант уже существует"),
				"500": errorResponse("Ошибка при создании тенанта"),
			},
		}

		paths["/admin/tenants/{id}"] = map[string]any{
			"get": map[string]any{
				"summary":     "Информация о конкретном тенанте",
				"operationId": "admin_get_tenant",
				"tags":        adminTag,
				"security":    adminSec,
				"parameters": []map[string]any{
					{"name": "id", "in": "path", "required": true, "schema": map[string]any{"type": "string"}},
				},
				"responses": map[string]any{
					"200": map[string]any{
						"description": "Данные тенанта",
						"content": map[string]any{
							"application/json": map[string]any{
								"schema": map[string]any{"$ref": "#/components/schemas/TenantResponse"},
							},
						},
					},
				},
				"404": errorResponse("Тенант не найден"),
			},
			"delete": map[string]any{
				"summary":     "Удалить тенант",
				"operationId": "admin_remove_tenant",
				"tags":        adminTag,
				"security":    adminSec,
				"parameters": []map[string]any{
					{"name": "id", "in": "path", "required": true, "schema": map[string]any{"type": "string"}},
				},
				"responses": map[string]any{
					"200": map[string]any{"description": "Успешно удалено"},
					"403": errorResponse("Нельзя удалить default тенант"),
					"404": errorResponse("Тенант не найден"),
				},
			},
		}

		paths["/admin/config"] = map[string]any{
			"get": map[string]any{
				"summary":     "Текущий конфиг default-тенанта",
				"operationId": "admin_get_config",
				"tags":        adminTag,
				"security":    adminSec,
				"responses": map[string]any{
					"200": map[string]any{
						"description": "Конфигурация",
						"content": map[string]any{
							"application/json": map[string]any{
								"schema": map[string]any{"$ref": "#/components/schemas/ConfigResponse"},
							},
						},
					},
				},
			},
			"post": map[string]any{
				"summary":     "Обновить конфиг default-тенанта",
				"operationId": "admin_update_config",
				"tags":        adminTag,
				"security":    adminSec,
				"requestBody": map[string]any{
					"required": true,
					"content": map[string]any{
						"application/json": map[string]any{
							"schema": map[string]any{"type": "object"},
						},
					},
				},
				"responses": map[string]any{
					"200": map[string]any{"description": "Конфиг обновлен и применен"},
					"400": errorResponse("Ошибка валидации или сборки роутера"),
					"500": errorResponse("Ошибка записи на диск"),
				},
			},
		}

		paths["/admin/config/reload"] = map[string]any{
			"post": map[string]any{
				"summary":     "Hot reload конфига с диска",
				"operationId": "admin_config_reload",
				"tags":        adminTag,
				"security":    adminSec,
				"responses": map[string]any{
					"200": map[string]any{"description": "Конфиг перезагружен"},
					"500": errorResponse("Ошибка перезагрузки"),
				},
			},
		}

		paths["/admin/config/versions"] = map[string]any{
			"get": map[string]any{
				"summary":     "История версий конфига",
				"operationId": "admin_config_versions",
				"tags":        adminTag,
				"security":    adminSec,
				"responses": map[string]any{
					"200": map[string]any{
						"description": "Список версий",
						"content": map[string]any{
							"application/json": map[string]any{
								"schema": map[string]any{"type": "array", "items": map[string]any{"$ref": "#/components/schemas/VersionInfo"}},
							},
						},
					},
				},
			},
		}

		paths["/admin/discover"] = map[string]any{
			"get": map[string]any{
				"summary":     "Сгенерировать конфиг из схемы БД",
				"operationId": "admin_discover",
				"tags":        adminTag,
				"security":    adminSec,
				"parameters": []map[string]any{
					{
						"name": "raw", "in": "query", "required": false,
						"schema":      map[string]any{"type": "string", "enum": []string{"true"}},
						"description": "?raw=true — отдать чистый JSON конфига",
					},
				},
				"responses": map[string]any{
					"200": map[string]any{
						"description": "Сгенерированный конфиг",
						"content": map[string]any{
							"application/json": map[string]any{
								"schema": map[string]any{"type": "object"},
							},
						},
					},
					"500": errorResponse("Ошибка подключения или интроспекции БД"),
				},
			},
		}

		paths["/admin/config/rewrite"] = map[string]any{
			"post": map[string]any{
				"summary":     "Перегенерировать и сохранить конфиг из схемы БД",
				"operationId": "admin_config_rewrite",
				"description": "Интроспектирует БД, генерирует config.json и перезаписывает файл на диске. Требует настроенный configPath.",
				"requestBody": map[string]any{
					"required": true,
					"content": map[string]any{
						"application/json": map[string]any{
							"schema": map[string]any{"type": "object"},
						},
					},
				},
				"responses": map[string]any{
					"200": map[string]any{
						"description": "Конфиг перезаписан",
						"content": map[string]any{
							"application/json": map[string]any{
								"schema": map[string]any{"$ref": "#/components/schemas/RewriteResponse"},
							},
						},
					},
				},
				"400": errorResponse("configPath не настроен"),
				"500": errorResponse("Ошибка подключения, интроспекции или записи файла"),
			},
		}
	}

	return paths
}

func buildSystemComponents() map[string]any {
	return map[string]any{
		"schemas": map[string]any{
			"ErrorResponse": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"error":   map[string]any{"type": "string", "description": "Код ошибки"},
					"message": map[string]any{"type": "string", "description": "Описание ошибки"},
				},
			},
			"HealthResponse": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"status": map[string]any{"type": "string", "enum": []string{"ok", "degraded"}},
					"db":     map[string]any{"type": "string", "enum": []string{"ok", "error"}},
				},
			},
			"TenantResponse": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"id":         map[string]any{"type": "string"},
					"driver":     map[string]any{"type": "string"},
					"entities":   map[string]any{"type": "integer"},
					"endpoints":  map[string]any{"type": "integer"},
					"healthy":    map[string]any{"type": "boolean"},
					"error":      map[string]any{"type": "string"},
					"created_at": map[string]any{"type": "string", "format": "date-time"},
				},
			},
			"TenantRequest": map[string]any{
				"type":     "object",
				"required": []string{"id", "config"},
				"properties": map[string]any{
					"id":          map[string]any{"type": "string"},
					"config":      map[string]any{"type": "object", "description": "Full config.Config object"},
					"config_path": map[string]any{"type": "string", "description": "Optional path to save config file"},
				},
			},
			"ConfigResponse": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"version":   map[string]any{"type": "integer"},
					"driver":    map[string]any{"type": "string"},
					"entities":  map[string]any{"type": "array", "items": map[string]any{"type": "object"}},
					"endpoints": map[string]any{"type": "array", "items": map[string]any{"type": "object"}},
				},
			},
			"VersionInfo": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"name":       map[string]any{"type": "string"},
					"size_bytes": map[string]any{"type": "integer"},
					"mod_time":   map[string]any{"type": "string", "format": "date-time"},
				},
			},
			"RewriteResponse": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"status":    map[string]any{"type": "string"},
					"path":      map[string]any{"type": "string"},
					"entities":  map[string]any{"type": "integer"},
					"endpoints": map[string]any{"type": "integer"},
					"note":      map[string]any{"type": "string"},
				},
			},
		},
		"securitySchemes": map[string]any{
			"BearerAuth": map[string]any{
				"type":         "http",
				"scheme":       "bearer",
				"bearerFormat": "JWT",
				"description":  "Введите ADMIN_TOKEN из .env",
			},
		},
	}
}

// buildPaths собирает paths из cfg.Endpoints + /admin/discover если hasAdmin.
func buildPaths(cfg *config.Config, hasAdmin bool) map[string]any {
	paths := make(map[string]any)
	for _, ep := range cfg.Endpoints {
		method := strings.ToLower(string(ep.Method))
		path := ep.Path
		tag := entityTag(ep)
		op := map[string]any{
			"summary":     ep.Description,
			"description": buildDescription(cfg, ep),
			"operationId": operationID(ep),
			"tags":        []string{tag},
			"responses": map[string]any{
				"200": map[string]any{
					"description": "Успешный ответ",
					"content": map[string]any{
						"application/json": map[string]any{
							"schema": responseSchema(ep),
						},
					},
				},
				"404": errorResponse("Сущность не найдена"),
				"500": errorResponse("Внутренняя ошибка сервера"),
			},
		}
		params := make([]map[string]any, 0)
		for _, p := range extractPathParams(path) {
			params = append(params, map[string]any{
				"name": p, "in": "path", "required": true, "schema": map[string]any{"type": "string"},
			})
		}
		if qp := queryParam(ep); qp != "" {
			params = append(params, map[string]any{
				"name": qp, "in": "query", "required": false, "schema": map[string]any{"type": "string"},
				"description": "Поисковый запрос. Без параметра — список всех записей.",
			})
		}
		if len(params) > 0 {
			op["parameters"] = params
		}
		if _, ok := paths[path]; !ok {
			paths[path] = make(map[string]any)
		}
		paths[path].(map[string]any)[method] = op
	}
	// /admin/* (if admin is enabled)
	if hasAdmin {
		adminTag := []string{"Admin"}
		adminSec := []map[string]any{{"BearerAuth": []string{}}}
		// --- Tenant Management ---
		paths["/admin/tenants"] = map[string]any{
			"get": map[string]any{
				"summary":     "Список всех тенантов",
				"operationId": "admin_list_tenants",
				"tags":        adminTag,
				"security":    adminSec,
				"responses": map[string]any{
					"200": map[string]any{
						"description": "Список тенантов",
						"content": map[string]any{
							"application/json": map[string]any{
								"schema": map[string]any{"type": "array", "items": map[string]any{"$ref": "#/components/schemas/TenantResponse"}},
							},
						},
					},
				},
			},
			"post": map[string]any{
				"summary":     "Добавить новый тенант",
				"operationId": "admin_add_tenant",
				"tags":        adminTag,
				"security":    adminSec,
				"requestBody": map[string]any{
					"required": true,
					"content": map[string]any{
						"application/json": map[string]any{
							"schema": map[string]any{"$ref": "#/components/schemas/TenantRequest"},
						},
					},
				},
				"responses": map[string]any{
					"201": map[string]any{
						"description": "Тенант создан",
						"content": map[string]any{
							"application/json": map[string]any{
								"schema": map[string]any{"type": "object"},
							},
						},
					},
					"409": errorResponse("Тенант уже существует"),
					"500": errorResponse("Ошибка при создании тенанта"),
				},
			},
		}
		paths["/admin/tenants/{id}"] = map[string]any{
			"get": map[string]any{
				"summary":     "Информация о конкретном тенанте",
				"operationId": "admin_get_tenant",
				"tags":        adminTag,
				"security":    adminSec,
				"parameters": []map[string]any{
					{"name": "id", "in": "path", "required": true, "schema": map[string]any{"type": "string"}},
				},
				"responses": map[string]any{
					"200": map[string]any{
						"description": "Данные тенанта",
						"content": map[string]any{
							"application/json": map[string]any{
								"schema": map[string]any{"$ref": "#/components/schemas/TenantResponse"},
							},
						},
					},
					"404": errorResponse("Тенант не найден"),
				},
			},
			"delete": map[string]any{
				"summary":     "Удалить тенант",
				"operationId": "admin_remove_tenant",
				"tags":        adminTag,
				"security":    adminSec,
				"parameters": []map[string]any{
					{"name": "id", "in": "path", "required": true, "schema": map[string]any{"type": "string"}},
				},
				"responses": map[string]any{
					"200": map[string]any{"description": "Успешно удалено"},
					"403": errorResponse("Нельзя удалить default тенант"),
					"404": errorResponse("Тенант не найден"),
				},
			},
		}
		// --- Config Management ---
		paths["/admin/config"] = map[string]any{
			"get": map[string]any{
				"summary":     "Текущий конфиг default-тенанта",
				"operationId": "admin_get_config",
				"tags":        adminTag,
				"security":    adminSec,
				"responses": map[string]any{
					"200": map[string]any{
						"description": "Конфигурация",
						"content": map[string]any{
							"application/json": map[string]any{
								"schema": map[string]any{"$ref": "#/components/schemas/ConfigResponse"},
							},
						},
					},
				},
			},
			"post": map[string]any{
				"summary":     "Обновить конфиг default-тенанта",
				"operationId": "admin_update_config",
				"tags":        adminTag,
				"security":    adminSec,
				"requestBody": map[string]any{
					"required": true,
					"content": map[string]any{
						"application/json": map[string]any{
							"schema": map[string]any{"type": "object"},
						},
					},
				},
				"responses": map[string]any{
					"200": map[string]any{"description": "Конфиг обновлен и применен"},
					"400": errorResponse("Ошибка валидации или сборки роутера"),
					"500": errorResponse("Ошибка записи на диск"),
				},
			},
		}
		paths["/admin/config/reload"] = map[string]any{
			"post": map[string]any{
				"summary":     "Hot reload конфига с диска",
				"operationId": "admin_config_reload",
				"tags":        adminTag,
				"security":    adminSec,
				"responses": map[string]any{
					"200": map[string]any{"description": "Конфиг перезагружен"},
					"500": errorResponse("Ошибка перезагрузки"),
				},
			},
		}
		paths["/admin/config/versions"] = map[string]any{
			"get": map[string]any{
				"summary":     "История версий конфига",
				"operationId": "admin_config_versions",
				"tags":        adminTag,
				"security":    adminSec,
				"responses": map[string]any{
					"200": map[string]any{
						"description": "Список версий",
						"content": map[string]any{
							"application/json": map[string]any{
								"schema": map[string]any{"type": "array", "items": map[string]any{"$ref": "#/components/schemas/VersionInfo"}},
							},
						},
					},
				},
			},
		}
		// Existing legacy admin endpoints
		paths["/admin/discover"] = map[string]any{
			"get": map[string]any{
				"summary":     "Сгенерировать конфиг из схемы БД (GET-версия)",
				"description": "Интроспектирует БД и возвращает config.json. ?raw=true — чистый JSON без обёртки.",
				"operationId": "admin_discover",
				"tags":        adminTag,
				"security":    adminSec,
				"parameters": []map[string]any{
					{
						"name": "raw", "in": "query", "required": false,
						"schema":      map[string]any{"type": "string", "enum": []string{"true"}},
						"description": "?raw=true — отдать чистый JSON конфига",
					},
				},
				"responses": map[string]any{
					"200": map[string]any{
						"description": "Сгенерированный конфиг",
						"content": map[string]any{
							"application/json": map[string]any{
								"schema": map[string]any{"type": "object"},
							},
						},
					},
					"500": errorResponse("Ошибка подключения или интроспекции БД"),
				},
			},
		}
		paths["/admin/config/rewrite"] = map[string]any{
			"post": map[string]any{
				"summary":     "Перегенерировать и сохранить конфиг из схемы БД",
				"description": "Интроспектирует БД, генерирует config.json и перезаписывает файл на диске. Требует настроенный configPath.",
				"operationId": "admin_config_rewrite",
				"tags":        adminTag,
				"security":    adminSec,
				"responses": map[string]any{
					"200": map[string]any{
						"description": "Конфиг перезаписан",
						"content": map[string]any{
							"application/json": map[string]any{
								"schema": map[string]any{"$ref": "#/components/schemas/RewriteResponse"},
							},
						},
					},
					"400": errorResponse("configPath не настроен"),
					"500": errorResponse("Ошибка подключения, интроспекции или записи файла"),
				},
			},
		}
	}
	return paths
}

// buildComponents собирает схемы ответов.
func buildComponents(cfg *config.Config) map[string]any {
	schemas := make(map[string]any)
	schemas["ErrorResponse"] = map[string]any{
		"type": "object",
		"properties": map[string]any{
			"error":   map[string]any{"type": "string", "description": "Код ошибки"},
			"message": map[string]any{"type": "string", "description": "Описание ошибки"},
		},
	}
	schemas["HealthResponse"] = map[string]any{
		"type": "object",
		"properties": map[string]any{
			"status": map[string]any{"type": "string", "enum": []string{"ok", "degraded"}},
			"db":     map[string]any{"type": "string", "enum": []string{"ok", "error"}},
		},
	}
	schemas["RewriteResponse"] = map[string]any{
		"type": "object",
		"properties": map[string]any{
			"status":    map[string]any{"type": "string"},
			"path":      map[string]any{"type": "string"},
			"entities":  map[string]any{"type": "integer"},
			"endpoints": map[string]any{"type": "integer"},
			"note":      map[string]any{"type": "string"},
		},
	}
	// Admin API schemas
	schemas["TenantResponse"] = map[string]any{
		"type": "object",
		"properties": map[string]any{
			"id":         map[string]any{"type": "string"},
			"driver":     map[string]any{"type": "string"},
			"entities":   map[string]any{"type": "integer"},
			"endpoints":  map[string]any{"type": "integer"},
			"healthy":    map[string]any{"type": "boolean"},
			"error":      map[string]any{"type": "string"},
			"created_at": map[string]any{"type": "string", "format": "date-time"},
		},
	}
	schemas["TenantRequest"] = map[string]any{
		"type":     "object",
		"required": []string{"id", "config"},
		"properties": map[string]any{
			"id":          map[string]any{"type": "string"},
			"config":      map[string]any{"type": "object", "description": "Full config.Config object"},
			"config_path": map[string]any{"type": "string", "description": "Optional path to save config file"},
		},
	}
	schemas["ConfigResponse"] = map[string]any{
		"type": "object",
		"properties": map[string]any{
			"version":   map[string]any{"type": "integer"},
			"driver":    map[string]any{"type": "string"},
			"entities":  map[string]any{"type": "array", "items": map[string]any{"type": "object"}},
			"endpoints": map[string]any{"type": "array", "items": map[string]any{"type": "object"}},
		},
	}
	schemas["VersionInfo"] = map[string]any{
		"type": "object",
		"properties": map[string]any{
			"name":       map[string]any{"type": "string"},
			"size_bytes": map[string]any{"type": "integer"},
			"mod_time":   map[string]any{"type": "string", "format": "date-time"},
		},
	}
	for _, e := range cfg.Entities {
		schemas[e.Name] = entitySchema(e)
	}
	return map[string]any{
		"schemas": schemas,
		"securitySchemes": map[string]any{
			"BearerAuth": map[string]any{
				"type":         "http",
				"scheme":       "bearer",
				"bearerFormat": "JWT",
				"description":  "Введите ADMIN_TOKEN из .env",
			},
		},
	}
}

func entitySchema(e config.Entity) map[string]any {
	props := make(map[string]any)
	required := make([]string, 0)
	for _, f := range e.Fields {
		props[f.Name] = map[string]any{
			"type":        openapiType(f.Type),
			"description": f.Description,
		}
		if f.Nullable == nil || !*f.Nullable {
			required = append(required, f.Name)
		}
	}
	s := map[string]any{"type": "object", "properties": props}
	if len(required) > 0 {
		s["required"] = required
	}
	return s
}

func openapiType(t config.FieldType) string {
	switch t {
	case config.FieldTypeString:
		return "string"
	case config.FieldTypeInt:
		return "integer"
	case config.FieldTypeFloat:
		return "number"
	case config.FieldTypeBool:
		return "boolean"
	case config.FieldTypeJSON:
		return "object"
	case config.FieldTypeDatetime, config.FieldTypeDate:
		return "string"
	default:
		return "string"
	}
}

func operationID(ep config.Endpoint) string {
	parts := strings.Split(strings.Trim(ep.Path, "/"), "/")
	clean := make([]string, 0, len(parts))
	for _, p := range parts {
		if strings.HasPrefix(p, "{") && strings.HasSuffix(p, "}") {
			clean = append(clean, "by_"+strings.Trim(p, "{}"))
		} else {
			clean = append(clean, p)
		}
	}
	return strings.Join(clean, "_")
}

func entityTag(ep config.Endpoint) string {
	switch {
	case ep.Path == "/health" || ep.Path == "/stats" || ep.Path == "/docs" || ep.Path == "/openapi.json":
		return "System"
	case ep.Entity != "":
		return ep.Entity
	case ep.QueryID != "":
		return "Custom Queries"
	default:
		return "General"
	}
}

func buildDescription(cfg *config.Config, ep config.Endpoint) string {
	parts := []string{ep.Description}
	if ep.Op == config.OpCustomQuery {
		if cq, ok := cfg.CustomQueries[ep.QueryID]; ok {
			parts = append(parts, "", "SQL: `"+cq.SQL+"`")
		}
	}
	if ep.Entity != "" {
		for _, e := range cfg.Entities {
			if e.Name == ep.Entity {
				fields := make([]string, len(e.Fields))
				for i, f := range e.Fields {
					fields[i] = f.Name
				}
				parts = append(parts, "", "Поля: `"+strings.Join(fields, "`, `")+"`")
				break
			}
		}
	}
	return strings.Join(parts, "\n")
}

func extractPathParams(path string) []string {
	var params []string
	for {
		start := strings.Index(path, "{")
		if start < 0 {
			break
		}
		end := strings.Index(path[start:], "}")
		if end < 0 {
			break
		}
		params = append(params, path[start+1:start+end])
		path = path[start+end+1:]
	}
	return params
}

func queryParam(ep config.Endpoint) string {
	if ep.QueryParam != "" {
		return ep.QueryParam
	}
	if ep.Op == config.OpFind && ep.SearchField != "" {
		return ep.SearchField
	}
	return ""
}

func responseSchema(ep config.Endpoint) map[string]any {
	switch {
	case ep.Path == "/health":
		return map[string]any{"$ref": "#/components/schemas/HealthResponse"}
	case ep.Path == "/stats":
		return map[string]any{"type": "object", "additionalProperties": map[string]any{"type": "integer"}}
	case ep.Op == config.OpGetByID && ep.Entity != "":
		return map[string]any{"$ref": "#/components/schemas/" + ep.Entity}
	case (ep.Op == config.OpFind || ep.Op == config.OpList) && ep.Entity != "":
		return map[string]any{"type": "array", "items": map[string]any{"$ref": "#/components/schemas/" + ep.Entity}}
	case ep.Op == config.OpCustomQuery:
		return map[string]any{"type": "array", "items": map[string]any{"type": "object"}}
	}
	return map[string]any{"type": "object"}
}

func errorResponse(desc string) map[string]any {
	return map[string]any{
		"description": desc,
		"content": map[string]any{
			"application/json": map[string]any{
				"schema": map[string]any{"$ref": "#/components/schemas/ErrorResponse"},
			},
		},
	}
}

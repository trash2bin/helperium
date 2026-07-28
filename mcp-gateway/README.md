# mcp-gateway

Generic MCP (Model Context Protocol) сервер на Go. Заменил Python-сервер `mcp_server/` (удалён).

Мост между LLM-агентом и data-service: превращает REST-эндпоинты БД в MCP-инструменты.

## 🏢 Multi-tenancy архитектура

`mcp-gateway` — stateless шлюз между агентами и data-service.

### Одноtenantный режим (Legacy)

```
Агент → SSE → mcp-gateway → HTTP → data-service (X-Tenant-ID: tenant-a)
```

- SSE-сессия (GET /mcp) → одна на tenant
- Инструменты без префикса: `grep_products`, `filter_products`, `get_products`
- Включён, когда `X-Tenant-ID` содержит **один** tenant

### Composite Multi-Tenant Mode

```text
X-Tenant-ID: tenant-a               → legacy: инструменты без префикса
X-Tenant-ID: tenant-a,tenant-b      → composite: tenant-a__grep_products, tenant-b__grep_products
```

Режим включается автоматически, когда `X-Tenant-ID` содержит несколько tenant'ов через запятую.
`createCompositeServer()` загружает конфиги всех tenant'ов и регистрирует инструменты с префиксом `{tenantID}__`.

**Изоляция через closure:** `makeHandler(td, client, tenantID)` — tenantID зашит в closure.
Инструмент `tenant-a__grep_products` всегда идёт в data-service с `X-Tenant-ID: tenant-a`,
даже если клиент подменит заголовок.

**Кэш на SSE-сессии:** `sseSession.ensureCompositeServer()` переиспользует созданный сервер,
пока список tenant'ов не изменится.

### Ключевые файлы

| Файл | Назначение |
|---|---|
| `cmd/main.go` | Точка входа: SSE-сессии, JSON-RPC, composite/routing, debug |
| `cmd/mcp_debug.go` | `//go:embed playground.html` |
| `internal/httpclient/client.go` | HTTP-клиент к data-service: `FetchConfigWithTenant`, `Call` |
| `internal/ragclient/client.go` | HTTP-клиент к RAG: `SearchDocuments`, `ListDocuments`, `GetRagContext` |
| `internal/tools/tools.go` | **Реестр инструментов**: `NewRegistry`, `NewPrefixedRegistry`, `RegisterAll`, `makeHandler`, `deriveToolName` |

## Как работают инструменты

### Создание MCP-сервера

1. **SSE-сессия** (GET /mcp): клиент открывает долгий SSE-стрим, получает `event: endpoint` с URL для POST
2. **POST /mcp/message?sessionId=...**: JSON-RPC → `mcpPostHandler()` → `mcpServer.HandleMessage()`
3. **Создание MCP-сервера** (`createServerForTenant` / `createCompositeServer`):
   - `httpClient.FetchConfigWithTenant(tenantID)` → GET к data-service `/mcp/manifest`
   - `tools.NewRegistry(cfg)` → конвертирует `mcp_tools[]` из конфига в MCP-инструменты
   - `registry.RegisterAll(mcpServer)` → регистрирует хендлеры

> **Кэш манифеста:** `FetchConfigWithTenant()` кэширует ответ `/mcp/manifest` на 30 секунд (TTL, per-tenant).
> Повторные вызовы в пределах окна не ходят в data-service.
> После config rewrite (POST /admin/config/rewrite) нужно вызвать `InvalidateManifestCache(tenantID)`
> для принудительного сброса кэша — иначе до 30 секунд будут использоваться старые тулы.
> `InvalidateManifestCache()` без аргументов очищает весь кэш.
4. **Каждый инструмент** — closure с `client.Call(ctx, endpoint, params)` к data-service

### Поток вызова инструмента

1. **Запрос**: Агент шлёт JSON-RPC `tools/call` через SSE-сессию с `X-Tenant-ID`
2. **Манифест**: mcp-gateway проксирует `/mcp/manifest` → data-service (тем tenant'ом)
3. **Разрешение**: `Registry.buildTools()` — маппинг endpoint → MCP toolDef
4. **Вызов**: `makeHandler()` → `client.Call(ctx, endpoint, params)` → data-service → JSON → MCP-результат

## Схема именования инструментов

| Op | Имя | Пример |
|---|---|---|
| `get_by_id` | `get_{entity}` | `get_student` |
| `grep` (strategy) | `grep_{entity}` | `grep_products` |
| `filter` (strategy) | `filter_{entity}` | `filter_products` |
| `schema` (strategy) | `schema_{entity}` | `schema_products` |
| `distinct` | `distinct_{entity}` | `distinct_products` |
| `count` | `count_{entity}` | `count_products` |
| `builtin_health` | `health` | `health` |
| `builtin_stats` | `stats` | `stats` |
| `custom_query` | `{query_id}` | `student_grades` |

> **Примечание:** `find` / `list` — REST-эндпоинты для data-service, но **не MCP-тулы**.
> MCP-тулы для поиска/фильтрации генерируются через стратегии (`grep`, `filter`, `schema`).

Санитизация: `deriveToolName()` удаляет `{` `}` из имён (Mistral reject).

> **⚠️ Баг в legacy fallback `deriveToolName()`:** функция корректно обрабатывает только `OpGetByID`.
> Все остальные операции (`find`, `list`, `distinct`, `count` и стратегии) падают в `default: return ""`.
> На практике это не критично, т.к. data-service возвращает явный `mcp_tools[]` в манифесте,
> и `buildTools()` использует его в приоритете. Fallback срабатывает только в legacy-режиме
> (конфиг без `mcp_tools[]`), где в итоге регистрируются только `get_*` инструменты.
>
> Аналогичная ситуация в `buildDefaultDesc()` — осмысленное описание генерируется только для `OpGetByID`.

## RAG-инструменты

Три тула регистрируются через `registerRagTools()`, но **только если RAG доступен** (проверка `RagEnabled()` → `ragClient.IsAvailable()`).
Если RAG_SERVICE_URL не задан или health-check падает — инструменты не регистрируются:

| Инструмент | RAG-эндпоинт | Описание |
|---|---|---|
| `search_documents` | POST /search | Семантический поиск |
| `list_documents` | POST /documents/list | Список документов |
| `get_rag_context` | POST /context | Контекст для LLM |

## Эндпоинты

| Путь | Метод | Описание | Auth |
|---|---|---|---|
| `/health` | GET | Статус | — |
| `/metrics` | GET | Prometheus | — |
| `/mcp` | GET | SSE endpoint | MCP_API_KEY |
| `/sse` | GET | Алиас `/mcp` | MCP_API_KEY |
| `/` | GET | Алиас `/mcp` | MCP_API_KEY |
| `/mcp/message` | POST | JSON-RPC | MCP_API_KEY |
| `/mcp` | POST | Алиас `/mcp/message` | MCP_API_KEY |
| `/message` | POST | Алиас `/mcp/message` | MCP_API_KEY |
| `/` | POST | Алиас `/mcp/message` | MCP_API_KEY |
| `/mcp/manifest` | GET | Прокси манифеста → data-service | MCP_API_KEY |
| `/mcp/tools/mapping` | GET | JSON `{tool: display_name}` | MCP_API_KEY |
| `/mcp/schema` | GET | Прокси схемы → data-service | MCP_API_KEY |
| `/debug` | GET | MCP Playground (dev) | MCP_API_KEY |
| `/debug/sessions` | GET | Активные SSE-сессии | MCP_API_KEY |
| `/debug/config` | GET | Текущий конфиг | MCP_API_KEY |
| `/config` | GET | Алиас `/debug/config` | MCP_API_KEY |
| `/docs` | GET | Swagger UI | MCP_API_KEY |
| `/openapi.json` | GET | OpenAPI spec | MCP_API_KEY |

**Auth:** если `MCP_API_KEY` не установлена — все маршруты без аутентификации. `/health` и `/metrics` всегда открыты.

## 📚 Ссылки

- [AGENTS.md](../AGENTS.md) — общая архитектура проекта, data flow, правила работы
- [doc/agents/mcp-session-lifecycle.md](../doc/agents/mcp-session-lifecycle.md) — полный lifecycle MCP-сессии
- [doc/agents/search-strategies.md](../doc/agents/search-strategies.md) — стратегии поиска (grep, filter, schema)
- [mcp-gateway/internal/tools/tools.go](internal/tools/tools.go) — реестр инструментов (source of truth)

## Переменные окружения

| Переменная | Дефолт | Описание |
|---|---|---|
| `MCP_PORT` | `8083` | Порт HTTP |
| `MCP_API_KEY` | — | Bearer-токен для auth (пустой → auth отключён) |
| `DATA_SERVICE_URL` | `http://127.0.0.1:8084` | Базовый URL data-service |
| `DATA_SERVICE_TIMEOUT` | `30` | Таймаут HTTP к data-service (сек) |
| `RAG_SERVICE_URL` | `http://127.0.0.1:8082` | Базовый URL RAG |
| `RAG_HTTP_TIMEOUT` | `30` | Таймаут HTTP к RAG (сек) |
| `BOOTSTRAP_TENANT_ID` | — | Tenant ID для первоначальной загрузки манифеста |
| `MCP_MAX_SESSIONS` | `1000` | Max SSE-сессий (OOM protection) |
| `MCP_SESSION_IDLE_TIMEOUT` | `5m` | Таймаут простоя SSE |
| `MCP_SESSION_MAX_LIFETIME` | `30m` | Макс. время жизни SSE |
| `MCP_POST_HANDLER_TIMEOUT` | `25` | Таймаут JSON-RPC (сек) |
| `MCP_READ_HEADER_TIMEOUT` | `10` | Read header timeout (сек, slowloris защита) |
| `MCP_IDLE_TIMEOUT` | `120` | Idle timeout HTTP (сек) |
| `MCP_DEV` | — | Debug-режим (playground, доп. логи) |
| `MCP_RATE_LIMIT_RPS` | `10` | Requests per second (rate limiter) |
| `MCP_RATE_LIMIT_BURST` | `20` | Burst size (rate limiter) |

## Управление сессиями

- **MaxSessions = 1000** — лимит одновременных SSE-сессий
- **SessionIdleTimeout = 5m** — закрытие неактивных SSE
- **SessionMaxLifetime = 30m** — макс. время жизни SSE
- При превышении лимита → `503 Service Unavailable`
- `/debug/sessions` — мониторинг активных сессий

## Метрики (Prometheus)

| Метрика | Тип | Labels |
|---|---|---|
| `mcp_tool_calls_total` | Counter | `tool`, `tenant`, `status` |
| `mcp_sessions_active` | Gauge | `tenant` |
| `mcp_rate_limit_hits_total` | Counter | `tenant` |

## Dev-режим

```bash
MCP_DEV=true DATA_SERVICE_URL=http://127.0.0.1:8084 go run ./cmd/
```

Доступен `MCP Playground` на `/debug` (веб-интерфейс для тестирования тулов).

## Запуск

```bash
# data-service
cd ../data-service && go run ./cmd/server/

# mcp-gateway
DATA_SERVICE_URL=http://127.0.0.1:8084 go run ./cmd/
```

Регистрация tenant'ов через data-service (agent-db или admin API).

## Smoke test

```bash
# Манифест tenant'a
curl -s -H "X-Tenant-ID: default" http://127.0.0.1:8083/mcp/manifest | jq '.tools | length'

# Прямой JSON-RPC вызов (без SSE)
curl -s -X POST http://127.0.0.1:8083/mcp/message \
  -H "X-Tenant-ID: default" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}' | jq .
```

## Troubleshooting

| Симптом | Причина | Фикс |
|---|---|---|
| `connection refused` :8083 | mcp-gateway не запущен | `go run ./cmd/` |
| Пустой манифест (0 tools) | Tenant не зарегистрирован | register через data-service admin API |
| 401 Unauthorized | MCP_API_KEY mismatch | Синхронизируй токен |

---
**Last verified:** 2026-07-28 (commit `a12e54c96fb1b751902329133786daf8bab8e971`)
---
**Last verified:** 2026-07-28 (commit `a12e54c96fb1b751902329133786daf8bab8e971`)

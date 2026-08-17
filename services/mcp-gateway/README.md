# mcp-gateway

Generic MCP (Model Context Protocol) сервер на Go. Заменил Python-сервер `mcp_server/` (удалён).

Мост между LLM-агентом и data-service: превращает REST-эндпоинты БД в MCP-инструменты.

## 🏢 Multi-tenancy архитектура

`mcp-gateway` — tenant-scoped **stateful** Streamable HTTP gateway между агентами и data-service. MCP sessions и registry handlers process-local; production topology пока должна использовать один gateway instance (либо sticky routing при будущем scale-out).

### Одноtenantный режим

```
Агент → Streamable HTTP `/mcp` → mcp-gateway → HTTP → data-service (X-Tenant-ID: tenant-a)
```

- Stateful Streamable HTTP handler создаётся для одного уже разрешённого tenant scope
- Инструменты без префикса: `grep_products`, `filter_products`, `get_products`
- Включён, когда `X-Tenant-ID` содержит **один** tenant

### Composite Multi-Tenant Mode

```text
X-Tenant-ID: tenant-a               → инструменты без префикса
X-Tenant-ID: tenant-a,tenant-b      → composite: tenant-a__grep_products, tenant-b__grep_products
```

Режим включается автоматически, когда `X-Tenant-ID` содержит несколько tenant'ов через запятую.
`createCompositeServer()` загружает конфиги всех tenant'ов и регистрирует инструменты с префиксом `{tenantID}__`.

**Изоляция через closure:** `makeHandler(td, client, tenantID)` — tenantID зашит в closure.
Инструмент `tenant-a__grep_products` всегда идёт в data-service с `X-Tenant-ID: tenant-a`,
даже если клиент подменит заголовок.

**Кэш handler-а:** `streamableTenantRegistry` переиспользует stateful Streamable HTTP handler для exact ordered tenant set; число scopes ограничено `MCP_MAX_STREAMABLE_TENANT_SCOPES`. Один composite scope допускает не более `MCP_MAX_TENANTS_PER_SCOPE` unique tenant IDs; duplicate IDs получают `400`.

### Ключевые файлы

| Файл | Назначение |
|---|---|
| `cmd/main.go` | Точка входа: Streamable HTTP, tenant-scoped registry, composite routing и config diagnostics |
| `internal/httpclient/client.go` | HTTP-клиент к data-service: `FetchConfigWithTenant`, `Call` |
| `internal/ragclient/client.go` | HTTP-клиент к RAG: `SearchDocuments`, `ListDocuments`, `GetRagContext` |
| `internal/tools/tools.go` | **Реестр инструментов**: `NewRegistry`, `NewPrefixedRegistry`, `RegisterAll`, `makeHandler`, `deriveToolName` |

## MCP transport

`/mcp` — **единственный стандартный Streamable HTTP MCP endpoint** на `mcp-go v0.58`. Он поддерживает standard single-endpoint request/response flow и transport-managed MCP sessions. Для каждого уже разрешённого набора `X-Tenant-ID` gateway создаёт отдельный stateful handler: manifest и tool closures остаются tenant-scoped, а `data-service` получает primary tenant через request context.

Не публикуй этот endpoint без `MCP_API_KEY`: `X-Tenant-ID` определяет scope tools, но сам по себе не является криптографическим доказательством права клиента на tenant. Production ставит `MCP_REQUIRE_AUTH=true`; gateway fail-fast завершится, если ключ пуст. Legacy GET-SSE/POST JSON-RPC compatibility path удалён; rollback выполняется deploy предыдущего tested image.

### Transport и security contract

| Контракт | Гарантия gateway |
|---|---|
| Единственный transport path | Только `GET`, `POST` и `DELETE /mcp` обслуживаются mcp-go Streamable HTTP server; `/`, `/sse`, `/mcp/message` и `/mcp/v2` возвращают `404` |
| Tenant scope | Только header `X-Tenant-ID`; query parameters намеренно игнорируются. Отсутствие header на `/mcp` возвращает `400` |
| Composite scope | `X-Tenant-ID: a,b` создаёт handler для exact ordered scope и публикует только prefixed tools (`a__db_map`, `b__db_map`); duplicate IDs и scope выше `MCP_MAX_TENANTS_PER_SCOPE` возвращают `400` до загрузки manifests |
| Metadata scope | `/mcp/manifest`, `/mcp/tools/mapping` и `/mcp/schema` требуют ровно один validated tenant ID; отсутствующий/default fallback и composite metadata request получают `400` |
| Service authentication | `MCP_REQUIRE_AUTH=true` требует non-empty `MCP_API_KEY` already at startup; все non-health/metrics routes требуют exact `Authorization: Bearer <token>`, отсутствие или ошибка — `401` |
| Browser Origin | Requests без `Origin` разрешены для service clients. Любой present Origin должен exactly match `MCP_ALLOWED_ORIGINS`, иначе `403` |
| Capacity bound | Новый tenant scope свыше `MCP_MAX_STREAMABLE_TENANT_SCOPES` получает `503`, существующий cached scope остаётся доступным |
| Abuse control | Rate limiter применяется ко всем Streamable HTTP methods на `/mcp`; превышение возвращает `429` |

> Gateway не принимает raw session ID от application-кода. Используй официальный MCP SDK v2; он согласует initialization, request/response и `DELETE` cleanup через `/mcp`.

## Как работают инструменты

### Создание MCP-сервера

1. **Streamable HTTP request** (`/mcp`): transport-managed MCP session несёт JSON-RPC request/response
2. **Создание MCP-сервера** (`createServerForTenant` / `createCompositeServer`) на первом request tenant scope:
   - `httpClient.FetchConfigWithTenant(tenantID)` → GET к data-service `/mcp/manifest`
   - `tools.NewRegistry(cfg)` → конвертирует `mcp_tools[]` из конфига в MCP-инструменты
   - `registry.RegisterAll(mcpServer)` → регистрирует хендлеры

> **Кэш манифеста:** `FetchConfigWithTenant()` кэширует ответ `/mcp/manifest` на 30 секунд (TTL, per-tenant).
> Повторные вызовы в пределах окна не ходят в data-service.
> После config rewrite (POST /admin/config/rewrite) нужно вызвать `InvalidateManifestCache(tenantID)`
> для принудительного сброса кэша — иначе до 30 секунд будут использоваться старые тулы.
> `InvalidateManifestCache()` без аргументов очищает весь кэш.
3. **Каждый инструмент** — closure с `client.Call(ctx, endpoint, params)` к data-service

### Поток вызова инструмента

1. **Запрос**: Агент шлёт JSON-RPC `tools/call` через Streamable HTTP `/mcp` с `X-Tenant-ID`
2. **Манифест**: mcp-gateway проксирует `/mcp/manifest` → data-service (тем tenant'ом)
3. **Разрешение**: `Registry.buildTools()` — маппинг endpoint → MCP toolDef
4. **Вызов**: `makeHandler()` → `client.Call(ctx, endpoint, params)` → data-service → JSON → MCP-результат

## Схема именования инструментов (Фаза 2/2.5 — N filter_* + 5 db_*)

| Op | Имя | Пример |
|---|---|---|
| `/q/map` | `db_map` | `db_map` |
| `/q/describe` | `db_describe` | `db_describe` |
| `/q/search` | `db_search` | `db_search` |
| `/q/get` | `db_get` | `db_get` |
| `/q/related` | `db_related` | `db_related` |
| `filter` (strategy) | `filter_{entity}` | `filter_products` |
| `get_by_id`/`distinct`/`count` | `get_*`/`distinct_*`/`count_*` | только opt-in (`LLMToolPolicy`, default false) |
| `builtin_health` | `health` | `health` |
| `builtin_stats` | `stats` | `stats` |
| `custom_query` | `{query_id}` | `student_grades` |

> **Примечание:** `find` / `list` — REST-эндпоинты для data-service, но **не MCP-тулы**.
> Фильтрация — пер-энтити `filter_{entity}` (поля в схеме тула); текст/разведка/получение — консолидированные `db_*` через `/q/*` (см. `data-service/README.md`).

Санитизация: `deriveToolName()` удаляет `{` `}` из имён (Mistral reject).

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
| `/mcp` | GET/POST/DELETE | Standard Streamable HTTP MCP | MCP_API_KEY |
| `/mcp/manifest` | GET | Прокси манифеста → data-service | MCP_API_KEY |
| `/mcp/tools/mapping` | GET | JSON `{tool: display_name}` | MCP_API_KEY |
| `/mcp/schema` | GET | Прокси схемы → data-service | MCP_API_KEY |
| `/debug/config` | GET | Текущий конфиг | MCP_API_KEY |
| `/config` | GET | Алиас `/debug/config` | MCP_API_KEY |
| `/docs` | GET | Swagger UI | MCP_API_KEY |
| `/openapi.json` | GET | OpenAPI spec | MCP_API_KEY |

**Auth:** development может оставить `MCP_REQUIRE_AUTH=false`. Production обязан установить `MCP_REQUIRE_AUTH=true` и тот же сильный secret в gateway `MCP_API_KEY` и api-service `MCP_CLIENT_API_KEY`; `/health` и `/metrics` всегда открыты.

## 📚 Ссылки

- [AGENTS.md](../../AGENTS.md) — общая архитектура проекта, data flow, правила работы
- [doc/agents/mcp-session-lifecycle.md](../../doc/agents/mcp-session-lifecycle.md) — полный lifecycle MCP-сессии
- [doc/agents/search-strategies.md](../../doc/agents/search-strategies.md) — стратегии поиска (grep, filter, schema)
- [mcp-gateway/internal/tools/tools.go](internal/tools/tools.go) — реестр инструментов (source of truth)

## Переменные окружения

| Переменная | Дефолт | Описание |
|---|---|---|
| `MCP_PORT` | `8083` | Порт HTTP |
| `MCP_REQUIRE_AUTH` | `false` | `true` запрещает старт gateway с пустым `MCP_API_KEY`; production setting |
| `MCP_API_KEY` | — | Gateway Bearer-токен; должен совпадать с `MCP_CLIENT_API_KEY` api-service |
| `MCP_ALLOWED_ORIGINS` | — | Comma-separated exact browser Origin allow-list; absent Origin разрешён service clients |
| `MCP_MAX_TENANTS_PER_SCOPE` | `8` | Maximum unique tenant IDs в composite scope; duplicate/oversize получают `400` |
| `DATA_SERVICE_URL` | `http://127.0.0.1:8084` | Базовый URL data-service |
| `DATA_SERVICE_TIMEOUT` | `30` | Таймаут HTTP к data-service (сек) |
| `RAG_SERVICE_URL` | `http://127.0.0.1:8082` | Базовый URL RAG |
| `RAG_HTTP_TIMEOUT` | `30` | Таймаут HTTP к RAG (сек) |
| `BOOTSTRAP_TENANT_ID` | — | Tenant ID для первоначальной загрузки манифеста |
| `MCP_MAX_STREAMABLE_TENANT_SCOPES` | `256` | Max cached tenant-set handlers for stateful Streamable HTTP; new scope above limit receives `503` |
| `MCP_SESSION_IDLE_TIMEOUT` | `5m` | Idle TTL transport-managed Streamable HTTP sessions |
| `MCP_READ_HEADER_TIMEOUT` | `10` | Read header timeout (сек, slowloris защита) |
| `MCP_IDLE_TIMEOUT` | `120` | Idle timeout HTTP (сек) |
| `MCP_DEV` | — | Debug log level для gateway |
| `MCP_RATE_LIMIT_RPS` | `10` | Requests per second (rate limiter) |
| `MCP_RATE_LIMIT_BURST` | `20` | Burst size (rate limiter) |

## Управление сессиями

- **Streamable HTTP session TTL = 5m** — transport-managed сессия закрывается при простое.
- **MaxStreamableTenantScopes = 256** — ограничение cached tenant-set handlers; новый scope выше лимита получает `503 Service Unavailable`.
- В gateway больше нет самописных SSE session IDs, long-lived GET streams и session debug endpoint.

## Метрики (Prometheus)

| Метрика | Тип | Labels |
|---|---|---|
| `mcp_tool_calls_total` | Counter | `tool`, `tenant`, `status` |
| `mcp_sessions_active` | Gauge | `tenant_scope` |
| `mcp_rate_limit_hits_total` | Counter | `tenant` |

## Dev-режим

```bash
MCP_DEV=true DATA_SERVICE_URL=http://127.0.0.1:8084 go run ./cmd/
```

`MCP_DEV` включает debug log level; ручной SSE playground намеренно удалён вместе с устаревшим transport.

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
# Metadata endpoint for a registered tenant in a secure deployment.
curl -s -H "Authorization: Bearer $MCP_API_KEY" -H "X-Tenant-ID: default" \
  http://127.0.0.1:8083/mcp/manifest | jq '.tools | length'

# Gateway security regression: legacy-route absence, auth startup validation,
# Origin policy, header-only routing and bounded composite scopes.
go test ./cmd -v

# Live SDK v2 contract: tools, composite scopes, session isolation, query
# rejection, auth and Origin policy (security tests activate when env is set).
cd ../..
MCP_API_KEY="$MCP_API_KEY" MCP_ALLOWED_ORIGINS="$MCP_ALLOWED_ORIGINS" \
  uv run pytest services/agent-db/tests/e2e/test_mcp_streamable_http.py -v
```

## Troubleshooting

| Симптом | Причина | Фикс |
|---|---|---|
| `connection refused` :8083 | mcp-gateway не запущен | `go run ./cmd/` |
| `400 X-Tenant-ID header is required` | Client передал tenant в query либо вовсе его потерял | Передать resolved scope только через `X-Tenant-ID` |
| Пустой манифест (0 tools) | Tenant не зарегистрирован | register через data-service admin API |
| 401 Unauthorized | Missing/mismatched MCP Bearer token | Синхронизируй `MCP_API_KEY` gateway и `MCP_CLIENT_API_KEY` api-service |
| 403 Origin is not allowed | Browser Origin absent from `MCP_ALLOWED_ORIGINS` | Keep MCP internal or add only the explicit trusted HTTPS origin |
| 429 Too Many Requests | Исчерпан rate-limit burst для client IP | Ограничить повторные попытки или пересмотреть limits после capacity review |
| 503 too many active Streamable HTTP tenant scopes | Churn tenant sets заполнил bounded cache | Стабилизировать scopes или оценить безопасное увеличение `MCP_MAX_STREAMABLE_TENANT_SCOPES` |

---
**Last verified:** 2026-08-18 (working tree after `6cdb51f`) — `mcp-go v0.58`, единственный Streamable HTTP `/mcp`, required-production auth, Origin allow-list, header-only tenant scope, bounded composite scopes, lifecycle-backed session metrics, session isolation и native Python SDK v2 E2E сверены локально.

# mcp-gateway

Generic MCP (Model Context Protocol) сервер на Go. Заменил Python-сервер `mcp_server/` (удалён).

**Ключевая фича**: MCP-инструменты **авто-генерируются** из конфига data-service.
Не нужно писать ни строчки кода для новой БД — достаточно запустить `--discover`.

## 🏢 Multi-tenancy и Stateless архитектура

`mcp-gateway` реализован как **stateless динамический шлюз**. Он не хранит статический реестр инструментов при старте, а разрешает их на лету на основе идентификатора тенанта.

### Строгая мультитеннантность (Strict Mode)

Система работает в режиме **строгой изоляции**. Любой запрос к данным или конфигурации требует явного указания тенанта.

1. **X-Tenant-ID**: Основной ключ изоляции. Передается в заголовках каждого запроса. Без этого заголовка (или `?tenant_id=`) запросы возвращают `404 Not Found`.
2. **Bootstrappable Startup**: Поскольку `data-service` больше не имеет «дефолтного» тенанта, `mcp-gateway` использует переменную окружения `BOOTSTRAP_TENANT_ID` для первичной загрузки конфигурации при старте.
3. **Динамический манифест**: При каждом MCP-запросе (`/mcp/message`) шлюз запрашивает актуальный манифест конкретного тенанта у `data-service` через `/mcp/manifest`. Манифест генерируется на лету через `configgen.GenerateMCPTools()` с русскими conversational описаниями и санитизированными именами функций.
4. **Разрешение инструментов**: `toolsCallHandler` сопоставляет имя вызванного инструмента с путем к эндпоинту из полученного манифеста.
5. **Проброс (Propagation)**: Заголовок `X-Tenant-ID` пробрасывается сквозь шлюз в `data-service`, обеспечивая доступ к правильной БД тенанта.

## Composite Multi-Tenant Mode

`mcp-gateway` поддерживает **composite multi-tenant режим**: одна SSE сессия обслуживает несколько tenant'ов одновременно с префиксацией инструментов.

Режим включается автоматически, когда заголовок `X-Tenant-ID` содержит несколько tenant'ов через запятую:

```
X-Tenant-ID: tenant-a               → legacy: инструменты без префикса (как было)
X-Tenant-ID: tenant-a,tenant-b      → composite: инструменты с префиксом tenant-a__, tenant-b__
```

### Как это работает

#### 1. Парсинг tenant'ов

Функция `resolveTenantIDs()` в `cmd/main.go` парсит заголовок `X-Tenant-ID` как comma-separated список:

```go
// "tenant-a,tenant-b" → ["tenant-a", "tenant-b"]
func resolveTenantIDs(r *http.Request) []string {
    parts := strings.Split(r.Header.Get("X-Tenant-ID"), ",")
    ...
}
```

#### 2. Composite сервер

Функция `createCompositeServer()` в `cmd/main.go`:

- **1 tenant**: создаёт обычный MCPServer (без префикса, legacy path → backward compat)
- **N tenant'ов**: создаёт composite MCPServer, запрашивает конфиги всех tenant'ов у data-service и регистрирует все инструменты с префиксом `{tenantID}__`

```go
func createCompositeServer(tenantIDs []string) (*server.MCPServer, error) {
    if len(tenantIDs) == 1 {
        return createServerForTenant(tenantIDs[0]) // legacy
    }
    composite := server.NewMCPServer("helperium", "1.0.0")
    for _, tenantID := range tenantIDs {
        cfg := globalClient.FetchConfigWithTenant(tenantID)
        registry := tools.NewPrefixedRegistry(cfg, tenantID) // префикс
        registry.RegisterAll(composite)
    }
    return composite, nil
}
```

#### 3. DisplayName (публичное имя для UI)

Каждый инструмент может иметь `display_name` — человекочитаемое имя для отображения в UI.
Не влияет на MCP-протокол (LLM видит `name`). Настраивается вручную:

```json
{
  "mcp_tools": [
    {
      "name": "find_catalog_brand",
      "display_name": "Поиск брендов в каталоге",
      "endpoint": "/catalog_brand",
      "description": "Поиск брендов по названию",
      "params": [{"name": "search", "type": "string", "required": true}]
    }
  ]
}
```

**Как это работает:**
1. `GET /mcp/tools/mapping` возвращает `{"find_catalog_brand": "Поиск брендов в каталоге", ...}`
2. `api-service` MCPClient загружает маппинг при открытии SSE-сессии
3. SSE-события `tool_call` и `tool_result` содержат `display_name`
4. frontend (включая embed-виджет) использует `display_name` с контекстной иконкой (🔍 поиск, 📋 чтение, 📊 запрос, ⚡ остальное)

**Где настраивается:** admin dashboard → Тулы → колонка "Отображаемое имя" → 💾 Сохранить имена

Функция `NewPrefixedRegistry(cfg, tenantID)` в `internal/tools/tools.go`:

- Создаёт реестр как обычно, но каждый инструмент получает префикс `{tenantID}__`
- `RegisterAll()` регистрирует все инструменты на composite MCPServer с префиксом

```go
func (r *Registry) RegisterAll(mcpServer *server.MCPServer) {
    for _, td := range r.toolDefs {
        name := td.Name
        if r.tenantID != "" {
            name = r.tenantID + "__" + name  // ← префикс
        }
        registerOne(mcpServer, td, r.client, name, r.tenantID)
    }
}
```

#### 4. Routing handler

Функция `makeHandler()` получает tenantID **через замыкание (closure)**, а не из контекста запроса:

```go
func makeHandler(td toolDef, client *httpclient.Client, tenantID string) server.ToolHandlerFunc {
    return func(ctx context.Context, request mcp.CallToolRequest) (*mcp.CallToolResult, error) {
        actualTenantID := tenantID  // из closure — нельзя подменить через заголовок
        if actualTenantID == "" {
            actualTenantID = ctx.Value(httpclient.TenantIDKey).(string) // legacy
        }
        // → data-service с X-Tenant-ID: actualTenantID
    }
}
```

#### 5. Кэширование на SSE сессии

`ensureCompositeServer()` на сессии кэширует созданный composite сервер:

```go
func (s *sseSession) ensureCompositeServer(tenantIDs []string) (*server.MCPServer, error) {
    if s.mcpServer != nil && sliceEqual(s.tenantIDs, tenantIDs) {
        return s.mcpServer, nil  // reuse: те же tenant'ы
    }
    mcpServer, err := createCompositeServer(tenantIDs) // create
    s.mcpServer = mcpServer
    s.tenantIDs = tenantIDs
    return mcpServer, nil
}
```

### Пример data flow

```
User → "сравни успеваемость tenant-a и tenant-b"
  │
  ▼ api-service (tenant_ids = ["school-a", "school-b"])
  │
  ▼ mcp-client → sse_client(headers={"X-Tenant-ID": "school-a,school-b"})
  │
  ▼ mcp-gateway GET /mcp (X-Tenant-ID: school-a,school-b)
  │   └── session.tenantIDs = ["school-a", "school-b"]
  │
  ▼ POST /mcp/message (tools/list)
  │   └── ensureCompositeServer(["school-a","school-b"])
  │        └── createCompositeServer(...)
  │             ├── school-a__list_students
  │             ├── school-a__get_grades
  │             ├── school-b__list_students
  │             └── school-b__get_grades
  │
  ▼ Агент → tenant-a__list_students
  │   └── makeHandler(..., tenantID="school-a")  // closure
  │        └── data-service (X-Tenant-ID: school-a)
  │
  ▼ Агент → tenant-b__list_students
  │   └── makeHandler(..., tenantID="school-b")  // closure
  │        └── data-service (X-Tenant-ID: school-b)
```

### Security: изоляция между tenant'ами

Composite режим гарантирует **строгую изоляцию** данных между tenant'ами:

1. **TenantID зашит в closure хендлера**: Инструмент `tenant-a__list_students` всегда вызывает data-service с `X-Tenant-ID: tenant-a`. Даже если клиент изменит заголовок в POST-запросе — хендлер проигнорирует его.

2. **Инструменты недоступны без tenant'а**: Сессия открывается только с конкретным списком tenant'ов. Инструменты tenant-c недоступны, если tenant-c не был указан при открытии SSE.

3. **Per-tenant кэш**: Инструменты каждого tenant'а загружаются только из его манифеста — никакого пересечения.

4. **Обратная совместимость**:
   - `X-Tenant-ID: tenant-a` → legacy mode (без префикса, как работало всегда)
   - `X-Tenant-ID: tenant-a,tenant-b` → composite mode (с префиксом)
   - Старые клиенты и тесты (`e2e-mcp`, `e2e-data`) продолжают работать без изменений

### Переменные окружения

Composite режим не требует дополнительных переменных окружения. Единственный источник конфигурации — заголовок `X-Tenant-ID`.

### Тестирование

```bash
# Старый тест (per-tenant, backward compat)
uv run agent-db e2e-mcp

# Новый composite тест (multi-tenant, одна сессия)
uv run agent-db e2e-mcp-composite

# Полный набор
uv run agent-db e2e-full
```

## Генерация инструментов

Инструменты определяются в конфиге тенанта в `data-service`.

### 1. Auto-generated (рекомендуемый)

Конфиг **без** `mcp_tools[]`. Инструменты генерятся из `endpoints`:

```json
{
  "entities": [
    { "name": "products", "table": "products", "id_column": "id", "fields": [...] },
    { "name": "customers", "table": "customers", "id_column": "id", "fields": [...] }
  ],
  "endpoints": [
    { "method": "GET", "path": "/products/{id}", "op": "get_by_id", "entity": "products" },
    { "method": "GET", "path": "/products", "op": "find", "entity": "products",
      "search_field": "name", "query_param": "name" }
  ]
}
```

Автоматически получаете MCP-инструменты:
```
🔧 get_products(id)     → GET /products/{id}
🔧 find_products(name)  → GET /products?name=
```

### 2. Explicit override (для кастомных имён и параметров)

Если нужно другое имя или описание — добавьте `mcp_tools[]` в конфиг тенанта:

```json
{
  "mcp_tools": [
    {
      "name": "search_products_by_name",
      "endpoint": "/products",
      "description": "Поиск товаров по названию",
      "params": [{"name": "name", "type": "string", "required": true}]
    }
  ]
}
```

Explicit тулы перезаписывают auto-generated с тем же именем.

### Runtime-генерация инструментов (configgen.GenerateMCPTools)

MCP-инструменты генерируются на лету через `data-service/internal/configgen/configgen.go`:

- **Conversational описания** — русские фразы вроде `«Возвращает данные о студентах по уникальному идентификатору»`, понятные LLM.
- **Санитизация имён** — из custom_query path удаляются `{` и `}`, чтобы Mistral не отклонял function names.
- **Параметры-подсказки** — каждый параметр получает explicit описание (например `«Идентификатор записи в таблице»`).
- **Приоритет явных тулов** — если манифест содержит `mcp_tools[]`, они используются вместо auto-generated (без дубликатов).
- **Builtin-тулы всегда доступны** — `health` и `stats` присутствуют независимо от конфига.
- **Генерация на каждый запрос** — `/mcp/manifest` вызывает `configgen.GenerateMCPTools()` runtime, не зависит от файлового кэша.

## Схема именования инструментов

| Op в endpoint | Имя инструмента | Пример |
|---|---|---|
| `get_by_id` | `get_{entity}` | `get_student` |
| `find` | `find_{entity}` | `find_student` |
| `list` | `list_{entity}` | `list_students` |
| `builtin_health` | `health` | `health` |
| `builtin_stats` | `stats` | `stats` |
| `custom_query` | `{query_id}` | `student_grades` |

## Архитектура

```
mcp-gateway/
├── cmd/
│   ├── main.go                 # Точка входа: динамический toolsCallHandler, SSE + JSON-RPC
│   ├── mcp_debug.go            # //go:embed playground.html (MCP_DEV)
│   └── playground.html         # Веб-интерфейс для тестирования тулов
├── internal/
│   ├── httpclient/
│   │   └── client.go           # HTTP-клиент к data-service: FetchConfigWithTenant + Call
│   ├── ragclient/
│   │   └── client.go           # HTTP-клиент к RAG: SearchDocuments, ListDocuments, GetRagContext
│   └── tools/
│       └── tools.go            # Статические RAG-тулы
├── Dockerfile
├── go.mod / go.sum
└── README.md
```

## Поток вызова инструмента (Detailed)

1. **Запрос**: Агент шлёт JSON-RPC `tools/call` через SSE-сессию `/mcp` с заголовком `X-Tenant-ID: uni-tenant`.
2. **Манифест**: `mcp-gateway` делает `GET /mcp/manifest` $\rightarrow$ `data-service` с тем же заголовком.
3. **Разрешение**:
   - Ищет инструмент в `mcp_tools` из манифеста.
   - Если не найден $\rightarrow$ auto-gen из `endpoints` (по правилам именования `get_{entity}`, `find_{entity}`).
   - Если не найден $\rightarrow$ проверяет статические RAG-тулы.
4. **Вызов**:
   - Подставляет path-параметры (`{id}`) из аргументов.
   - Выполняет `GET` к `data-service` с заголовком `X-Tenant-ID`.
5. **Ответ**: JSON-ответ от `data-service` оборачивается в MCP-результат и возвращается агенту через SSE.

## Запуск

### 1. Запуск сервисов
```bash
# data-service
cd ../data-service
DS_CONFIG=/tmp/myapp-config.json go run ./cmd/server/

# mcp-gateway
cd ../mcp-gateway
DATA_SERVICE_URL=http://127.0.0.1:8084 go run ./cmd/
```

### 2. Регистрация тенантов (Обязательно)
Так как шлюз теперь stateless, тенанты должны быть зарегистрированы в `data-service` перед использованием:
```bash
# Используйте agent-db CLI для регистрации тенантов
uv run agent-db e2e --tenants default,shop    # materialize + register + проверить
uv run agent-db tenant register sqlite-testseed   # зарегистрировать отдельно
```

## Dev-режим

```bash
MCP_DEV=true DATA_SERVICE_URL=http://127.0.0.1:8084 go run ./cmd/
```

Доступно:
- `/debug` — MCP Playground: веб-интерфейс для тестирования всех инструментов (требует `X-Tenant-ID` в запросах)
- `/config` — совместимый алиас `/debug/config` для старых клиентов и закешированного UI
- `/debug/sessions` — активные SSE-сессии
- `/debug/config` — текущий конфиг тенанта (фетчится из `/mcp/manifest`)

### Metrics (v1.1.0)

Сервис отдаёт Prometheus-метрики на `/metrics` (без авторизации):

- `mcp_tool_calls_total` — счётчик вызовов MCP-тулов (labels: tool, tenant, status)
- `mcp_sessions_active` — количество активных SSE-сессий (label: tenant)
- `mcp_rate_limit_hits_total` — счётчик rate-limited запросов (label: tenant)

```bash
curl http://127.0.0.1:8083/metrics | grep mcp_
```

### Logging (v1.1.0)

- Используется `slog` (structured JSON-логи)
- `LOG_LEVEL` из окружения: debug, info, warn, error
- Формат: JSON (машиночитаемый, по умолчанию) или text

Пример лога:
```json
{"time":"...","level":"INFO","msg":"jsonrpc_call","method":"tools/list","session_id":"...","tenant_ids":["default"],"duration_ms":42}
```

## Эндпоинты

| Путь | Метод | Описание | Заголовок |
|---|---|---|---|
| `/mcp/tools/mapping` | GET | JSON: `{"tool_name": "display_name_or_name"}` | `X-Tenant-ID` |
| `/health` | GET | Статус сервиса | - |
| `/mcp` | GET | SSE endpoint (MCP streamable HTTP) | `X-Tenant-ID` |
| `/mcp/message` | POST | JSON-RPC сообщения; без `sessionId` возвращает прямой JSON-ответ, с `sessionId` пишет в SSE-сессию | `X-Tenant-ID` |
| `/debug` | GET | MCP Playground (dev) | `X-Tenant-ID` |

## Переменные окружения

| Переменная | Дефолт | Описание |
|---|---|---|
| `BOOTSTRAP_TENANT_ID` | — | ID тенанта для первичной загрузки конфига при старте (обязателен в strict mode) |
| `DATA_SERVICE_URL` | `http://127.0.0.1:8084` | Базовый URL data-service |
| `DATA_SERVICE_TIMEOUT` | `30` | Таймаут HTTP-запроса к data-service в секундах |
| `RAG_SERVICE_URL` | `http://127.0.0.1:8082` | Базовый URL RAG-сервиса |
| `RAG_HTTP_TIMEOUT` | `30` | Таймаут HTTP-запроса к RAG в секундах |
| `MCP_PORT` | `8083` | Порт HTTP |
| `MCP_DEV` | — | Включает debug endpoints + логирование |
| `MCP_MAX_SESSIONS` | 1000 | Максимальное количество одновременных SSE-подключений (защита от OOM) |
| `MCP_SESSION_IDLE_TIMEOUT` | 5m | Таймаут простоя после которого SSE-соединение закрывается (например: "5m", "30s") |
| `MCP_SESSION_MAX_LIFETIME` | 30m | Максимальное время жизни SSE-подключения независимо от активности (например: "30m", "1h") |
| `MCP_POST_HANDLER_TIMEOUT` | 25 | Таймаут для одного JSON-RPC запроса/ответа в POST /mcp/message (секунды) |
| `MCP_READ_HEADER_TIMEOUT` | 10 | Read header timeout для HTTP сервера (секунды) — защита от slowloris |
| `MCP_IDLE_TIMEOUT` | 120 | Idle timeout для HTTP сервера (секунды) — макс. время keep-alive соединений |
| `LOG_LEVEL` | `info` | Уровень логирования: debug, info, warn, error |

## RAG-инструменты (статическая регистрация)

Три RAG-тула доступны всем тенантам:

| Инструмент | RAG-эндпоинт | Описание |
|---|---|---|
| `search_documents` | `POST /search` | Семантический поиск по документам |
| `list_documents` | `POST /documents/list` | Список документов с фильтром по дисциплине |
| `get_rag_context` | `POST /context` | Готовый контекст для ответа LLM |

## ⚙️ Управление сессиями и защита от перегрузки

Для предотвращения исчерпания ресурсов при большом числе одновременных подключений реализованы механизмы ограничения и автоматической очистки SSE-сессий:

### Лимиты и таймауты
- **MaxSessions**: максимальное количество одновременных SSE-сессий (по умолчанию: 1000)
- **SessionIdleTimeout**: время простоя после которого сессия закрывается (по умолчанию: 5 минут)
- **SessionMaxLifetime**: максимальное время жизни сессии независимо от активности (по умолчанию: 30 минут)

### Как это работает
1. При установке SSE-соединения (`GET /mcp`) создается объект сессии с отметкой времени создания
2. При каждом сообщении в сессии (`POST /mcp/message`) обновляется timestamp последней активности
3. Фоновый процесс периодически проверяет все сессии и удаляет те, которые:
   - Превысили время максимальной жизни (SessionMaxLifetime)
   - Или простаивали дольше допустимого бездействия (SessionIdleTimeout)
4. Если количество активных сессий достигает лимита MaxSessions, новые подключения получают ответ `503 Service Unavailable`

### Защита от утечек ресурсов
- Раньше каждая SSE-сессия создавала свой MCP-сервер в памяти, что приводило к линейному росту потребления памяти с числом подключений
- Теперь сессии имеют жесткие временные границы и автоматически очищаются
- Даже при одновременном подключении 1000+ клиентов память остается предсказуемо ограниченной
- Администратор может отслеживать активные сессии через endpoint `/debug/sessions`

Эти механизмы делают mcp-gateway устойчивым к резким скачкам нагрузки и предотвращают исчерпание памяти в продакшн-среде.
---

## 🔧 Troubleshooting

| Симптом | Причина | Фикс |
|---|---|---|
| `MCP session not ready (timeout)` в e2e-mcp | data-service не запущен на 8084 | Запусти `go run ./data-service/cmd/server/ --config ./specs/config.example.json` |
| `connection refused` на 8083 | mcp-gateway не запущен | `go run ./mcp-gateway/cmd/` |
| Пустой `/mcp/manifest` (0 tools) | Тенант не зарегистрирован в data-service | `uv run agent-db tenant list` → `uv run agent-db register <id> <scenario>` |
| 401 / `admin_disabled` | `ADMIN_TOKEN` mismatch | `export ADMIN_TOKEN=secret` (должен совпадать с data-service) |
| 500 `invalid JSON-RPC` | Не тот формат запроса | POST на `/mcp/message` с `Content-Type: application/json`, тело: `{"jsonrpc":"2.0","method":"tools/call","params":{"name":"...","arguments":{}},"id":1}` |

### Быстрый smoke-тест
```bash
# 1. Запусти data-service (порт 8084) + mcp-gateway (порт 8083)
# 2. Проверь манифест
curl -s -H "X-Tenant-ID: default" http://127.0.0.1:8083/mcp/manifest | jq '.tools | length'
# Должен вернуть > 0

# 3. Прямой JSON-RPC вызов (без SSE)
curl -s -X POST http://127.0.0.1:8083/mcp/message \
  -H "X-Tenant-ID: default" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"list_student","arguments":{}},"id":1}' | jq .
```

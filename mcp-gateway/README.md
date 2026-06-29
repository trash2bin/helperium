# mcp-gateway

Generic MCP (Model Context Protocol) сервер на Go. Заменил Python-сервер `mcp_server/` (удалён).

**Ключевая фича**: MCP-инструменты **авто-генерируются** из конфига data-service.
Не нужно писать ни строчки кода для новой БД — достаточно запустить `--discover`.

## Как это работает

```
                     config.json
                          │
               ┌──────────┘
               ▼
     ┌──────────────────────┐
     │     data-service     │
     │     (Go, :8084)      │
     │                      │
     │  /mcp/manifest ──────┼──HTTP──► mcp-gateway (Go, :8083)
     │                      │                   │
     │  REST API ◀──────────┼──HTTP── mcp-gateway │
     └──────────────────────┘              │
                                       tools/list
                                            │
                                       ┌────▼────┐
                                       │  Агент  │
                                       │(LiteLLM)│
                                       └─────────┘
```

1. **config.json** — единый конфиг, описывающий БД, сущности, эндпоинты, MCP-инструменты
2. **data-service** строит REST API по `cfg.endpoints[]` и **публикует MCP-манифест** на `/mcp/manifest`
3. **mcp-gateway** при старте фетчит манифест через HTTP (а не парсит config.json напрямую)
4. data-service остаётся **единственным source of truth** для конфигурации
5. Каждый endpoint → MCP tool. Имя выводится из `op` + entity: `get_by_id/students → get_students`

## Два режима генерации инструментов

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

Если нужно другое имя или описание — добавьте `mcp_tools[]`:

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

Explicit тулы перезаписывают auto-generated с тем же именем. Всё остальное — из `endpoints`.

### Демо: БД маркетплейса за 2 минуты

Создали SQLite с таблицами `customers, products, orders, order_items, categories`,
запустили `--discover` — получили конфиг без единой ручной правки:

```bash
# data-service
DATA_SERVICE_URL=http://127.0.0.1:8084 DS_CONFIG=/tmp/marketplace.json go run ./cmd/server/

# mcp-gateway (конфиг получает из data-service по HTTP)
DATA_SERVICE_URL=http://127.0.0.1:8084 go run ./cmd/
```

→ **10 MCP-инструментов** авто-сгенерировано:
```
🔧 health             — проверка статуса
🔧 stats              — счётчики по всем таблицам
🔧 get_products(id)   — товар по ID
🔧 find_products(name) — ��оиск товаров
🔧 get_customers(id)  — клиент по ID
🔧 find_customers(name) — пои��к клиентов
🔧 get_categories(id) — категория по ID
🔧 find_categories(name) — поиск категорий
🔧 get_orders(id)     — заказ по ID
🔧 get_order_items(id) — позиция заказа по ID
```

**Ни одной строчки Go/Python кода. Только `--discover` + `config.json`.**

## Схема именования инструментов

| Op в endpoint | Имя инструмента | Пример |
|---|---|---|
| `get_by_id` | `get_{entity}` | `get_student` |
| `find` | `find_{entity}` | `find_student` |
| `list` | `list_{entity}` | `list_students` |
| `builtin_health` | `health` | `health` |
| `builtin_stats` | `stats` | `stats` |
| `custom_query` | `{query_id}` | `student_grades` |

Параметры выводятся из path-паттерна (`{id}`, `{name}`) и `search_field` для `find`/`list`.

## Архитектура

```
mcp-gateway/
├── cmd/
│   ├── main.go                 # точка входа, HTTP-роутер, SSE + JSON-RPC
│   ├── mcp_debug.go            # //go:embed playground.html (MCP_DEV)
│   └── playground.html         # веб-интерфейс для тестирования тулов
├── internal/
│   ├── httpclient/
│   │   └── client.go           # HTTP-клиент к data-service: FetchConfig + Call
│   ├── ragclient/
│   │   └── client.go           # HTTP-клиент к RAG: SearchDocuments, ListDocuments, GetRagContext
│   └── tools/
│       └── tools.go            # авто-генерация data-тулов + статические RAG-тулы
├── Dockerfile
├── go.mod / go.sum
└── README.md
```

## Поток вызова инструмента

1. Агент шлёт `POST /mcp/message` с JSON-RPC `tools/call`
2. mcp-gateway находит инструмент по име��и, получает endpoint URL
3. Path-параметры (`{id}`) подставляются через `url.PathEscape`, остальные — query
4. HTTP GET к data-service, JSON-ответ → `CallToolResult`

```
MCP вызывающий → POST /mcp/message { method: "tools/call", params: { name: "get_student", arguments: { id: "123" } } }
                      │
                      ▼
mcp-gateway: name="get_student" → endpoint="/students/{id}" → args={id:"123"}
                      │
                      ▼ resolvePathParams("/students/{id}", {id:"123"}) = "/students/123"
                      │
                      ▼ GET http://data-service:8084/students/123
                      │
                      ▼ { "course": 1, "full_name": "Иванов Иван", ... }
                      │
MCP ← CallToolResult { content: [{ type: "text", text: "{ ... }" }] }
```

## Запуск

```bash
# 1. Запустить data-service (публикует конфиг на /mcp/manifest)
cd ../data-service
DS_CONFIG=/tmp/myapp-config.json go run ./cmd/server/

# 2. Запустить mcp-gateway (фетчит манифест из data-service)
cd ../mcp-gateway
DATA_SERVICE_URL=http://127.0.0.1:8084 go run ./cmd/

# Сборка
go build -o mcp-gateway ./cmd/
```

## Dev-режим

```bash
MCP_DEV=true DATA_SERVICE_URL=http://127.0.0.1:8084 go run ./cmd/
```

Доступно:
- `/debug` — MCP Playground: веб-интерфейс для тестирования всех инструментов
- `/debug/sessions` — активные SSE-сессии
- `/debug/config` — загруженный конфиг (source: data-service /mcp/manifest)
- `/` → редирект на `/debug`
- Логирование всех HTTP-запросов и MCP-сообщений (уровень debug)

## Эндпоинты

| Путь | Метод | Описание |
|---|---|---|
| `/health` | GET | Статус сервиса |
| `/mcp` | GET | SSE endpoint (streamable HTTP) |
| `/mcp` | POST | JSON-RPC (fallback для Python SDK) |
| `/mcp/message` | POST | JSON-RPC сообщения |
| `/tools/list` | GET | Список инструментов (JSON-RPC) |
| `/tools/call` | POST | Вызов инструмента (JSON-RPC) |
| `/debug` | GET | MCP Playground (dev) |
| `/debug/sessions` | GET | Активные SSE сессии (dev) |
| `/debug/config` | GET | Текущий конфиг (dev) |

### SSE протокол

```
1. GET /mcp  →  event: endpoint, data: http://host/mcp/message?sessionId=<uuid>
2. POST /mcp/message?sessionId=<uuid>  →  JSON-RPC запрос
```

Совместим с `mcp.client.streamable_http.streamable_http_client` (Python SDK).

## Переменные окружения

| Переменная | Дефолт | Описание |
|---|---|---|
| `DATA_SERVICE_URL` | `http://127.0.0.1:8084` | Базовый URL data-service (откуда fetch-ится `/mcp/manifest`) |
| `DATA_SERVICE_TIMEOUT` | `30` | Таймаут HTTP-запроса в секундах |
| `RAG_SERVICE_URL` | `http://127.0.0.1:8082` | Базовый URL RAG-сервиса (для `search_documents` и др.) |
| `RAG_HTTP_TIMEOUT` | `30` | Таймаут HTTP-запроса к RAG в секундах |
| `MCP_PORT` | `8083` | Порт HTTP |
| `MCP_DEV` | — | Включает debug endpoints + логирование |

## Ограничения

- **No JOIN endpoints** — для объединения таблиц (`customer → orders`) нужны `custom_queries` в конфиге
- **Только SELECT** — data-service read-only по дизайну
- **Нет фильтрации** — `find` только по одному полю (`search_field`)
- **Одна БД на инстанс** — multi-tenancy в фазе 3.7

## RAG-инструменты (статическая регистрация)

Три RAG-тула регистрируются всегда, независимо от конфига data-service:

| Инструмент | RAG-эндпоинт | Описание |
|---|---|---|
| `search_documents` | `POST /search` | Семантический поиск по загруженным документам (лекции, методички) |
| `list_documents` | `POST /documents/list` | Список документов в базе знаний с фильтрацией по дисциплине |
| `get_rag_context` | `POST /context` | Готовый контекст из релевантных фрагментов для подстановки в ответ LLM |

Если RAG-сервис недоступен (проверяется через `GET /health` при регистрации),
вызов тула возвращает осмысленную ошибку вместо краша шлюза.

## Совместимость с Python mcp_server (legacy — удалён)

Python-сервер `mcp_server/` был заменён на `mcp-gateway` (Go) и полностью удалён.
Все инструменты (data-service + RAG) теперь обслуживаются Go-шлюзом.

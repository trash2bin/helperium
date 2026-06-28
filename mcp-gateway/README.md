# mcp-gateway

Generic MCP (Model Context Protocol) сервер на Go. Замена Python-сервера `mcp_server/`.

**Ключевая фича**: MCP-инструменты **авто-генерируются** из конфига data-service.
Не нужно писать ни строчки кода для новой БД — достаточно запустить `--discover`.

## Как это работает

```
                         config.json (один на оба сервиса)
                              │
               ┌──────────���───┴────────���─────┐
               ▼                              ▼
     ┌──────────���──────┐          ┌─────────────────────┐
     │   data-service   │          │    mcp-gateway      │
     │   (Go, :8084)    │◀──HTTP──│   (Go, :8083)       │
     └─────────────────┘          └─────────────────────┘
                                         │
                                    tools/list
                                         │
                                    ┌────▼────┐
                                    │  Агент  │
                                    │(LiteLLM)│
                                    └─────────┘
```

1. **config.json** — единый конфиг. Описывает: подключение к БД, сущности, эндпоинты, MCP-инструменты
2. **data-service** строит REST API по `cfg.endpoints[]`
3. **mcp-gateway** читает тот же конфиг и **авто-генерирует** MCP-инструменты из `cfg.endpoints[]`
4. Каждый endpoint → MCP tool. Имя выводится из `op` + entity: `get_by_id/students → get_students`

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
DS_CONFIG=/tmp/marketplace.json go run ./cmd/server/

# mcp-gateway (тот же конфиг, 0 mcp_tools в config)
DS_CONFIG=/tmp/marketplace.json go run ./cmd/
```

→ **10 MCP-инструментов** авто-сгенерировано:
```
🔧 health             — проверка статуса
🔧 stats              — счётчики по всем таблицам
🔧 get_products(id)   — товар по ID
🔧 find_products(name) — поиск товаров
🔧 get_customers(id)  — клиент по ID
🔧 find_customers(name) — поиск клиентов
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
│   ├── main.go                 # точка входа, HTTP-роу��ер, SSE + JSON-RPC
│   ├── mcp_debug.go            # //go:embed playground.html (MCP_DEV)
│   └── playground.html         # веб-интерфейс для тестирования тулов
├── internal/
│   ├── config/
│   │   └── config.go           # загрузчик полного config.json (entities, endpoints, mcp_tools, ...)
│   ├── httpclient/
│   │   └── client.go           # HTTP-клиент к data-service (path params + query params)
│   └── tools/
│       └── tools.go            # авто-генерация и регистрация MCP-инструментов
├── Dockerfile
├── go.mod / go.sum
└── README.md
```

## Поток вызова инструмента

1. Агент шлёт `POST /mcp/message` с JSON-RPC `tools/call`
2. mcp-gateway находит инструмент по имени, получает endpoint URL
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

## Dev-режим

```bash
MCP_DEV=true go run ./cmd/
```

Доступно:
- `/debug` — MCP Playground: веб-интерфейс для тестирования всех инструментов
- `/debug/sessions` — активные SSE-сессии
- `/debug/config` — загруженный конфиг
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
| `DS_CONFIG` | поиск кандидатов | Путь к config.json (тот же, что у data-service) |
| `MCP_PORT` | `8083` | Порт HTTP |
| `DATA_SERVICE_URL` | `http://127.0.0.1:8084` | Базовый URL data-service |
| `DATA_SERVICE_TIMEOUT` | `30` | Таймаут HTTP-запроса в секундах |
| `MCP_DEV` | — | Включает debug endpoints + логирование |

## Запуск

```bash
# Dev (из корня проекта)
cd mcp-gateway

# По умолчанию ищет specs/config.example.json
go run ./cmd/

# Явный путь к конфигу
go run ./cmd/ --config ../specs/config.example.json

# С кастомной БД (--discover → config → старт)
# 1. Сгенерировать конфиг
cd ../data-service
DB_PATH=/path/to/any.db go run ./cmd/server/ --discover > /tmp/myapp-config.json

# 2. Запустить data-service
DS_CONFIG=/tmp/myapp-config.json PORT=8084 go run ./cmd/server/ &

# 3. Запустить mcp-gateway
cd ../mcp-gateway
DS_CONFIG=/tmp/myapp-config.json go run ./cmd/

# Сборка
go build -o mcp-gateway ./cmd/
```

## Ограничения

- **No JOIN endpoints** — для объединения таблиц (`customer → orders`) нужны `custom_queries` в конфиге
- **No RAG** — RAG-инструменты остались в Python (`mcp_server/` под `legacy` профилем)
- **Только SELECT** — data-service read-only по дизайну
- **Нет фильтрации** — `find` только по одному полю (`search_field`)
- **Одна БД на инстанс** — multi-tenancy в фазе 3.7

## Совместимость с Python mcp_server (legacy)

Python сервер (`mcp_server/`) живёт под профилем `legacy` в docker-compose (порт 8085) для
RAG-инструментов. Все domain-specific инструменты (8 штук для university) переехали на Go
и доступны через mcp-gateway на порту 8083.

| | Python (legacy) | Go (mcp-gateway) |
|---|---|---|
| Инструменты | 8 хардкодных + 3 RAG | auto-generated из конфига |
| RAG | Да (HTTP к rag:8082) | Нет |
| Конфиг | env only | `config.json` (entities/endpoints/tools) |
| Generic | Нет (хардкод SQL) | Да (любая БД через конфиг) |

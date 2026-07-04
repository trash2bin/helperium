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

## Два режима генерации инструментов

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

## Эндпоинты

| Путь | Метод | Описание | Заголовок |
|---|---|---|---|
| `/health` | GET | Статус сервиса | - |
| `/mcp` | GET | SSE endpoint (MCP streamable HTTP) | `X-Tenant-ID` |
| `/mcp/message` | POST | JSON-RPC сообщения; без `sessionId` возвращает прямой JSON-ответ, с `sessionId` пишет в SSE-сессию | `X-Tenant-ID` |
| `/debug` | GET | MCP Playground (dev) | `X-Tenant-ID` |

## Переменные окружения

| Переменная | Дефолт | Описание |
|---|---|---|
| `BOOTSTRAP_TENANT_ID` | — | ID тенанта для первичной загрузки конфига при старте (обязателен в strict mode) |
| `DATA_SERVICE_URL` | `http://127.0.0.1:8084` | Базовый URL data-service |
| `DATA_SERVICE_TIMEOUT` | `30` | Таймаут HTTP-запроса в секундах |
| `RAG_SERVICE_URL` | `http://127.0.0.1:8082` | Базовый URL RAG-сервиса |
| `MCP_PORT` | `8083` | Порт HTTP |
| `MCP_DEV` | — | Включает debug endpoints + логирование |
| `MCP_MAX_SESSIONS` | 1000 | Максимальное количество одновременных SSE-подключений (защита от OOM) |
| `MCP_SESSION_IDLE_TIMEOUT` | 5m | Таймаут простоя после которого SSE-соединение закрывается (например: "5m", "30s") |
| `MCP_SESSION_MAX_LIFETIME` | 30m | Максимальное время жизни SSE-подключения независимо от активности (например: "30m", "1h") |

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

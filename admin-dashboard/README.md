# Admin Dashboard — управление платформой

**Порт:** `:8085`  
**Стек:** Go (chi) + Alpine.js (UI)  
**Назначение:** Веб-интерфейс для администрирования всех сервисов agent-tutor: управление тенантами, конфигами, MCP-инструментами, RAG-документами и AI-агентами.

---

## Роль в системе

`admin-dashboard` — единая точка входа для администратора платформы. Он не хранит состояние сам, а проксирует запросы к трём бэкенд-сервисам:

```
Admin Dashboard (:8085)
  ├─ /api/tenants/*             → data-service (:8084) — tenant CRUD, конфиги, интроспекция
  ├─ /api/tools/*               → data-service (:8084) — tool approval flow
  ├─ /api/rag/*                 → RAG service (:8082) — документы, импорт, удаление
  └─ /api/agents/*              → API service (:8081) — CRUD агентов
```

**Защита:** Все API-запросы (кроме `/api/health` и статики) требуют `Authorization: Bearer <ADMIN_TOKEN>`.

---

## UI — страницы

| Страница | Маршрут | Описание |
|----------|---------|----------|
| **📊 Дашборд** | `/` | Сводка: количество тенантов, статус data-service |
| **🏪 Тенанты** | Tenants sidebar | Список тенантов, создание нового (SQLite upload / PostgreSQL DSN), удаление |
| **⚙️ Конфиг** | Config sidebar | Просмотр/редактирование JSON-конфига тенанта, read-only toggle, интроспекция схемы, включение/выключение entity и endpoints |
| **🛠️ Тулы** | Tools sidebar | MCP-манифест тенанта, подтверждение write-тулов в read-only режиме |
| **📄 RAG** | RAG sidebar | Загрузка документов (drag-and-drop), список, удаление, статус RAG сервиса |
| **🤖 Агенты** | Agents sidebar | CRUD AI-агентов, привязка tenant'ов, чат с агентом |

---

## API эндпоинты

Все эндпоинты требуют `Authorization: Bearer <ADMIN_TOKEN>` (кроме `/api/health`).

### Health

```http
GET /api/health
→ {"status":"ok"}
```

### Dashboard

```http
GET /api/dashboard
→ {"tenants": [...], "tenant_count": 5, "data_service": "http://localhost:8084"}
```

### Tenant CRUD

```http
GET  /api/tenants                        # список тенантов
POST /api/tenants                        # создать тенант (JSON)
GET  /api/tenants/{id}                   # получить тенант
DELETE /api/tenants/{id}                 # удалить тенант
POST /api/tenants/upload-sqlite          # создать тенант из .db файла (multipart)
```

**POST /api/tenants** (JSON):
```json
{
  "tenant_id": "my-client",
  "driver": "postgres",
  "dsn": "postgres://user:pass@host:5432/dbname?sslmode=disable"
}
```

**POST /api/tenants/upload-sqlite** (multipart/form-data):
- `file` — .db/.sqlite файл
- `tenant_id` — ID нового тенанта
- При создании автоматически запускается интроспекция схемы

### Config

```http
GET  /api/tenants/{id}/config            # текущий конфиг (DSN скрыт)
PUT  /api/tenants/{id}/config            # обновить конфиг → hot-reload
POST /api/tenants/{id}/introspect        # пересканировать схему БД → новый конфиг
GET  /api/tenants/{id}/manifest          # MCP-манифест инструментов
```

### Tool Approval

```http
GET  /api/tools/pending                  # write-тулы, ожидающие подтверждения
POST /api/tools/{toolName}/approve       # подтвердить write-тул
```

### RAG

```http
GET  /api/rag/health                     # статус RAG сервиса
POST /api/rag/documents/list             # список документов
POST /api/rag/documents/import           # импорт по пути
POST /api/rag/documents/upload           # загрузка файла (multipart)
POST /api/rag/documents/delete           # удаление документа
```

### Agent CRUD

```http
GET  /api/agents                         # список агентов
POST /api/agents                         # создать агента
GET  /api/agents/{name}                  # получить агента
PUT  /api/agents/{name}                  # обновить агента
DELETE /api/agents/{name}                # удалить агента
```

**POST /api/agents:**
```json
{
  "name": "my-chat-agent",
  "description": "Агент для отдела продаж",
  "tenant_ids": ["shop", "default"]
}
```

---

## Архитектура

```
admin-dashboard/
├── cmd/server/main.go           — точка входа, чтение env/флагов
├── internal/server/
│   ├── server.go                — chi роутер, middleware, все хендлеры, proxy-helper'ы
│   ├── client.go                — HTTP-клиенты к data-service и RAG
│   └── static/
│       ├── index.html           — SPA на Alpine.js (встроен в бинар через embed)
│       ├── app.js               — логика UI (Alpine.js компоненты)
│       └── styles.css           — тёмная тема (GitHub-dark inspired)
├── Dockerfile                   — multistage: golang:1.24-alpine → scratch
├── go.mod / go.sum
└── README.md
```

Статика вкомпиливается в бинар через `//go:embed static/*` — сервис не требует внешних файлов при запуске.

---

## Запуск

```bash
# Сборка
cd admin-dashboard && go build -o bin/admin-dashboard ./cmd/server/

# Запуск (требует работающие data-service, rag, api)
ADMIN_TOKEN=secret ./bin/admin-dashboard \
  -data-service http://localhost:8084 \
  -rag-service http://localhost:8082 \
  -api-service http://localhost:8081

# Через dev.sh
./scripts/dev.sh start    # admin стартует автоматически
```

**Проверка:**
```bash
curl -s -H "Authorization: Bearer secret" http://localhost:8085/api/health
→ {"status":"ok"}
```

**Переменные окружения:**

| Переменная | Дефолт | Описание |
|---|---|---|
| `LISTEN_ADDR` | `:8085` | Адрес сервера |
| `DATA_SERVICE_URL` | `http://localhost:8084` | Data service URL |
| `RAG_SERVICE_URL` | `http://localhost:8082` | RAG service URL |
| `API_SERVICE_URL` | `http://localhost:8081` | API service URL |
| `ADMIN_TOKEN` | — | Bearer-токен для API (обязателен, без него API возвращает 500) |
| `DATA_DIR` | `/data` | Директория для загруженных SQLite-файлов тенантов |

---

## Безопасность

- Все API-эндпоинты (кроме `/api/health` и статики) защищены `ADMIN_TOKEN`
- Токен передаётся как `Bearer <token>` в заголовке `Authorization`
- Без токена сервис возвращает `500 ADMIN_TOKEN not configured`
- UI имеет форму логина — токен сохраняется в `localStorage` браузера
- CORS разрешён для всех origin (dev-mode)

---

## Docker

```yaml
# docker-compose.yml
admin-dashboard:
  image: agent-tutor-admin:latest
  build:
    context: ./admin-dashboard
    dockerfile: Dockerfile
  ports:
    - "127.0.0.1:8085:8085"
  environment:
    - DATA_SERVICE_URL=http://data-service:8084
    - RAG_SERVICE_URL=http://rag:8082
    - API_SERVICE_URL=http://api:8081
    - ADMIN_TOKEN=${ADMIN_TOKEN:-}
  volumes:
    - tenant_uploads:/data/tenant-dbs
```

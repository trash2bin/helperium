# Data Service — Config-Driven REST API для произвольных БД

**Назначение:** Универсальный HTTP-прокси к клиентским БД. Схема БД описывается JSON-конфигом → автогенерируются REST эндпоинты, MCP-тулы, OpenAPI. Никакого доменного кода.

**Цель:** Горизонтально масштабируемый stateless сервис — добавление инстансов за балансировщиком, каждый tenant изолирован (свои пул коннектов, роутер, конфиг).

---

## Архитектура (30 сек)

```
config.json → chi Router → Runtime Handlers (generic) → Query Builder → Prepared SQL → DB
                │
                ├─ /{entity}/{id}        → get_by_id
                ├─ /{entity}?search=...  → find
                ├─ /{entity}             → list
                ├─ /{entity}/{id}/...    → custom_query (whitelist SELECT)
                ├─ /health, /stats       → builtin
                ├─ /admin/*              → tenant CRUD, config hot-reload, discover
                └─ /mcp/manifest         → runtime MCP tools generation
```

**Структура пакетов (`internal/`):**

```
internal/
├── configgen/                  # --discover: интроспекция схемы → config.json
├── datasource/                 # Adapter interface (SQLite, PG) + registry
│   └── tests/                  # black-box adapter тесты
├── openapigen/                 # Runtime OpenAPI 3.1 генерация из конфига
├── runtime/                    # Query builder + handlers
│   ├── handlers/               # 6 хендлеров (get_by_id, find, list, custom_query, health, stats)
│   │   └── tests/              # black-box handler тесты
│   └── tests/                  # black-box query builder тесты
├── server/                     # HTTP server, middleware, TenantStore, admin API
│   └── tests/                  # black-box scenario/integration тесты
└── testdata/scenarios/         # Pre-built database files (data.db) для тестов
```

**Принцип:** white-box тесты (`package xxx`) остаются рядом с исходным кодом, black-box тесты (`package xxx_test`) вынесены в `tests/` внутри каждого пакета для чистоты иерархии.

---

## Multi-Tenancy (Strict Mode, фаза 3.7)

- **TenantStore** — мапа `tenant_id → TenantInstance{Config, Conn, Router, ConfigPath, ...}`
- **Strict**: запрос **обязателен** `X-Tenant-ID` или `?tenant=` → иначе `404 tenant_not_found`
- **Изоляция**: у каждого tenant свой пул коннектов, роутер, конфиг
- **Admin API** (`Authorization: Bearer $ADMIN_TOKEN`):
  - `POST /admin/tenants` — добавить tenant на лету
  - `GET /admin/tenants` — список + health
  - `PUT /admin/tenants/{id}/config` — обновить конфиг существующего tenant'а
  - `POST /admin/tenants/{id}/config/rewrite` — интроспекция БД → перезапись конфига
  - `POST /admin/tenants/{id}/reload` — hot reload без рестарта процесса
  - `DELETE /admin/tenants/{id}` — graceful drain (закрыть пул, удалить из мапы, стереть конфиг с диска)
- **Health**: single-tenant `{"status":"ok","db":"ok"}` | multi-tenant `{"status":"degraded","tenants":[...]}`

### 🗃️ Tenant Config Persistence

Все tenant'ы, добавленные через admin API, автоматически сохраняются на диск.
После перезапуска data-service читает конфиги из файловой системы и восстанавливает tenant'ов.

**Директория хранения:** `$TENANTS_DIR` (по умолчанию `.data/tenants/` относительно корня проекта).
Каждый tenant — отдельный JSON-файл:

```
.data/tenants/
├── default.json          # Bootstrap-tenant из --config
├── shop.json             # Добавлен через POST /admin/tenants
└── my-client.json        # Добавлен через UI admin-dashboard
```

**Жизненный цикл:**

```
POST /admin/tenants  ──→  AddTenant() + SaveTenantConfig(id, cfg)  ──→  .data/tenants/{id}.json
PUT .../config       ──→  update + reload  ──→  SaveTenantConfig(id, cfg)  ──→  перезаписан
POST .../rewrite     ──→  introspect → generate → save  ──→  SaveTenantConfig(id, cfg)  ──→  обновлён
DELETE /admin/tenants  ──→  RemoveTenant() + DeleteTenantConfig(id)  ──→  файл удалён

Startup               ──→  os.ReadDir(.data/tenants/) → config.Load() → AddTenant()  ──→  восстановлен
```

**Механизм:** три публичных метода `TenantStore` отвечают за запись/чтение/удаление:

| Метод | Назначение |
|---|---|
| `SaveTenantConfig(id, cfg) string` | Маршалит конфиг в JSON и пишет в `.data/tenants/{id}.json`. Возвращает полный путь. |
| `TenantConfigPath(id) string` | Возвращает ожидаемый путь для tenant'а: `.data/tenants/{id}.json`. Создаёт директорию если нужно. |
| `DeleteTenantConfig(id)` | Удаляет файл конфига с диска. Игнорирует `ENOENT` (уже удалён). |

Все admin-хендлеры (`adminAddTenantHandler`, `adminConfigUpdateHandler`, `adminRewriteHandler`) пишут ТОЛЬКО через `SaveTenantConfig()` — никаких inline `os.WriteFile`.

**Проверено:**
- Tenant переживает полный restart всех сервисов data-service + mcp-gateway + rag + api + web
- Все entities, endpoints, custom_queries восстанавливаются из JSON на диске
- Удаление tenant'а стирает конфиг с диска

---

## Конфиг (Source of Truth)

```json
{
  "version": 1,
  "data_source": { "driver": "sqlite|postgres", "dsn": "${DB_PATH:-file.db}", "pool_size": 10, "read_only": true },
  "entities": [{ "name": "student", "table": "students", "id_column": "id", "fields": [...] }],
  "endpoints": [{ "method": "GET", "path": "/students/{id}", "op": "get_by_id", "entity": "student" }],
  "custom_queries": { "student_grades": { "sql": "SELECT ... WHERE student_id = ?", "params": ["id"], "max_rows": 500 }},
  "stats": { "counters": [{ "name": "students", "entity": "student" }] }
}
```

**Генерация:** `--discover` / `GET /admin/discover?raw=true` / `POST /admin/config/rewrite` — интроспекция схемы → entities + endpoints + health/stats. **Не генерируются:** custom_queries, MCP tools, params.

---

## Быстрый старт

```bash
# Сборка
cd data-service && go build -o bin/data-service ./cmd/server/

# Dev SQLite (из корня проекта)
./bin/data-service --config ../specs/config.example.json

# Dev PostgreSQL
docker compose up -d db
./bin/data-service --config ../specs/config.postgres.json

# Smoke-test
curl -s http://127.0.0.1:8084/health                    # {"status":"ok"}
curl -s -H "X-Tenant-ID: default" http://127.0.0.1:8084/students
curl -s -H "Authorization: Bearer secret" http://127.0.0.1:8084/admin/tenants
```

**Env vars:** `DS_CONFIG`, `PORT` (8084), `LOG_LEVEL` (info/debug/warn/error), `ADMIN_TOKEN` (обязателен для admin), `CONFIG_SCHEMA`.

### Metrics (v1.1.0)

Сервис отдаёт Prometheus-метрики на `/metrics` (требует `?tenant=...` для tenant-контекста):

- `data_requests_total` — счётчик запросов (labels: entity, operation, status)
- `data_request_duration_ms` — гистограмма длительности запросов

```bash
curl "http://127.0.0.1:8084/metrics?tenant=default" | grep data_
```

---

## Сценарии (Test DB Factory)

`testdata/scenarios/<name>/` содержат pre-built `data.db` для Go-тестов и исходные `seed.json` (используется Python seedgen для пересоздания).

| Сценарий | Драйвер | Назначение |
|---|---|---|
| `sqlite-testseed` | SQLite | Базовый смоук (13 entities) |
| `postgres-testseed` | PG | Cross-driver parity (только seed.json, без data.db) |
| `big-testseed` | SQLite | Load test (500 students, 4000 grades) |
| `shop` | SQLite | Сторонняя БД (FK lookups) |

```bash
# Пересоздать data.db из seed.json (через Python seedgen в agent-db)
uv run --package agent-db python3 -c "from agent_db.seedgen import materialize; materialize('data-service/testdata/scenarios/sqlite-testseed', force=True)"

# Запуск сервера со сценарием
go run ./cmd/server/ --config testdata/scenarios/sqlite-testseed/config.json
```

---

## Тестирование

```bash
# Все go-тесты (416 шт, 11 пакетов)
go test ./... -count=1

# White-box тесты (рядом с кодом)
go test ./internal/server/ ./internal/runtime/... ./internal/datasource/ ./internal/configgen/... -count=1

# Black-box тесты (в tests/)
go test ./internal/server/tests/ ./internal/runtime/tests/ ./internal/runtime/handlers/tests/ ./internal/datasource/tests/ -count=1

# Race detector (флаки)
go test -race ./... -count=3
for i in {1..5}; do go test ./internal/server/tests/ -run TestConcurrency -race; done

# Cross-driver parity (PG)
docker compose up -d db
AGENT_TUTOR_TEST_PG=1 go test ./internal/server/tests/ -run TestCrossDriver -v

# Integration (faker + PG)
uv run python tests/integration/test_with_faker.py --students 50 --grades 200
```

**Test helpers:** `internal/server/tests/scenario_loader_test.go` — `loadScenario()`, `buildTestRouter()` для in-memory httptest на pre-built data.db.

**Seed generation** больше не часть data-service — вынесен в `agent-db/agent_db/seedgen/`. Для пересоздания БД из сценария используй Python seedgen: `uv run --package agent-db python3 -c "from agent_db.seedgen import materialize; materialize('agent-db/scenarios/shop', force=True)"`

---

## Security & Hardening

- Только SELECT, prepared statements (`?` / `$1`), `max_rows` обязателен для custom_query
- `read_only: true` по умолчанию, enforced
- Валидация через Go-типы (`helperium-go/config/types.go`), JSON Schema не используется
- Чужая БД — read-only, data-service не пишет

---

## Horizontal Scalability

- **Stateless**: никакого локального состояния сессии (кроме кэша tenant'ов в памяти процесса)
- **Tenant isolation**: каждый tenant = независимый `*sql.DB` + `chi.Router` → можно шардить по инстансам
- **Config hot-reload**: `POST /admin/config/reload` без рестарта
- **Shared-nothing**: добавление инстансов за LB требует только shared config store (file/DB) для admin API

---

## Troubleshooting (Top 5)

| Симптом | Причина | Фикс |
|---|---|---|
| `bind: address already in use` | Порт 8084 занят | `lsof -ti:8084 \| xargs kill -9` |
| `config: load "...config.example.json": no such file` | Не тот cwd | Запуск из корня: `go run ./data-service/cmd/server/ --config ./specs/config.example.json` |
| `ADMIN_TOKEN not configured` / 401 | Токен mismatch | `export ADMIN_TOKEN=secret` (совпадает с `agent-db`) |
| `Python seedgen failed: no entity` | config.json не содержит entities | Добавить `entities[]` в конфиг |
| PG `connection refused` | Colima PG упал | `docker ps \| grep postgres`, `pg_isready -h localhost -p 5432` |
| `LOG_LEVEL=debug` не даёт трассировки | LOG_LEVEL не включена | Добавить `LOG_LEVEL=debug` в env до запуска |

---

## Связь с остальными сервисами

| Сервис | Порт | Контракт (неизменяемый HTTP API) |
|---|---|---|
| **mcp-gateway** | 8083 | `GET /mcp/manifest` (с `X-Tenant-ID`) → runtime MCP tools |
| **demo-web** | 8080 | Reverse proxy `/api/data/*` → `GET /{entity}` (через `X-Tenant-ID`) |
| **demo-api** | 8081 | Через mcp-gateway вызывает тулы data-service |
| **admin-dashboard** | 8085 | `/admin/*` (tenant CRUD, config, tools approval) |

**Agent-db CLI** — единая точка управления: `scenario list`, `materialize`, `register`, `e2e-*`.

**Все эндпоинты сохранены:** `/health`, `/docs`, `/openapi.json`, `/{entity}`, `/{entity}/{id}`, `/{entity}?search=...`, `/{entity}/{id}/custom`, `/mcp/manifest`, `/admin/*`.

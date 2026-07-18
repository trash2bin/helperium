# Admin Dashboard — управление платформой

**Порт:** `:8085`
**Стек:** Go (chi) + Alpine.js (UI)
**Назначение:** Веб-интерфейс для администрирования всех сервисов helperium: управление тенантами, конфигами, MCP-инструментами, RAG-документами и AI-агентами.

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

**Защита:** Все API-запросы (кроме `/api/health` и статики) требуют `Authorization: Bearer <token>`.
Два уровня доступа:
- **admin** (`ADMIN_TOKEN`) — полный CRUD
- **viewer** (`VIEWER_TOKEN`) — только GET на `/api/*` (read-only). POST/PUT/DELETE → 403.

Роль определяется автоматически по токену и возвращается в `/api/dashboard`.

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
| **🛡️ Anti-Abuse** | Anti-Abuse sidebar | Настройка anti-abuse engine (глобально + per-agent), Emergency Presets (Normal/Cautious/Lockdown) |
| **🤖 LLM Fallback** | LLM Fallback sidebar | Статус провайдеров LLM: активная модель, failover цепочка (primary → fallback → tertiary) |
| **🎤 Voice** | Voice sidebar | STT/TTS провайдеры, настройки голоса |
| **📋 Аудит** | Audit sidebar | История изменений конфигурации |
| **🌐 Language** | В хедере | Переключатель языка RU/EN (i18n, 309 ключей) |

---

## API эндпоинты

> ⚠️ Эта секция **автогенерируется** из chi-роутов `server.go` командой:
> `./scripts/check-admin-contract.sh --update-readme`.
> **Не редактируй вручную** — обновится при следующем запуске скрипта.

Все эндпоинты под `/api`, защищены `Authorization: Bearer <ADMIN_TOKEN>`
(кроме `/api/health`).

| Method | Path |
|---|---|
| POST | `/api/abuse-preset/{preset}` |
| GET | `/api/abuse-settings` |
| PUT | `/api/abuse-settings` |
| GET | `/api/agents` |
| POST | `/api/agents` |
| DELETE | `/api/agents/{name}` |
| GET | `/api/agents/{name}` |
| PUT | `/api/agents/{name}` |
| GET | `/api/agents/{name}/abuse` |
| PUT | `/api/agents/{name}/abuse` |
| POST | `/api/chat/voice` |
| GET | `/api/dashboard` |
| POST | `/api/db/test` |
| GET | `/api/emergency-status` |
| GET | `/api/health` |
| GET | `/api/llm-config` |
| GET | `/api/llm-provider-list` |
| GET | `/api/llm-providers` |
| POST | `/api/llm-providers` |
| DELETE | `/api/llm-providers/{name}` |
| GET | `/api/llm-providers/{name}` |
| PUT | `/api/llm-providers/{name}` |
| POST | `/api/llm-providers/{name}/toggle` |
| GET | `/api/rag/config` |
| PUT | `/api/rag/config` |
| POST | `/api/rag/documents/delete` |
| POST | `/api/rag/documents/import` |
| POST | `/api/rag/documents/list` |
| POST | `/api/rag/documents/upload` |
| GET | `/api/rag/health` |
| GET | `/api/rag/stats` |
| GET | `/api/tenants` |
| POST | `/api/tenants` |
| POST | `/api/tenants/upload-sqlite` |
| DELETE | `/api/tenants/{id}` |
| GET | `/api/tenants/{id}` |
| GET | `/api/tenants/{id}/config` |
| PUT | `/api/tenants/{id}/config` |
| POST | `/api/tenants/{id}/introspect` |
| GET | `/api/tenants/{id}/manifest` |
| GET | `/api/tenants/{id}/tools/pending` |
| POST | `/api/tenants/{id}/tools/{toolName}/approve` |
| GET | `/api/voice-config` |
| PUT | `/api/voice-config` |

**Примечания:**
- `/api/health` — без авторизации.
- `/api/db/test` — тестовый эндпоинт (в production может быть отключён).
- `/api/chat/voice` — голосовой ввод (voice mic), проксируется в api-service.
- Prometheus метрики отдаются на `/metrics` (не под `/api`, отдельный chi-хендлер).

---

## Архитектура

```
admin-dashboard/
├── cmd/server/main.go           — точка входа, чтение env/флагов
├── internal/server/
│   ├── server.go                — chi роутер, middleware, все хендлеры, proxy-helper'ы
│   ├── client.go                — HTTP-клиенты к data-service и RAG
│   └── static/
│       ├── index.html           — SPA на Alpine.js (собирается из partials/ билдом)
│       ├── dist/app.js          — esbuild-бандл (TypeScript → IIFE)
│       ├── styles.css           — тёмная тема + toast/debug-panel стили
│       └── i18n.json            — переводы RU/EN (309 ключей)
├── partials/                    — HTML-компоненты (собираются в index.html)
│   ├── head.html                — doctype, meta, стили
│   ├── login.html               — логин-оверлей
│   ├── app-open.html            — сайдбар + открытие <main>
│   ├── pages/                   — 10 страниц-компонентов
│   │   ├── dashboard.html
│   │   ├── tenants.html
│   │   ├── config.html
│   │   ├── tools.html
│   │   ├── rag.html
│   │   ├── agents.html
│   │   ├── abuse.html
│   │   ├── voice.html
│   │   ├── llm.html
│   │   └── audit.html
│   ├── app-close.html           — закрытие </main> + </div.app>
│   ├── modals.html              — модальные окна (Create/Edit Agent)
│   └── tail.html                — toast-container, debug-panel, <script>
├── src/                         — TypeScript-исходники
│   ├── index.ts                 — точка входа, Alpine.start()
│   ├── types.ts                 — типы
│   ├── i18n.ts                  — i18n-хелпер
│   ├── core/                    — ядро приложения
│   │   ├── registry.ts          — AppRegistry: регистрация доменных модулей
│   │   ├── apiClient.ts         — обёртка fetch → Alpine.store('api')
│   │   ├── store.ts             — Alpine.store() — глобальное состояние
│   │   ├── eventBus.ts          — pub/sub между модулями
│   │   ├── notify.ts            — toast-уведомления (ok/err)
│   │   └── apiLogger.ts         — логирование API-вызовов + debug-панель
│   └── domains/                 — доменные модули
│       ├── auth.ts              — авторизация, токен
│       ├── tenants.ts           — CRUD tenant'ов
│       ├── config.ts            — конфиги tenant'ов
│       ├── tools.ts             — MCP-инструменты, approval
│       ├── rag.ts               — RAG-документы
│       ├── agents.ts            — CRUD агентов
│       ├── abuse.ts             — anti-abuse настройки
│       ├── emergency.ts         — Big Red Button (Lockdown)
│       ├── llm.ts               — LLM-провайдеры, модели
│       ├── voice.ts             — STT/TTS провайдеры
│       └── audit.ts             — история изменений конфигурации
├── build.sh                     — сборочный скрипт:
│                                  1) tsc --noEmit (typecheck)
│                                  2) esbuild src/index.ts → dist/app.js
│                                  3) cat partials/* → index.html
├── package.json                 — npm-зависимости (esbuild, typescript)
├── tsconfig.json                — настройки TypeScript
├── tests/
│   ├── api.test.js              — unit-тесты api() функции (mock)
│   ├── contract.test.js         — contract scan по domain-модулям
│   └── contracts/
│       ├── api-endpoints.json   — api-service endpoints
│       ├── rag-endpoints.json   — rag-service endpoints
│       └── admin-endpoints.json — Go proxy endpoints
├── Dockerfile                   — multistage: golang:1.24-alpine → scratch
├── go.mod / go.sum
└── README.md
```

**Сборка:** `build.sh` собирает HTML из partials/ (конкатенация cat) и JS из TypeScript (esbuild).
Статика вкомпиливается в бинар через `//go:embed static` — сервис не требует внешних файлов при запуске.

**Auth bypass:** `server.go` пропускает авторизацию для `/`, `/static/`, `/js/`, `/dist/` и файловых путей (стили, метрики).

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
| `ADMIN_TOKEN` | — | Bearer-токен для API (admin, полный доступ). Без него API возвращает 500 |
| `VIEWER_TOKEN` | — | Bearer-токен для API (viewer, только чтение). Опционально. |
| `DATA_DIR` | `/data` | Директория для загруженных SQLite-файлов тенантов |
| `LOG_LEVEL` | `info` | Уровень логирования: debug, info, warn, error |
| `LOG_FORMAT` | `json` | Формат: json (slog) или text |

---

## Безопасность

### Аутентификация (RBAC)

Два уровня доступа:

| Роль | Токен | Права |
|---|---|---|
| **admin** | `ADMIN_TOKEN` | Полный CRUD: все методы, все эндпоинты |
| **viewer** | `VIEWER_TOKEN` | Только GET/OPTIONS на `/api/*`. POST/PUT/DELETE → 403 Forbidden |

- Токен передаётся как `Bearer <token>` в заголовке `Authorization`
- Если не задан ни один токен — `/api/*` возвращают 500
- Если токен не совпал — 401
- Роль возвращается в `/api/dashboard` как `"role": "admin"|"viewer"`
- UI: фронтенд фетчит роль при логине, скрывает write-кнопки для viewer

### Публичные пути (без auth)

- `/health`, `/api/health`
- `/`, `/index.html`, `/styles.css`, `/i18n.json`, `/i18n.js`
- `/static/*`, `/js/*`, `/dist/*`
- `/metrics`

### CORS

- По умолчанию: `http://localhost:8080`
- Для embed/production: `CORS_ALLOW_ORIGINS=*`
- Разрешённые заголовки: `Content-Type, Authorization, X-Tenant-ID, X-Correlation-ID`

---

## i18n (v1.1.0)

- Bilingual: русский / английский (309 ключей)
- Файл: `static/i18n.json`
- Лоадер: `static/i18n.js` (синхронный XHR, загружается до Alpine.js)
- Использование: `__('key')` в HTML, `$store.i18n.t('key')` в Alpine.js
- Переключатель: в хедере UI, сохраняется в localStorage

---

## Emergency Presets (v1.1.0)

Три профиля безопасности для быстрого реагирования на DDoS / аномальную нагрузку:

| Preset | RPS | Burst | Session Budget | Интервал | Длина сообщения |
|---|---|---|---|---|---|
| **Normal** | 1.0 | 5 | 50 | 1s | 2000 chars |
| **Cautious** | 0.5 | 3 | 25 | 2s | 1000 chars |
| **Lockdown** | 0.1 | 1 | 10 | 5s | 500 chars |

Big Red Button на странице Anti-Abuse: Normal → Cautious → Lockdown.

---

## 🔗 OpenAPI-контракты с api-service

Admin-dashboard проксирует Agent CRUD в api-service. Формат данных должен
совпадать с тем, что ожидает api-service.

### Где лежит контракт

```
specs/api.openapi.yaml          # OpenAPI 3.1 спецификация api-service
```

Спецификация **автоматически генерируется FastAPI** из Pydantic-моделей
и декораторов `@app.get/post`. При изменении эндпоинтов или моделей в
api-service спека обновляется:

```bash
# Вручную (если тест упал):
curl -s http://127.0.0.1:8081/openapi.json | python3 -c "import sys,yaml,json; yaml.dump(json.load(sys.stdin), sys.stdout)" > specs/api.openapi.yaml
```

### Типы TypeScript из OpenAPI

В `admin-dashboard/internal/server/static/api-types/` лежат сгенерированные
TS-типы для api-service (`api-service.d.ts`, ~1733 строки).

Обновление:
```bash
npx openapi-typescript specs/api.openapi.yaml -o admin-dashboard/internal/server/static/api-types/api-service.d.ts
```

Хотя фронт написан на Alpine.js (не TS), типы полезны в JSDoc-аннотациях
для IDE-подсказок и для проверки при Code Review.

### Правило: контракт прежде всего

При изменении любого эндпоинта api-service, которых касается admin-dashboard:

1. Обнови OpenAPI spec
2. Сгенерируй TS-типы
3. Проверь что `admin-dashboard/tests/api.test.js` проходит
4. Запусти `make ci` (или хотя бы JS-тесты)

Без этого — баги вроде `"The string did not match the expected pattern"`
или `"JSON.parse: unexpected end of data"` гарантированы.

---

## 🧪 Тестирование

### JS-тесты (Vitest)

```bash
cd admin-dashboard/tests
npm test              # однократный прогон
npm run test:watch    # watch mode
npm run test:coverage # с coverage
```

Что тестируется:
- `api()` — парсинг ответов сервера (200, 204, 422, 401, network errors)
- Обработка Pydantic validation errors (человеческий текст, а не "Unprocessable Entity")
- **Contract scan** — все domain-модули (`src/domains/*.ts`) сверяются с 3 контрактными JSON (`tests/contracts/`). Поддерживаются паттерны: `api()`, `Alpine.store('api')`, `fetch()`.

### Сборка

```bash
cd admin-dashboard && bash build.sh
# 1. tsc --noEmit (typecheck)
# 2. esbuild src/index.ts → static/dist/app.js
# 3. cat partials/* → static/index.html
```

### E2E (Playwright)

Сценарии в `.pi/skills/browser-e2e-test/SKILL.md`:
- Tenant CRUD
- Agent CRUD
- Write-tool approval

---

## Docker

```yaml
# docker-compose.yml
admin-dashboard:
  image: helperium-admin:latest
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
    - LOG_LEVEL=${LOG_LEVEL:-info}
    - LOG_FORMAT=${LOG_FORMAT:-json}
  volumes:
    - tenant_uploads:/data/tenant-dbs
```

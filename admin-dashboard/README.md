# Admin Dashboard — управление платформой

**Порт:** `:8085`
**Стек:** Go (chi) + Alpine.js (UI) + TypeScript
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
| **⚙️ Конфиг** | Config sidebar | Просмотр/редактирование JSON-конфига тенанта, read-only toggle, интроспекция схемы |
| **🛠️ Тулы** | Tools sidebar | MCP-манифест тенанта, подтверждение write-тулов в read-only режиме |
| **📄 RAG** | RAG sidebar | Загрузка документов (drag-and-drop), список, удаление, статус RAG сервиса |
| **🤖 Агенты** | Agents sidebar | CRUD AI-агентов, привязка tenant'ов |
| **🛡️ Anti-Abuse** | Anti-Abuse sidebar | Настройка anti-abuse engine + Emergency Presets (Normal/Cautious/Lockdown) |
| **🤖 LLM Fallback** | LLM Fallback sidebar | Статус провайдеров LLM, failover цепочка |
| **🎤 Voice** | Voice sidebar | STT провайдеры, настройки голоса |
| **📋 Аудит** | Audit sidebar | История изменений конфигурации |

---

## Архитектура

```
admin-dashboard/
├── cmd/server/main.go           — точка входа, чтение env/флагов
├── internal/server/
│   ├── server.go                — chi роутер, middleware, хендлеры, proxy
│   ├── client.go                — HTTP-клиенты к data-service и RAG
│   └── static/
│       ├── index.html           — SPA (сборка из partials/)
│       ├── dist/app.js          — esbuild-бандл (TypeScript → IIFE)
│       ├── styles.css           — общие стили
│       ├── admin.css            — админ-специфичные стили
│       └── i18n.json            — переводы RU/EN (309 ключей)
├── partials/                    — HTML-компоненты (16 файлов)
│   ├── head.html                — doctype, meta, <link>
│   ├── login.html               — логин-оверлей
│   ├── app-open.html            — сайдбар + открытие <main>
│   ├── pages/                   — 10 страниц (самодостаточные блоки)
│   ├── app-close.html           — закрытие </main> + </div.app>
│   ├── modals.html              — модальные окна
│   └── tail.html                — <script> + </body></html>
├── src/                         — TypeScript (17 файлов)
│   ├── index.ts                 — точка входа, Alpine.start()
│   ├── types.ts                 — типы
│   ├── i18n.ts                  — i18n-хелпер
│   ├── globals.d.ts             — глобальные типы Alpine
│   ├── core/                    — apiClient, auth, store, eventBus, notify, apiLogger
│   └── domains/                 — 11 доменных модулей
├── build.sh                     — сборка (см. ниже)
├── tests/                       — Vitest (58 тестов: api + contract scan)
├── go.mod / Dockerfile
└── README.md
```

### Сборка (build.sh)

```bash
cd admin-dashboard && bash build.sh
# 1. tsc --noEmit          — typecheck
# 2. cat partials/* > static/index.html — HTML сборка
# 3. npx html-validate ... — HTML линтинг (close-order, no-raw-characters)
# 4. esbuild src/index.ts → static/dist/app.js
```

Lint срабатывает на собранном HTML (partials — фрагменты). `close-order` ловит ту же ошибку, что была — страницы, оказавшиеся вне `.app`.

---

## Публичные пути (без auth)

- `/health`, `/api/health`
- `/`, `/index.html`, `/styles.css`, `/admin.css`, `/i18n.json`
- `/static/*`, `/js/*`, `/dist/*`
- `/metrics`

---

## Тестирование

```bash
cd admin-dashboard/tests && npm test   # 58 тестов (~300ms)
cd admin-dashboard && bash build.sh     # 0 errors expected
```

---

## i18n

- Bilingual: русский / английский (309 ключей)
- Файл: `static/i18n.json`
- Лоадер: вкомпилирован в TypeScript-бандл (`src/i18n.ts`)
- Использование: `__('key')` в HTML, `$store.i18n.t('key')` в Alpine

---

## Emergency Presets

| Preset | RPS | Burst | Session Budget | Интервал | Длина |
|---|---|---|---|---|---|
| **Normal** | 1.0 | 5 | 50 | 1s | 2000 chars |
| **Cautious** | 0.5 | 3 | 25 | 2s | 1000 chars |
| **Lockdown** | 0.1 | 1 | 10 | 5s | 500 chars |

---

## Docker

```yaml
admin-dashboard:
  build: ./admin-dashboard
  ports: ["127.0.0.1:8085:8085"]
  environment:
    - DATA_SERVICE_URL=http://data-service:8084
    - RAG_SERVICE_URL=http://rag:8082
    - API_SERVICE_URL=http://api:8081
    - ADMIN_TOKEN=${ADMIN_TOKEN}
  volumes: [tenant_uploads:/data/tenant-dbs]
```

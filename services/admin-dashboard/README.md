# Admin Dashboard — управление платформой

**Порт:** `:8085`
**Стек:** Go (chi) + Alpine.js (UI) + TypeScript
**Назначение:** Веб-интерфейс для администрирования всех сервисов helperium: управление тенантами, конфигами, MCP-инструментами, RAG-документами и AI-агентами.

---

## Роль в системе

`admin-dashboard` — единая точка входа для администратора платформы. Он проксирует tenant/RAG/agent operations к backend-сервисам и владеет persisted global anti-abuse policy, которую синхронно применяет в `api-service`:

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

> **Обязательная конфигурация:** `ADMIN_TOKEN` и `VIEWER_TOKEN` должны быть заданы и различаться. Если значения совпадают, middleware выбирает admin-ветку первым, и bearer token viewer фактически получает полный доступ.

`ADMIN_TOKEN`/`VIEWER_TOKEN` аутентифицируют browser-to-dashboard access и используются для data-service admin API. Private management routes на api-service получают отдельный internal `API_BEARER_TOKEN`; dashboard не пересылает browser bearer в api-service. В production эти credentials должны быть различны. При отсутствии `API_BEARER_TOKEN` dashboard fail-closed возвращает `503 api_auth_unconfigured` для private api-service proxy routes.

Роль определяется автоматически по токену и возвращается в `/api/dashboard`.

---

## UI — страницы

| Страница | Маршрут | Описание |
|----------|---------|----------|
| **📊 Дашборд** | `/` | Сводка: количество тенантов, статус data-service |
| **🏪 Тенанты** | Tenants sidebar | Список тенантов, создание нового (SQLite upload / PostgreSQL DSN), удаление |
| **⚙️ Конфиг** | Config sidebar | Просмотр/редактирование JSON-конфига тенанта, read-only toggle, интроспекция схемы |
| **🛠️ Тулы** | Tools sidebar | MCP-манифест тенанта, display names тулов |
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
│   ├── static/
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
├── tests/                       — Vitest (API, contract, i18n and types)
├── go.mod / Dockerfile
└── README.md
```

### Сборка (build.sh)

```bash
cd admin-dashboard && bash build.sh
# 1. tsc --noEmit          — typecheck
# 2. cat partials/* > static/index.html — HTML сборка
# 3. npx html-validate ... — HTML линтинг (close-order, no-raw-characters)
# 4. Generate admin OpenAPI → static/openapi.json
# 5. esbuild src/index.ts → static/dist/app.js
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
cd admin-dashboard/tests && npm test   # Vitest: API, contract, i18n and types
cd admin-dashboard && bash build.sh     # 0 errors expected
```

---

## OpenAPI-контракты и прокси (2026-08-03)

Спека `internal/openapi/spec.go` — ручной хардкод (не генерация из роутера), синхронизация защищена двумя контрактными тестами:

- **`internal/server/router_contract_test.go` (Gap A)** — reciprocal: каждый маршрут `Router()` (chi.Walk) должен быть в `GenerateSpec()`, и наоборот. Падает при добавлении/удалении маршрута в `server.go` без правки `spec.go`.
- **`internal/openapi/proxy_contract_test.go` (Gap B)** — каждый прокси-эндпоинт (`withProxyTarget` в `spec.go` ставит `x-upstream-method`/`x-upstream-path`/`x-upstream-headers`) сверяется с реальной спекой upstream: data-service через импорт `helperium-go/openapigen`, api/rag через `specs/*.yaml`. Падает, если прокси шлёт на путь, которого нет у upstream.

Прокси-пути заполнены по реальному коду хендлеров (`server.go`/`client.go`), не по названию. Пример найденного и исправленного бага: `tenantDeleteHandler` слал `POST /admin/tenants/{id}/delete` (404 на data-service) → теперь `DELETE /admin/tenants/{id}` (200).

`openapigen` переехал из `data-service/internal` в `helperium-go/openapigen` — общий пакет, прямой импорт в тестах без internal-ограничений.

---

## i18n

- Bilingual: русский / английский (309 ключей)
- Файл: `static/i18n.json`
- Лоадер: вкомпилирован в TypeScript-бандл (`src/i18n.ts`)
- Использование: `__('key')` в HTML, `$store.i18n.t('key')` в Alpine

---

## Emergency Presets

| Preset | RPS | Burst | User-turn quota | Интервал | Длина |
|---|---:|---:|---:|---:|---:|
| **Normal** | 1.0 | 5 | 50 | 1s | 2000 chars |
| **Cautious** | 0.5 | 3 | 30 | 2s | 1000 chars |
| **Lockdown** | 0.2 | 1 | 10 | 5s | 500 chars |

`max_user_turns_per_session` / `ABUSE_MAX_USER_TURNS` ограничивает число принятых user turns; assistant и tool messages quota не расходуют. `max_messages_per_session` не имеет compatibility alias: старый JSON key отклоняется, чтобы policy нельзя было silently weaken. Presets управляют только реально enforced request/loop controls и не обещают token quota или LLM fallback.

## Anti-abuse apply contract

Глобальный `PUT /api/abuse-settings` сначала persist-ит candidate policy, затем **синхронно** вызывает `api-service:POST /admin/abuse-config/reload`. Ответ `200` означает acknowledged apply. Если API service недоступен или возвращает non-200, dashboard возвращает `502 config_not_applied` и восстанавливает предыдущую persisted policy. Emergency preset использует тот же apply/rollback contract.

Это отдельный request anti-abuse control plane. Provider spending/billing limits не настраиваются полем `token_budget` и требуют самостоятельной spending policy.

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
    - API_BEARER_TOKEN=${API_BEARER_TOKEN}
    - ADMIN_TOKEN=${ADMIN_TOKEN}
  volumes: [tenant_uploads:/data/tenant-dbs]
```

---
**Last verified:** 2026-08-20 (working tree with acknowledged anti-abuse apply changes) — global policy save/preset rollback, OpenAPI/dashboard contracts and active emergency settings сверены с кодом; full repository CI/live verification marker обновляется после final validation.

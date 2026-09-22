# CI/CD и Quality Gates

## CI Pipeline (`.github/workflows/ci.yml`)

| Job | Что проверяет | Команда |
|---|---|---|
| `lint-python` | Ruff lint, format, Pyright, uv audit | `ruff check`, `ruff format --check`, `pyright`, `uv audit` |
| `lint-js` | Biome (embed/admin-dashboard/static JS) + node:test демо-страницы | `biome check`, `node --experimental-vm-modules --test demo/web/static/*.test.mjs` |
| `test-python` | Все Python тесты | `pytest` по всем пакетам |
| `test-storefront` | Тесты demo/autoparts-store (Django на SQLite) | `uv run --frozen --directory demo/autoparts-store python manage.py test tests` |
| `lint-go` | golangci-lint v2 + govulncheck | `golangci-lint run ./...`, `govulncheck` |
| `test-go` | Go тесты | `go test ./... -count=1 -timeout 180s` |
| `test-admin-js` | Admin dashboard vitest | `npm test` |
| `test-embed` | Embed-виджет тесты + build | `npm test`, `bash build.sh` |
| `test-e2e` | e2e без LLM (agent-db) | two-stage Compose: `up -d` long-lived services, затем `run --rm e2e` |

Pipeline зелёный = все джобы проходят (lint-python, docs-links, lint-js, test-storefront, test-python, lint-go, test-go, test-embed, test-admin-js, test-e2e).

`demo/autoparts-store` — foreign project со своим uv-окружением (не часть helperium workspace), поэтому в CI и в `make ci-test-storefront` он запускается через `--frozen --directory`; `config.test_settings` подменяет PostgreSQL на in-memory SQLite, так что сервис БД не нужен.

## Docker E2E lifecycle

`ci-state-init` намеренно завершается с кодом `0` после bootstrap named CI volumes. Поэтому он не может находиться в `docker compose up --abort-on-container-exit`: normal init exit остановит stack ещё до pytest. GitHub Actions и local reproduction запускают только long-lived dependencies detached, затем выполняют `e2e` как единственный terminal process. Для `--profile test` обёртка `compose.sh` принудительно выставляет test-only MCP/API credentials, explicit MCP Origin policy и high test-only rate limits, поэтому local `.env` не может ослабить E2E security coverage. CORS default задаётся явно, чтобы runner/user `.env` с wildcard не ослабил fail-closed CORS regression.

```bash
ADMIN_TOKEN=ci-secret-token VIEWER_TOKEN=ci-viewer-token CORS_ALLOW_ORIGINS=http://localhost:8080 \
  ./infra/scripts/compose.sh --profile test up -d data-service mcp-gateway api admin-dashboard web
ADMIN_TOKEN=ci-secret-token VIEWER_TOKEN=ci-viewer-token CORS_ALLOW_ORIGINS=http://localhost:8080 \
  ./infra/scripts/compose.sh --profile test run --rm e2e
ADMIN_TOKEN=ci-secret-token VIEWER_TOKEN=ci-viewer-token CORS_ALLOW_ORIGINS=http://localhost:8080 \
  ./infra/scripts/compose.sh --profile test down -v
```

## Pre-commit hooks (`.pre-commit-config.yaml`)

```bash
pre-commit install          # установка
pre-commit run --all-files  # прогнать
```

- `ruff`, `ruff-format` — Python lint/format
- `Pyright` — type correctness
- `go vet` — Go (data-service, mcp-gateway)
- `gitleaks` — секреты
- `admin-dashboard-tests` — vitest + contract scan (хука `admin-dashboard-stale` в `.pre-commit-config.yaml` нет)
- `trailing-whitespace`, `end-of-file-fixer`, `check-yaml`, `check-added-large-files`, `check-merge-conflict`

## Линтеры

**Python (ruff + Pyright):**
```bash
uv run ruff check api-service/src/
uv run ruff format --check api-service/src/
npx pyright
```

**Go (golangci-lint v2):**
```bash
cd data-service && golangci-lint run ./...
cd mcp-gateway && golangci-lint run ./...
```

## Makefile

```bash
make ci               # полный прогон (~2-3 мин)
make ci-lint-py       # только Python линт
make ci-test-py       # только Python тесты (~10 сек)
make ci-lint-go       # только Go линтинг
make ci-test-go       # только Go тесты (~30 сек)
make ci-test-js       # JS-тесты demo-web (node:test)
make ci-test-storefront  # Django-тесты demo/autoparts-store (SQLite)
make ci-admin         # admin-dashboard + JS тесты (~2 сек)
make ci-audit         # uv audit + govulncheck
```

## act — точная симуляция CI

```bash
brew install act
act -j lint-go              # одна джоба
act --pull=false            # весь пайплайн
```

Требует Docker Desktop, 100% совпадение с GitHub Actions.

## Admin-dashboard: защита от регрессий

Admin-dashboard — SPA на Alpine.js, вкомпилированная в Go-бинар через `//go:embed`.

### Архитектура JS-модулей

```
admin-dashboard/internal/server/static/
├── app.js                          # Точка входа, Alpine.start()
├── js/
│   ├── apiClient.js                # Обёртка fetch → Alpine.store('api')
│   ├── store.js                    # Alpine.store() — глобальное состояние
│   ├── core/
│   │   ├── apiLogger.js            # Логирование API + debug-панель
│   │   ├── eventBus.js             # pub/sub
│   │   └── notify.js               # Toast-уведомления
│   └── domains/
│       ├── auth.js                 # Авторизация
│       ├── tenants.js              # CRUD tenant'ов
│       ├── config.js               # Конфиги
│       ├── tools.js                # MCP-инструменты, approval
│       ├── rag.js                  # RAG-документы
│       ├── agents.js               # CRUD агентов
│       ├── abuse.js                # Anti-abuse
│       ├── emergency.js            # Lockdown
│       ├── llm.js                  # LLM-провайдеры
│       └── voice.js                # STT
└── styles.css
```

**Auth bypass:** Go-сервер пропускает `/static/` и `/js/` — прим.: `/js/` в коде нет; статика отдаётся через `r.Handle("/*", staticHandler)` (`server.go:140`), в `internal/server/static/` лежит собранный `dist/app.js` (SPA заBundleлена), каталога `js/` и доменных модулей (`js/domains/...`) не существует.

### Три уровня защиты

1. **JS unit-тесты** (`services/admin-dashboard/tests/api.test.js`, 16 тестов) — парсинг 200/204/422/401, ошибки.
2. **Contract-тесты** (`services/admin-dashboard/tests/contract.test.js`) — сканируют domain-модули и сверяют вызовы с 3 контрактными JSON (api-service, rag, admin endpoints).
3. **Pre-commit хуки:** stale-бинарник и vitest при изменении `app.js`/domain-модулей.

### Сборка фронта в CI

Бандл админки (`internal/server/static/dist/app.js`) — gitignored артефакт, и
образ его не собирает (в образе нет node/npm). Поэтому в `ci.yml` он собирается
до всех потребителей:

- **`test-admin-js`:** `npm ci` + `bash build.sh` в `services/admin-dashboard` **до** `npm test`. `contract.test.js` читает бандл; раньше отсутствие файла молча пропускалось через `if (existsSync(...))` — то есть в чистом чек-ауте CI тихо терял половину контрактной проверки (сканировались только `src/domains/*.ts`). Теперь тест падает с подсказкой `make build-admin`.
- **`test-e2e`:** те же два шага до `docker/build-push-action` — иначе `helperium-admin:latest` собирается без бандла, `/dist/app.js` уходит в SPA-fallback (HTML вместо JS), и e2e гоняется против мёртвого UI.
- **Node 22** в обеих джобах: `build.sh` зовёт `html-validate`, чей CLI требует `fs.globSync` (Node 20 падает с `TypeError`). Требование закреплено в `services/admin-dashboard/package.json` (`engines`) и проверяется первым шагом `build.sh`.
- **`Dockerfile`** закрывает третий случай (локальный `docker build` без сборки): guard `test -f internal/server/static/dist/app.js` валит сборку образа с инструкцией вместо тихой поломки.

Диагностика и таблица симптомов — в [`services/admin-dashboard/README.md`](../../services/admin-dashboard/README.md) (разделы «Сборка» и «Docker»).

```bash
make ci-admin
cd admin-dashboard/tests && npm test
```

**OpenAPI контракт:**
```bash
curl -s http://127.0.0.1:8081/openapi.json | python3 -c "import sys,yaml,json; yaml.dump(json.load(sys.stdin), sys.stdout)" > specs/api.openapi.yaml
npx openapi-typescript specs/api.openapi.yaml -o admin-dashboard/internal/server/static/api-types/api-service.d.ts
```

## Версионирование

Все пакеты синхронизированы: текущая **`1.1.0`**. Go: data-service/mcp-gateway `1.26.5`, admin-dashboard/helperium-go `1.26.5` (все четыре go.mod на go 1.26.5; версии `1.24.0` в репозитории нет).

## Критерий готовности перед коммитом

1. [ ] `make ci` — зелёный
2. [ ] Pre-commit hooks — все Passed
3. [ ] e2e без LLM зелёные — native `./infra/scripts/dev.sh e2e` или documented two-stage Docker `up -d` + `run --rm e2e`
4. [ ] Mutation score не упал (опционально)
---
**Last verified:** 2026-09-22 (working tree following `2b83366`) — в секцию admin-dashboard добавлена сборка фронта в CI: шаг `build.sh` в `test-admin-js`/`test-e2e`, громкий `contract.test.js` вместо `if (existsSync(...))`, guard в `Dockerfile`; обе джобы переведены на Node 22 (`html-validate` требует `fs.globSync`). Проверка путей документации больше не сканирует `CHANGELOG.md`: исторические записи перечисляют удалённые файлы, и это не мёртвые ссылки (правило в docstring `infra/scripts/check_docs_paths.py`, тесты — `infra/scripts/test_check_docs_paths.py`). **Verification:** `make ci-admin` зелёный (Go 131 passed, vitest 75 passed); `ci.yml` — 10 джоб, порядок шагов `checkout → setup-node → npm ci → build.sh → docker build`; `make ci-docs` зелёный. Предыдущий marker: 2026-09-15 (working tree, pentest follow-up) — добавлены джоба `test-storefront` и шаг node:test в `lint-js`; таблица джоб приведена к актуальному `ci.yml` (в неё же добавлены `docs-links`, `test-admin-js`, `test-embed`, ранее отсутствовавшие). Предыдущий marker: 2026-08-20 (working tree after `e839d6c`) — workflow запускает long-lived CI dependencies detached, then E2E as the sole terminal container; clean Docker profile passed 137 tests with explicit fail-closed CORS default.

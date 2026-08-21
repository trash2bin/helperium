# CI/CD и Quality Gates

## CI Pipeline (`.github/workflows/ci.yml`)

| Job | Что проверяет | Команда |
|---|---|---|
| `lint-python` | Ruff lint, format, Pyright, uv audit | `ruff check`, `ruff format --check`, `pyright`, `uv audit` |
| `lint-js` | Biome (embed/admin-dashboard JS) | `biome check` |
| `test-python` | Все Python тесты | `pytest` по всем пакетам |
| `lint-go` | golangci-lint v2 + govulncheck | `golangci-lint run ./...`, `govulncheck` |
| `test-go` | Go тесты | `go test ./... -count=1 -timeout 180s` |
| `test-e2e` | e2e без LLM (agent-db) | two-stage Compose: `up -d` long-lived services, затем `run --rm e2e` |

Pipeline зелёный = все **6 джоб** проходят (lint-python, lint-js, test-python, lint-go, test-go, test-e2e).

## Docker E2E lifecycle

`ci-state-init` намеренно завершается с кодом `0` после bootstrap named CI volumes. Поэтому он не может находиться в `docker compose up --abort-on-container-exit`: normal init exit остановит stack ещё до pytest. GitHub Actions и local reproduction запускают только long-lived dependencies detached, затем выполняют `e2e` как единственный terminal process. CORS default задаётся явно, чтобы runner/user `.env` с wildcard не ослабил fail-closed CORS regression.

```bash
ADMIN_TOKEN=ci-secret-token VIEWER_TOKEN=ci-viewer-token API_BEARER_TOKEN=ci-api-control-token CORS_ALLOW_ORIGINS=http://localhost:8080 \
  ./infra/scripts/compose.sh --profile test up -d data-service mcp-gateway api admin-dashboard web
ADMIN_TOKEN=ci-secret-token VIEWER_TOKEN=ci-viewer-token API_BEARER_TOKEN=ci-api-control-token CORS_ALLOW_ORIGINS=http://localhost:8080 \
  ./infra/scripts/compose.sh --profile test run --rm e2e
ADMIN_TOKEN=ci-secret-token VIEWER_TOKEN=ci-viewer-token API_BEARER_TOKEN=ci-api-control-token CORS_ALLOW_ORIGINS=http://localhost:8080 \
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
**Last verified:** 2026-08-20 (working tree after `e839d6c`) — workflow запускает long-lived CI dependencies detached, then E2E as the sole terminal container; clean Docker profile passed 137 tests with explicit fail-closed CORS default.

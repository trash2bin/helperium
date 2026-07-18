# CI/CD и Quality Gates

## CI Pipeline (`.github/workflows/ci.yml`)

| Job | Что проверяет | Команда |
|---|---|---|
| `lint-python` | Ruff lint, format, Pyright | `ruff check`, `ruff format --check`, `pyright` |
| `test-python` | Все Python тесты | `pytest` по всем пакетам |
| `lint-go` | golangci-lint v2 | `golangci-lint run ./...` |
| `test-go` | Go тесты | `go test ./... -count=1 -timeout 180s` |

Pipeline зелёный = все 4 джобы проходят.

## Pre-commit hooks (`.pre-commit-config.yaml`)

```bash
pre-commit install          # установка
pre-commit run --all-files  # прогнать
```

- `ruff`, `ruff-format` — Python lint/format
- `Pyright` — type correctness
- `go vet` — Go (data-service, mcp-gateway)
- `gitleaks` — секреты
- `admin-dashboard-stale` — бинарник свежий?
- `admin-dashboard-tests` — vitest + contract scan
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
│       └── voice.js                # STT/TTS
└── styles.css
```

**Auth bypass:** Go-сервер пропускает `/static/` и `/js/`.

### Три уровня защиты

1. **JS unit-тесты** (`admin-dashboard/tests/api.test.js`, 16 тестов) — парсинг 200/204/422/401, ошибки.
2. **Contract-тесты** (`admin-dashboard/tests/contract.test.js`) — сканируют domain-модули и сверяют вызовы с 3 контрактными JSON (api-service, rag, admin endpoints).
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

Все пакеты синхронизированы: текущая **`1.1.0`**. Go: data-service/mcp-gateway `1.26.5`, admin-dashboard/helperium-go `1.24.0`.

## Критерий готовности перед коммитом

1. [ ] `make ci` — зелёный
2. [ ] Pre-commit hooks — все Passed
3. [ ] `uv run pytest tests/e2e/ -v` — 44 теста без LLM
4. [ ] Mutation score не упал (опционально)

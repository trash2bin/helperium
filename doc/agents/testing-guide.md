# Регрессионное тестирование

Перед коммитом/пушем:

## 1. Python Unit/Integration

```bash
uv run pytest rag/tests/                            # RAG — 108 тестов
uv run pytest api-service/src/api_service/tests/    # API — 262 теста
uv run pytest demo/web/tests/                       # Web — 73 теста
uv run pytest demo/tests/                           # Settings — 18 тестов
uv run pytest helperium-sdk/tests/                  # SDK — 83 теста
```

## 2. Go Unit/Integration

```bash
go test ./data-service/... ./mcp-gateway/...   # ~585 тестов
```

### 2b. Embed Widget

```bash
cd api-service/embed && npm test           # 59 тестов (vitest)
cd api-service/embed && bash build.sh      # typecheck + esbuild
```

> ⚠️ После пересборки виджета: `./scripts/dev.sh restart api`

## 3. E2E (pytest, рекомендуется)

```bash
uv run pytest tests/e2e/ -v                  # 48 тестов, без LLM
uv run pytest tests/e2e/test_data_isolation.py -v
uv run pytest tests/e2e/agents -v
uv run pytest tests/e2e/ -v --llm-key        # 52 теста с LLM
uv run pytest tests/e2e/llm/ -v              # только LLM (#MISTRAL_API_KEY)
```

## 4. Mutation testing

**Python (mutmut, ~30 мин):**
```bash
./scripts/run_mutmut.sh --build    # сборка Docker (1 раз)
./scripts/run_mutmut.sh --docker   # запуск
```
Score: ~65% (8100+ KILLED / 2681 SURVIVED).

**Go (go-mutesting, ~5 мин):**
```bash
./scripts/run_mutmut.sh --go
```

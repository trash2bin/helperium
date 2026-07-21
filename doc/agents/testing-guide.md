# Регрессионное тестирование

Перед коммитом/пушем:

## 1. Python Unit/Integration

```bash
uv run pytest rag/tests/                            # RAG — 108 тестов
uv run pytest api-service/src/api_service/tests/    # API — ~260 тестов
uv run pytest demo/web/tests/                       # Web — 73 теста
uv run pytest demo/tests/                           # Settings — 18 тестов
uv run pytest helperium-sdk/tests/                  # SDK — 83 теста
```

### 1b. Agent Pipeline Unit Tests (без LLM/MCP/data-service)

Вся новая функциональность агента тестируется через моки Protocol'ов.

```bash
cd api-service
uv run pytest src/api_service/tests/unit/agent/test_stages.py -v         # 22 Stage-теста
uv run pytest src/api_service/tests/unit/agent/test_middlewares.py -v     # 8 Middleware-тестов
uv run pytest src/api_service/tests/unit/agent/test_error_flow.py -v     # 18 Error-flow тестов
uv run pytest src/api_service/tests/unit/agent/test_orchestrator_e2e.py -v  # 3 интеграционных
uv run pytest src/api_service/tests/unit/agent/test_orchestrator_fixes.py -v  # 7 регрессионных
```

Все 58 тестов зелёные, работают без запущенных сервисов.

**Правила написания тестов для Stage'ов:**

```python
from .helpers import TestLLMProvider, TestMCPProvider, llm_response, make_pipeline_ctx

async def test_my_stage():
    llm = TestLLMProvider()
    llm.queue(llm_response.final("answer"))

    mcp = TestMCPProvider()
    mcp.add_tool("find", {"ok": True, "data": {"id": "1"}})

    ctx = await make_pipeline_ctx(llm_provider=llm, mcp_provider=mcp)

    stage = MyStage()
    events = await collect_events(stage.run(ctx))
    assert any(t == "final" for t, _ in events)
```

**Тестирование Pipeline целиком:** использовать `Pipeline(stages=[LLMStage()], finalizer_stages=[...])` — не забыть параметр `finalizer_stages` если тестится финализация.

```python
from .helpers import make_pipeline_ctx
from api_service.agent.pipeline import Pipeline

ctx = await make_pipeline_ctx()
pipeline = Pipeline(stages=[...], finalizer_stages=[...])
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

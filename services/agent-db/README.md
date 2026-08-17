# agent-db

Unified CLI + Python seedgen for helperium database materialization, tenant registration, and e2e testing.

## Architecture

```
agent-db/
├── agent_db/
│   ├── __init__.py              # Package init
│   ├── cli.py                   # Click entry point: materialize, register, test, bench
│   ├── core/__init__.py         # Path resolution, shared constants
│   ├── seedgen/                 # Python seed generator
│   │   ├── __init__.py          # Public API: generate_ddl, apply, apply_with_ddl, materialize
│   │   ├── models.py           # Entity, Field, ScenarioConfig + TestSeed + Seed models
│   │   ├── ddl.py              # Config entities → CREATE TABLE (driver-aware)
│   │   ├── apply.py            # DDL + seed data insertion to SQLite/Postgres
│   │   └── materialize.py      # scenario dir (config.json + seed.json) → populated .db
│   └── bench/                   # Core Benchmark (детерминированный, без LLM-судьи)
│       ├── __init__.py
│       ├── cases/autoparts.json # 48 кейсов (lookup/filter/count/absence/status)
│       ├── models.py           # TestCase, RunResult, BacklogData, EvalResult, BenchmarkReport
│       ├── runner.py           # POST /api/chat/{agent} (SSE) + backlog + отдельный bench-лог
│       ├── backlog_parser.py   # backlog JSONL → BacklogData (turn_end)
│       ├── evaluator.py        # детерминированные проверки (retrieval/answer/halluc/refusal/entity/recovery)
│       ├── report.py           # агрегация метрик + печать + JSON
│       ├── cli.py              # typer CLI (python -m agent_db.bench run <cases>)
│       ├── __main__.py         # точка входа
│       ├── parser.py           # (legacy) backlog → TurnResult
│       ├── reader.py           # чтение backlog-файлов
│       ├── reporter.py         # (legacy) формат отчётов
│       ├── smoke_scripted.py   # dev-смоук без LLM (ScriptedLLMProvider)
│       └── README.md           # документация бенча
├── pyproject.toml
├── tests/test_bench_core.py   # 26 pytest (детерминированные, без LLM/сети)
└── README.md
```

**Key change (v1.1.0):** `seedgen` moved from `data-service/internal/seedgen/` (Go) into `agent-db/agent_db/seedgen/` (Python).
`data-service --materialize` flag and `cmd/seed-cli/` are removed. All seed generation happens through Python seedgen now.

**CLI moved:** from `cli.py` (root) to `agent_db/cli.py` (package).

## Seed generation (Python seedgen)

```python
from agent_db.seedgen import materialize, generate_ddl, apply, TestSeed

# Materialize a scenario directory → populated SQLite database
materialize("data-service/testdata/scenarios/shop", force=True)

# Or use from code with seed data
import sqlite3
conn = sqlite3.connect(":memory:")
apply(conn, TestSeed)

# Generate DDL from entity descriptions
from agent_db.seedgen.models import Entity, EntityField, FieldType
ddl = generate_ddl([Entity(name="user", table="users", id_column="id", fields=[
    EntityField(name="id", column="id", type=FieldType.INT, primary_key=True),
    EntityField(name="name", column="name", type=FieldType.STRING),
])])
```

## CLI Commands (legacy)

```bash
# Materialize scenario databases (config.json + seed.json → SQLite)
agent-db materialize <scenario> [--force]
agent-db materialize-all [--all] [--force]

# Tenant registration
agent-db register <tenant_id> <scenario>   # register scenario as tenant
agent-db register-all [tenant_id:scenario ...]  # register multiple

# Serve scenario as data-service
agent-db serve <scenario> [--port]

# Test orchestration (requires running services) — pytest recommended
# (services/agent-db/tests/e2e/*.py, см. ниже; legacy agent-db test/e2e — упрощённые smoke)
agent-db test [--tenants default,shop]  # isolation + dynamic tools

# List scenarios and tenants
agent-db scenarios       # list available scenarios
agent-db tenants         # list registered tenants (via data-service)
agent-db drop <scenario> # remove scenario database
```

## E2E testing (recommended: pytest)

New modular pytest tests in `services/agent-db/tests/e2e/` — faster, self-documented, with proper fixtures.

```bash
# Full deterministic E2E without a real LLM (requires running services).
# The pytest summary is the authoritative current test count and duration.
./infra/scripts/dev.sh e2e

# Compose-режим: тесты выполняются внутри /workspace и видят те же SQLite-пути, что data-service
./infra/scripts/compose.sh --profile test up e2e --abort-on-container-exit --exit-code-from e2e
./infra/scripts/compose.sh --profile test down -v

# Или напрямую в native-режиме (только после `./infra/scripts/dev.sh start`):
uv run pytest services/agent-db/tests/e2e/ -v

# С LLM-тестами (opt-in, services/agent-db/tests/e2e-llm/, требует API key из .env)
uv run pytest services/agent-db/tests/e2e-llm/ -v --llm-key

# Traceback off (pass/fail only)
uv run pytest services/agent-db/tests/e2e/ --no-traceback

# Individual modules
uv run pytest services/agent-db/tests/e2e/test_data_isolation.py -v
uv run pytest services/agent-db/tests/e2e/test_agents.py -v
uv run pytest services/agent-db/tests/e2e/test_mcp_composite.py -v
# Official MCP SDK v2: tool call, composite scope and header-only tenant routing.
uv run pytest services/agent-db/tests/e2e/test_mcp_streamable_http.py -v
uv run pytest services/agent-db/tests/e2e-llm/test_llm_chat.py -v
```

## Quick start — add your own database

> Канонический каталог сценариев — `data-service/testdata/scenarios/`
> (`agent_db/core.SCENARIOS_DIR`). Для своих сценариев можно использовать
> любую директорию (пример ниже — `agent-db/scenarios/mydb`).

```bash
# 1. Create a scenario directory
mkdir -p agent-db/scenarios/mydb

# 2. Copy template config
cp specs/config.example.json agent-db/scenarios/mydb/config.json
# Edit entities, endpoints to match your schema

# 3. (Optional) Create seed.json with test data
# Use helperium_sdk.seed_models.StorageSeed for structure

# 4. Generate the database
uv run --package agent-db python3 -c "
from agent_db.seedgen import materialize
cfg = materialize('agent-db/scenarios/mydb', force=True)
print('OK:', cfg.data_source.dsn)
"

# 5. Register tenant with this database via admin API
curl -X POST http://127.0.0.1:8084/admin/tenants \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"id":"mydb","config":{"version":1,"data_source":{"driver":"sqlite","dsn":"/path/to/mydb.db"},"entities":[...]}}'

# 6. Done — data-service serves your database
curl -H "X-Tenant-ID: mydb" http://127.0.0.1:8084/health
```

## Legacy vs modern

| Feature | Legacy (Go seedgen + agent-db CLI) | Modern (Python seedgen + pytest) |
|---|---|---|
| Seed generation | `data-service/cmd/seed-cli/` (Go, ~130 строк) | `agent-db/agent_db/seedgen/` (Python, ~650 строк) |
| Materialize | `data-service --materialize` (в production binary) | `materialize()` из Python-пакета |
| E2E tests | `cli.py` `_run_*` функции (~900 строк) | `services/agent-db/tests/e2e/*.py` — модульные deterministic tests, включая native MCP SDK v2 contract |
| LLM tests | — | `services/agent-db/tests/e2e-llm/test_llm_chat.py — 4 теста (opt-in) |
| DB generation in e2e | `subprocess.run(["go", "run", "./cmd/seed-cli/"])` | `from agent_db.seedgen import materialize` |
| CLI entry point | `cli.py` (root) | `agent_db/cli.py` |
| Benchmark | — | `agent_db/bench/` — парсинг, прогон, отчёт |
---
**Last verified:** 2026-08-17 (working tree after `7de9feb`) — E2E commands distinguish full suite, composite tenant coverage and the official Streamable HTTP v2 contract test; obsolete SSE-only test is removed.

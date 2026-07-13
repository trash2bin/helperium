# agent-db

Unified CLI + Python seedgen for helperium database materialization, tenant registration, and e2e testing.

## Architecture

```
agent-db/
├── cli.py                       # Click entry point (legacy: e2e, materialize, register)
├── core/__init__.py             # Path resolution, shared constants
│
├── agent_db/
│   └── seedgen/                 # Python reimplementation of data-service/seedgen (Go → Python)
│       ├── __init__.py          # Public API: generate_ddl, apply, apply_with_ddl, materialize
│       ├── models.py           # Entity, Field, ScenarioConfig + TestSeed + Seed models
│       ├── ddl.py              # Config entities → CREATE TABLE (driver-aware)
│       ├── apply.py            # DDL + seed data insertion to SQLite/Postgres
│       └── materialize.py      # scenario dir (config.json + seed.json) → populated .db
│
├── scenarios/                   # (future) Scenario directories — currently in data-service/testdata/scenarios/
│
├── pyproject.toml
└── README.md
```

**Key change (v1.1.0):** `seedgen` moved from `data-service/internal/seedgen/` (Go) into `agent-db/agent_db/seedgen/` (Python).
`data-service --materialize` flag and `cmd/seed-cli/` are removed. All seed generation happens through Python seedgen now.

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

# Test orchestration (requires running services)
agent-db test [--tenants default,shop]  # isolation + dynamic tools
agent-db e2e    [--tenants default,shop]  # full pipeline: web-proxy + SSE chat

# List scenarios and tenants
agent-db scenarios       # list available scenarios
agent-db tenants         # list registered tenants (via data-service)
agent-db drop <scenario> # remove scenario database
```

## E2E testing (recommended: pytest)

New modular pytest tests in `tests/e2e/` — faster, self-documented, with proper fixtures.

```bash
# All e2e without LLM — 48 tests, ~5 sec
uv run pytest tests/e2e/ -v

# With LLM tests (requires API key from .env)
uv run pytest tests/e2e/ -v --llm-key

# Traceback off (pass/fail only)
uv run pytest tests/e2e/ --no-traceback

# Individual modules
uv run pytest tests/e2e/test_data_isolation.py -v
uv run pytest tests/e2e/test_agents.py -v
uv run pytest tests/e2e/test_mcp_composite.py -v
uv run pytest tests/e2e/test_sse_session.py -v
uv run pytest tests/e2e/llm/test_llm_chat.py -v
```

## Quick start — add your own database

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
  -H "Authorization: Bearer secret" \
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
| E2E tests | `cli.py` `_run_*` функции (~900 строк) | `tests/e2e/*.py` — модульные, 49 тестов |
| LLM tests | — | `tests/e2e/llm/test_llm_chat.py — 4 теста |
| DB generation in e2e | `subprocess.run(["go", "run", "./cmd/seed-cli/"])` | `from agent_db.seedgen import materialize` |

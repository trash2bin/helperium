# Эксплуатация и разработка

## Нативный запуск: `infra/scripts/dev.sh`

```bash
./infra/scripts/dev.sh start              # поднять весь стек
./infra/scripts/dev.sh stop / restart     # управление
./infra/scripts/dev.sh logs {service|all} # логи из .data/logs/
./infra/scripts/dev.sh status             # статус
```

Порядок старта: data → rag → mcp → admin → api → web

## Docker-запуск

```bash
./infra/scripts/compose.sh up -d                        # dev режим
./infra/scripts/compose.sh --profile prod up -d         # Caddy + HTTPS
./infra/scripts/compose.sh build                        # пересборка
./infra/scripts/compose.sh --profile monitoring up -d   # Prometheus + Grafana
```

Launcher всегда использует `infra/docker-compose.yml`, корневой `.env` и
`infra/` как project directory, поэтому его можно запускать из любой директории.

Тома в `./.data/` (БД, индексы ChromaDB, кэш моделей).

## Seed generation (Python seedgen)

```python
from agent_db.seedgen import materialize, generate_ddl, apply, TestSeed

cfg = materialize("data-service/testdata/scenarios/sqlite-testseed", force=True)

# Или напрямую в SQLite
import sqlite3
conn = sqlite3.connect(":memory:")
apply(conn, TestSeed)

ddl = generate_ddl(entities, "sqlite")
```

Быстро накидать свою БД:
```bash
mkdir -p agent-db/scenarios/mydb
cp specs/config.example.json agent-db/scenarios/mydb/config.json
uv run --package agent-db python3 -c "from agent_db.seedgen import materialize; materialize('agent-db/scenarios/mydb', force=True)"
curl -X POST http://127.0.0.1:8084/admin/tenants -H "Authorization: Bearer secret" ...
```

**agent-db CLI (legacy):** `uv run agent-db register <tenant_id> <scenario>`, `uv run agent-db tenants`, `uv run agent-db drop <scenario>`
---
**Last verified:** 2026-08-09 (commit `3aa1cdbc172fd7b95140a36577eee78f87ec218d`) — Docker launcher проверен из корня и из произвольной директории

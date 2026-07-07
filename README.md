# agent-tutor

LLM-agent with auto-generated tools from any SQL database schema. Connects to a client's database, introspects the schema, generates REST API and MCP tools on the fly, and gives the AI agent read/write(only confirmation) access to data without writing a line of code per database.

## Architecture

Seven independent HTTP services. All services communicate via HTTP with tenant isolation through `X-Tenant-ID`.

```
admin-dashboard:8085  -- proxies to all services (admin UI)

web:8080 ─┬→ api:8081 ─→ mcp-gateway:8083 ─→ data-service:8084 ── SQL ── DB
          │                              └→ rag:8082 ── ChromaDB
          ├→ data-service:8084 (data for UI tables)
          └→ rag:8082 (document list for UI)
```

| Service | Stack | Base port | Purpose |
|---|---|---|---|
| data-service | Go (chi, modernc/sqlite) | 8084 | Generic CRUD/Query proxy. Config-driven, introspects DB, generates REST + MCP manifest per tenant |
| mcp-gateway | Go (chi, mcp-go) | 8083 | Stateless MCP server (SSE/JSON-RPC). Resolves tools dynamically from data-service by tenant |
| admin-dashboard | Go (chi, Alpine.js) | 8085 | Web admin panel: tenant CRUD, config management, tool approval, RAG documents, agents |
| rag | Python (FastAPI) | 8082 | Semantic search over documents (ChromaDB + SQLite/PostgreSQL) |
| api | Python (FastAPI, LiteLLM) | 8081 | Agent orchestrator, LLM integration, Agent Store (CRUD), SSE streaming, session/backlog. [Embeddable chat widget →](api-service/embed/README.md) |
| web | Python (FastAPI) | 8080 | UI + reverse-proxy. Proxies X-Tenant-ID to all upstream services |

Storage: SQLite (default) or PostgreSQL. Vector index: ChromaDB. Embeddings: Sentence Transformers (local).

## Quick Start

```bash
git clone https://github.com/trash2bin/agent-tutor
cd agent-tutor
uv sync

# Install Go (for data-service and mcp-gateway)
# https://go.dev/dl/

# Start all services
./scripts/dev.sh start

# Open in browser
open http://127.0.0.1:8080
```

Default agent expects Ollama at `http://127.0.0.1:11434` with model `qwen2.5:0.5b`.
Switch providers via env vars (see `.env.example`):

```bash
MISTRAL_API_KEY=<token> MISTRAL_MODEL=mistral-medium ./scripts/dev.sh restart
```

### Docker

```bash
docker compose up -d                        # 6 services (dev)
docker compose --profile prod up -d         # + Caddy for HTTPS
```

## CLI (Data Management & Testing)

```bash
# Database scenarios and tenant management
uv run agent-db materialize university --force     # create test DB from scenario
uv run agent-db tenant register university         # register tenant
uv run agent-db tenant list                        # list active tenants
uv run agent-db e2e --tenants default,shop         # full E2E pipeline
uv run agent-db e2e-data                           # data isolation (8 tests)
uv run agent-db e2e-mcp                            # MCP tools (3 tests)
uv run agent-db e2e-mcp-composite                  # composite multi-tenant MCP (3 tests)
uv run agent-db e2e-full                           # all three levels (data + mcp + chat)

# RAG utilities
uv run agent-rag-ingest import ~/lecture.pdf -d <discipline-id>
uv run agent-rag-ingest search "quantum computing"
uv run agent-rag-docgen generate -d <discipline-id>

# Seed generation
uv run agent-seedgen --students 80
```

## Tests

```bash
# Python (150 tests)
uv run pytest

# Go (391 tests in 14 packages)
go test ./data-service/... ./mcp-gateway/...

# E2E integration
uv run agent-db e2e-full
```

## Multi-Tenancy

System provides full data and tool isolation at three levels:

- **Data level**: `TenantStore` holds isolated DB connections per tenant (separate SQLite files or PG schemas)
- **Tool level**: MCP tools are registered with tenantID in closure -- a call to `tenant-a__list_students` always routes to tenant-a's data-service
- **Consumer level**: Orchestrator passes `X-Tenant-ID` as-is; tenants absent from the header are invisible

Composite mode (one SSE session for N tenants) is supported -- tools get `{tenantID}__` prefix for conflict-free routing. Details in [mcp-gateway/README.md](mcp-gateway/README.md).

## Project Structure

```
data-service/       Go DB proxy (config-driven, :8084)
mcp-gateway/        MCP server (Go, :8083)
admin-dashboard/    Admin UI (Go + Alpine.js, :8085)
rag/                RAG service (Python, :8082)
agent-tutor-sdk/    Shared Pydantic models + HTTP clients
api-service/src/api_service/           Agent orchestrator + API (FastAPI, :8081)
api-service/embed/                     Embeddable chat widget (vanilla JS, Shadow DOM) — [README →](api-service/embed/README.md)
demo/web/           Web UI + reverse-proxy (FastAPI, :8080)
specs/              OpenAPI specs + JSON Schema config
scripts/            dev.sh -- run all services
doc/                Plans and roadmap
```
## Stack

Go 1.24, Python 3.13, JavaScript (Alpine.js) — FastAPI, LiteLLM, ChromaDB, Sentence Transformers, SQLite, PostgreSQL, Docker Compose, Caddy, docling, MCP

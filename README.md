# Agent: Self-Hosted AI Platform

![Go](https://img.shields.io/badge/Go-1.26.5-00ADD8?logo=go)
![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi)
![LiteLLM](https://img.shields.io/badge/LiteLLM-FF6F00)
![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker)
![Tests](https://img.shields.io/badge/Tests-1100%2B-brightgreen)
![License](https://img.shields.io/badge/License-MPL%202.0-blue)

🇷🇺 [Читать на русском](README_RU.md)

Self-hosted AI agent platform for any organization with a SQL database. An online shop connects its product catalog — visitors ask "find me a laptop under 1000$". A university connects its student database — students ask "what's my schedule for tomorrow?". A logistics company connects its warehouse DB — operators ask "where is order #4421?".

No code per database. No sending proprietary data to third-party clouds.

## Overview

Any business with a SQL database — an online shop, a university, a logistics company, a hospital — can connect it to Helperium. The platform introspects the schema, auto-generates tools, and exposes an AI agent that answers end-user questions in real time.

Unlike static RAG systems that require manual re-indexing, this platform queries the live database in read-only mode. The business owner controls exactly which tables, columns, and operations are visible to the agent through an administrative dashboard. The LLM layer can be deployed locally on GPU-equipped hardware or routed through any OpenAI-compatible provider.

## Screenshots


| Demo Web — chat with agent & data tables | Admin Dashboard — dashboard & tenant list |
|---|---|
| ![Demo Web UI](doc/demo-web-ui.png) | ![Admin Dashboard](doc/admin-dashboard.png) |

| API Swagger (api-service) | RAG Swagger (rag-service) |
|---|---|
| ![API Swagger](doc/api-swagger.png) | ![RAG Swagger](doc/rag-swagger.png) |

### Admin Panels

| Tenants list | Tenant config — entities & endpoints |
|---|---|
| ![Admin Tenants](doc/admin-tenants.png) | ![Admin Config](doc/admin-config.png) |

| Tools & write approval | Agents |
|---|---|
| ![Admin Tools](doc/admin-tools.png) | ![Admin Agents](doc/admin-agents.png) |

| RAG document management | Anti-Abuse settings |
|---|---|
| ![Admin RAG](doc/admin-rag.png) | ![Admin Anti-Abuse](doc/admin-antiabuse.png) |

| Data Service Swagger UI | MCP Gateway Debug Playground |
|---|---|
| ![Data Service Swagger](doc/data-swagger.png) | ![MCP Debug](doc/mcp-debug.png) |

### Monitoring

| Grafana Dashboard (12 panels) — full-page overview of all service metrics |
|---|
| ![Grafana Overview](doc/grafana-overview.png) |

## Core Capabilities

- **Live SQL introspection.** Connects to SQLite or PostgreSQL. Auto-generates entity-level tools (`list_*`, `get_by_id`, `find`) and custom query tools from the schema. No code changes required when the database structure evolves.
- **Read-only by default.** All write operations are blocked at the MCP gateway layer. Write tools exist as an opt-in capability but are disabled by default and require explicit admin approval before they appear in the agent's tool manifest.
- **Domain-agnostic.** Works with any schema — product catalogs, student records, patient data, inventory, orders. The agent adapts to whatever tables and columns it finds.
- **Hybrid retrieval.** Combines live SQL queries with vector search over uploaded documents (PDF, TXT, MD, DOCX). Documents are chunked, embedded, and cached. Re-embedding pipelines handle updates without full re-indexing.
- **Embeddable widget.** A single `<script>` tag injects a Shadow DOM-isolated chat widget into any website. Zero dependencies, configurable accent colors, SSE streaming with token-by-token rendering.
- **Administrative control.** Per-tenant configuration: toggle entities on/off, rename fields for business context, rewrite descriptions for the agent, enable or disable individual endpoints, set LLM provider and model per client, configure spending limits and anti-abuse guardrails.
- **Observability.** Prometheus metrics and a 12-panel Grafana dashboard covering request rates, LLM calls by model, tool invocations, token usage, RAG search rates, cache hit ratios, and active SSE sessions.

## Architecture

The platform consists of six independent HTTP services. They communicate over REST and SSE to enable horizontal scaling across multiple machines. In single-machine deployments, they function as a cohesive monolith over localhost without containerization overhead.

```
User -> web:8080 -> api:8081 -> mcp-gateway:8083 -> data-service:8084 -> DB
       (JS script)   (FastAPI       (Go, MCP            (Go, config-   (SQLite /
                     + LiteLLM)    SSE/JSON-RPC)        driven CRUD)    PostgreSQL)
                        |
                        v
                   rag:8082
                (Python,
              document search) -> ChromaDB (vector)
```

- **Mechanical workloads** (CRUD proxy, MCP gateway, admin dashboard, reverse proxy) are written in Go for throughput and full async concurrency.
- **AI workloads** (agent orchestration, LLM integration, RAG, embeddings) are written in Python using FastAPI, LiteLLM, and Sentence Transformers.

### Multi-tenant isolation

Three layers of isolation are enforced and verified in CI:

| Layer    | Mechanism                                  |
| -------- | ------------------------------------------ |
| Data     | Separate SQLite files or PG schemas        |
| Tools    | MCP tools registered with tenant ID in closure |
| Consumer | `X-Tenant-ID` header propagated through the stack |

Composite mode allows a single SSE session to route across N tenants with prefixed tool names (`{tenantID}__tool_name`) for conflict-free resolution. Different agents can be assigned to different sections of a site (e.g., one agent for unauthenticated visitors, another for logged-in customers with access to order history).

### Infrastructure flexibility

- **LLM providers.** Any OpenAI-compatible endpoint: local Ollama, Mistral, OpenAI, Anthropic, or self-hosted models on private GPU infrastructure.
- **Embeddings.** Remote API calls or local inference via Sentence Transformers.
- **Vector storage.** ChromaDB is the default. The architecture supports replacement with pgvector or Qdrant based on deployment requirements.
- **Databases.** SQLite for zero-config local development and single-tenant deployments. PostgreSQL for production multi-tenant environments. Extending to MySQL/MariaDB/MSSQL requires only adding a `database/sql` driver in the data service; the query builder is generic.

## Security Model

- **Read-only enforcement.** Write operations are blocked at the MCP gateway. The write-tool approval workflow exists in the admin dashboard but is disabled by default and treated as an extension point rather than a core feature.
- **Test-Driven Development.** CI pipeline enforces a failing-test-first workflow. Test counts are not hardcoded in documentation; the pipeline reports current coverage dynamically.
- **Pentest coverage.** A comprehensive security checklist is maintained in `doc/PENTEST-CHEK.md`. Each attack vector is tracked from initial failing test through remediation to passing state.
- **Tenant isolation.** Verified at three layers under concurrent load in end-to-end tests.
- **Widget hardening.** The embed endpoint sets `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, and long-lived immutable cache headers for static assets. Content Security Policy requirements are documented for host sites.

## Deployment

### Docker Compose (recommended for production)

```bash
docker compose up -d                              # dev (7 services)
docker compose --profile prod up -d               # + Caddy HTTPS termination
docker compose --profile monitoring up -d         # + Prometheus + Grafana
```

All `.data/` paths are configurable via environment variables and mounted as volumes. The `prod` profile includes Caddy for automatic HTTPS.

### Local development (macOS / Linux)

The platform runs without Docker overhead via a shell script:

```bash
git clone https://github.com/trash2bin/helperium
cd helperium
uv sync
./scripts/dev.sh start
open http://127.0.0.1:8080
```

Default LLM is Ollama at `http://127.0.0.1:11434` with `qwen2.5:0.5b`. Switch providers via environment variables:

```bash
MISTRAL_API_KEY=<token> MISTRAL_MODEL=mistral-medium ./scripts/dev.sh restart
```

### CLI for data management and testing

```bash
uv run agent-db materialize university --force     # create test DB from scenario
uv run agent-db tenant register university         # register a tenant
uv run agent-db tenant list                        # list active tenants
uv run agent-db e2e --tenants default,shop         # full E2E pipeline
uv run agent-db e2e-full                           # data + mcp + chat
uv run agent-db e2e-mcp-composite                  # composite multi-tenant MCP
uv run agent-rag-ingest import ~/lecture.pdf -d <discipline-id>
uv run agent-rag-ingest search "quantum computing"
```

## Testing and CI

The test suite covers unit, integration, and end-to-end scenarios across both Go and Python services. GitHub Actions runs four jobs: `lint-python`, `test-python`, `lint-go`, `test-go`. Pre-commit hooks enforce linting locally before push. The `make ci` target simulates the full pipeline locally.

Test counts are dynamic and reported by the pipeline. Current coverage exceeds 1100 tests across:

- `data-service`: 326 tests (CRUD, schema introspection, write-tool approval)
- `rag`: 104 tests (chunking, embeddings, re-embedding pipeline)
- `web`: 55 tests (reverse proxy, multi-tenant routing, SSE proxy)
- `helperium-sdk`: 83 tests (shared models, HTTP clients)
- E2E suites: data isolation, MCP tool routing, composite multi-tenant sessions

## Embedded Widget

A single `<script>` tag injects a Shadow DOM-isolated chat widget into any website. SSE streaming, configurable accent colors, zero dependencies.

```html
<script src="https://your-server.com/embed/embed.js"
        data-agent="shop-assistant"
        data-title="Assistant"
        data-accent="#0f766e"
        data-position="right"
        data-greeting="How can I help?">
</script>
```

Widget state is isolated via Shadow DOM — no CSS conflicts with the host site. Multiple independent widgets on one page are supported. Configuration is done entirely through `data-*` attributes (14+ parameters: size, position, colors, placeholder, header).

See [`api-service/embed/README.md`](api-service/embed/README.md) for full documentation on the SSE protocol, CSP requirements, CSS variable customization, and multi-widget configurations.

## Documentation

| Document | Description |
| -------- | ----------- |
| [`AGENTS.md`](AGENTS.md) | Technical project passport: architecture, service map, testing, CI |
| [`doc/FINAL_TASK.md`](doc/FINAL_TASK.md) | Migration plan and readiness criteria for pre-final version |
| [`doc/RUNBOOK.md`](doc/RUNBOOK.md) | Internal deployment cheat sheet: server setup, widget embedding, monitoring |
| [`doc/PENTEST-CHEK.md`](doc/PENTEST-CHEK.md) | Security checklist and coverage status per attack vector |
| [`docker/grafana/MONITORING.md`](docker/grafana/MONITORING.md) | Prometheus metrics, Grafana dashboard, troubleshooting |
| [`.env.example`](.env.example) | All environment variables documented |

Service-level READMEs are located in each service directory (`data-service/`, `mcp-gateway/`, `admin-dashboard/`, `rag/`, `api-service/`, `demo/web/`).

## License and Commercial Support

The core platform is available under the Mozilla Public License 2.0 (MPL 2.0). You can self-host it, modify it for your own use, and integrate it into proprietary systems without opening your own codebase.

**Commercial modifications of the platform itself** (custom features, bespoke integrations, white-label versions) are controlled by the maintainer. Contact us for enterprise licensing, SLA-backed support, and custom development.

The project also uses a [Contributor License Agreement](CLA.md). Contributions submitted via pull requests may be used by the maintainer in any form, including commercial and proprietary distributions. At the maintainer's discretion, contributors may be granted commercial usage rights as a reward for their involvement in the project.

For enterprise deployment assistance, custom integrations, and security audits — commercial services are available.

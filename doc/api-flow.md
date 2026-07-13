# API Flow — HTTP Communication Between Services

This document describes all HTTP communication between microservices.
It serves as ground truth for cross-service dependency mapping.

## Service Map

```
demo-web (:8080)  — Python/FastAPI reverse proxy + static frontend
api-service (:8081) — Python/FastAPI LLM agent orchestrator
rag (:8082) — Python/FastAPI RAG pipeline (ChromaDB + embedding)
mcp-gateway (:8083) — Go/MCP protocol gateway (SSE + JSON-RPC)
data-service (:8084) — Go/chi generic CRUD proxy + config generation
admin-dashboard (:8085) — Go/chi admin web UI (Alpine.js)
```

## Communication Matrix

### 1. demo-web → data-service (direct proxy)

**Source:** `demo/web/server.py`
**Target:** `data-service:8084`

| HTTP Call | Route | Method | Purpose |
|---|---|---|---|
| `proxy_manifest()` | GET `/mcp/manifest` | HTTP | Fetch MCP tool manifest for tenant |
| `proxy_mapping()` | GET `/mcp/tools/mapping` | HTTP | Fetch display_name map for tenant tools |
| `proxy_data_entity()` | GET `/{entity}` | HTTP | Generic data entity lookup |
| `proxy_data_entity()` | GET `/{entity}/{id}` | HTTP | Entity by ID |
| `proxy_data_stats()` | GET `/stats` | HTTP | Data statistics |
| `get_tenants()` → `/health` | GET `/health` | HTTP | Discover registered tenants |

**Headers forwarded:** `X-Tenant-ID`, `X-Correlation-ID`, `Authorization`
**Config env:** `DATA_SERVICE_URL` (default: `http://127.0.0.1:8084`)

### 2. demo-web → api-service (SSE proxy)

**Source:** `demo/web/server.py`
**Target:** `api-service:8081`

| HTTP Call | Route | Method | Purpose |
|---|---|---|---|
| `proxy_chat()` | POST `/api/chat` | SSE stream | Chat streaming |
| `proxy_chat_by_agent()` | POST `/api/chat/{agent_name}` | SSE stream | Named agent chat |
| `proxy_health()` | GET `/health` | HTTP | API health check |
| `proxy_backlog()` | GET `/api/backlog` | HTTP | List backlog sessions |
| `proxy_backlog_detail()` | GET `/api/backlog/{session_id}` | HTTP | Backlog details |
| `proxy_session_history()` | GET `/api/session/history` | HTTP | Session history |
| `proxy_embed()` | GET `/embed/{path}` | HTTP | Embed widget static files |
| `proxy_api_any()` | ANY `/api/{path}` | HTTP/SSE | Catch-all proxy |

**Headers forwarded:** `X-Tenant-ID`, `X-Correlation-ID`, `Authorization`
**Config env:** `API_HOST` + `API_PORT` (builds `http://{host}:{port}`)

### 3. demo-web → rag (direct proxy)

**Source:** `demo/web/server.py`
**Target:** `rag:8082`

| HTTP Call | Route | Method | Purpose |
|---|---|---|---|
| `proxy_rag_documents()` | POST `/documents/list` | HTTP | List RAG documents |

**Config env:** `RAG_SERVICE_URL`

### 4. mcp-gateway → data-service (MCP tool backend)

**Source:** `mcp-gateway/internal/httpclient/client.go`
**Target:** `data-service:8084`

| HTTP Call | Route | Method | Purpose |
|---|---|---|---|
| `FetchConfigWithTenant(tenantID)` | GET `/mcp/manifest` | HTTP | Load tenant MCP tool config |
| `Call(ctx, endpoint, params)` | GET `/{endpoint}` | HTTP | Execute generic data query |
| `Call(ctx, endpoint, params)` | GET `/{endpoint}/{id}` | HTTP | Get entity by ID |
| `Call(ctx, endpoint, params)` | GET `/{endpoint}?search=...` | HTTP | Search entities |

**Auth:** `X-Tenant-ID` from context
**Config env:** `DATA_SERVICE_URL` (default: `http://127.0.0.1:8084`)
**SSRF Protection:** `ValidateURL()` rejects private IPs

### 5. api-service → mcp-gateway (display_name mapping)

**Source:** `api-service/src/api_service/agent/mcp_client.py` (`fetch_tool_mapping()`)
**Target:** `mcp-gateway:8083`

| HTTP Call | Route | Method | Purpose |
|---|---|---|---|
| `fetch_tool_mapping()` | GET `/mcp/tools/mapping` | HTTP | Get `{tool_name: display_name}` map |

**Auth:** `X-Tenant-ID` from context
**Used for:** SSE payload enrichment — `display_name` field in `tool_call` and `tool_result` events

### 6. api-service → mcp-gateway (MCP SSE + JSON-RPC)

**Source:** `api-service/src/api_service/agent/mcp_client.py`
**Target:** `mcp-gateway:8083`

| Step | Protocol | Purpose |
|---|---|---|
| GET `/mcp` | SSE stream | Open persistent SSE session |
| POST `/mcp/message?sessionId=...` | JSON-RPC | Send tool_call, receive via SSE |
| `event: endpoint` → `event: message` | SSE | Gateway publishes tool results |

**MCP Protocol flow:**
1. `sse_client()` opens GET `/mcp` → receives `event: endpoint` with `messageURL`
2. `ClientSession` sends JSON-RPC via POST `/mcp/message?sessionId=...`
3. Gateway responds `202 Accepted` immediately
4. Actual response arrives as `event: message` on the SSE stream

**Multi-tenancy:** Headers `{"X-Tenant-ID": "tenant-a,tenant-b"}` trigger composite mode
**Config env:** `MCP_SERVICE_URL`
**One persistent SSE session per tenant**, lock-serialized per tenant

### 7. api-service → rag (RAG context for agent)

**Source:** `helperium-sdk/src/helperium_sdk/rag/client.py`
**Target:** `rag:8082`

| HTTP Call | Route | Method | Purpose |
|---|---|---|---|
| `RagClient.get_context()` | POST `/context` | HTTP | Get RAG context for LLM prompt |
| `RagClient.search_documents()` | POST `/search` | HTTP | Search documents |
| `RagClient.list_documents()` | POST `/documents/list` | HTTP | List documents |
| `RagClient.delete_document()` | POST `/documents/delete` | HTTP | Delete document |

**Config env:** `RAG_SERVICE_URL`

### 8. admin-dashboard → data-service (admin API)

**Source:** `admin-dashboard/internal/server/server.go`
**Target:** `data-service:8084`

| HTTP Call | Route | Method | Purpose |
|---|---|---|---|
| `proxyGetToDataService()` | GET `/admin/tenants` | HTTP | List tenants |
| `proxyGetToDataService()` | GET `/admin/tenants/{id}` | HTTP | Get tenant |
| `proxyGetToDataService()` | GET `/admin/health` | HTTP | Health check |
| Various admin routes | Various `/admin/*` | HTTP | Tenant CRUD, rewrite, tools |

**Headers forwarded:** `X-Tenant-ID`, `Authorization: Bearer {admin_token}`
**Config field:** `Opts.DataServiceURL`

### 9. admin-dashboard → api-service (abuse + agent config)

**Source:** `admin-dashboard/internal/server/abuse.go`
**Target:** `api-service:8081`

| HTTP Call | Route | Method | Purpose |
|---|---|---|---|
| `proxyGetToApiService()` | GET `/api/agents/{name}` | HTTP | Get agent abuse config |
| `proxyPutToApiService()` | PUT `/api/agents/{name}` | HTTP | Update agent abuse config |
| `notifyApiServiceReload()` | POST `/admin/abuse-config/reload` | HTTP | Trigger abuse config reload |

**Config field:** `Opts.ApiSvcURL`

### 10. admin-dashboard → rag (admin config)

**Source:** `admin-dashboard/internal/server/server.go` (via `RagClient`)
**Target:** `rag:8082`

| HTTP Call | Route | Method | Purpose |
|---|---|---|---|
| RagClient health/config | GET `/admin/config` | HTTP | Get RAG config |
| RagClient stats | GET `/admin/stats` | HTTP | Get RAG statistics |
| RagClient update config | PUT `/admin/config` | HTTP | Update RAG config |

**Config field:** `Opts.RagSvcURL`

### 11. api-service → data-service (for future use)

**Source:** `helperium-sdk/src/helperium_sdk/data_client.py`
**Target:** `data-service:8084`

| HTTP Call | Route | Method | Purpose |
|---|---|---|---|
| `AsyncDataServiceClient.list_all()` | GET `/{entity}` | HTTP | List all entities |
| `AsyncDataServiceClient.get_by_id()` | GET `/{entity}/{id}` | HTTP | Get by ID |
| `DataServiceClientSync` (sync) | Same as above | HTTP | CLI tool access |

## Data Flow Summary

```
Browser
  │
  ├── GET / (static frontend) ──→ demo-web:8080
  │
  ├── POST /api/chat (SSE) ──→ demo-web ──→ api-service:8081
  │                                               │
  │                                               ├── SSE ──→ mcp-gateway:8083 ──→ data-service:8084
  │                                               │
  │                                               └── HTTP ──→ rag:8082
  │
  ├── GET /api/data/{entity} ──→ demo-web ──→ data-service:8084
  │
  └── GET /api/rag/documents ──→ demo-web ──→ rag:8082
```

## Configuration (env vars)

| Var | Default | Used By | Points To |
|---|---|---|---|
| `DATA_SERVICE_URL` | `http://127.0.0.1:8084` | demo-web, mcp-gateway | data-service |
| `RAG_SERVICE_URL` | `http://127.0.0.1:8082` | demo-web, api-service, admin-dashboard | rag |
| `API_HOST` + `API_PORT` | `0.0.0.0:8081` | demo-web | api-service |
| `MCP_SERVICE_URL` | `http://127.0.0.1:8083` | api-service | mcp-gateway |
| `ADMIN_DASHBOARD_DS_URL` | - | admin-dashboard | data-service |
| `ADMIN_DASHBOARD_AS_URL` | - | admin-dashboard | api-service |
| `ADMIN_DASHBOARD_RS_URL` | - | admin-dashboard | rag |

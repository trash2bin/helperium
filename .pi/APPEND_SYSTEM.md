# SYSTEM RULES — READ BEFORE EVERY RESPONSE

---

## 1. Decision tree — what to do first

```
Received a task
│
├── Is it about the codebase? (architecture, code, deps, flow)
│   ├── YES → go to §2 (Graph-first workflow)
│   └── NO  → is it a web/external question?
│               ├── YES → pi-web-search first, then answer
│               └── NO  → answer directly, no tools needed
│
├── Is the task non-trivial? (>5 files, >10 tool calls, parallel subtasks)
│   └── YES → go to §5 (Subagent delegation)
│
└── Is direction unclear or has the task drifted?
    └── YES → ask_user_ext before doing anything
```

---

## 2. Knowledge-graph-first workflow (codebase questions)

**The codebase-memory graph indexes 5234 nodes, 24614 edges (AST + cross-file calls).
Never read source files first — search the graph instead.**

**Limitations the agent must know:**
- Graph **partially** sees HTTP calls (54 `HTTP_CALLS` edges via Cypher: `MATCH (n)-[:HTTP_CALLS]->(m) RETURN ...`). **Misses:** `sse_client()`, dynamic URLs, some `httpx.get()`
- For full cross-service HTTP matrix, use `doc/api-flow.md`
- 12 client files have docstring annotations like: `FetchConfigWithTenant() -> data-service:GET /mcp/manifest`

### Step 1 — Search the graph
```
# BM25 full-text search (camelCase-aware, structural boosting)
codebase_memory_search_graph({ query: "MCPClient tenant", project: "helperium" })

# Exact name pattern search
codebase_memory_search_graph({ name_pattern: ".*createCompositeServer.*", project: "helperium" })
```

### Step 2 — Trace data flow (always get qualified_name from search_graph first)
```
# 2a. Find exact symbol
codebase_memory_search_graph({ query: "createCompositeServer", project: "helperium" })
# → qualified_name: "helperium.mcp-gateway.cmd.createCompositeServer"

# 2b. Trace with exact function_name
codebase_memory_trace_path({ function_name: "helperium.mcp-gateway.cmd.createCompositeServer",
  project: "helperium", direction: "both", mode: "calls", depth: 3 })
```

### Step 3 — Run Cypher queries for complex questions
```
codebase_memory_query_graph({ query: "MATCH (n)-[r]->(m) WHERE n.name CONTAINS 'MCP' RETURN n, r, m", project: "helperium" })
```

### Step 4 — Read files only if graph has no answer
Fall back to ctx_execute or read to inspect specific files.

### Step 5 — Never use raw Bash/Read for output >1 KB
Any shell command that produces significant output must go through ctx_execute or ctx_batch_execute.
Direct Bash/Read for large outputs burns context. ctx_batch_execute combines multiple calls in one.

---

## 3. Codebase-memory cookbook — concrete examples

### "What depends on class X? Who calls it?"
```
# Step 1: find exact qualified_name
codebase_memory_search_graph({ query: "ClassName", project: "helperium" })

# Step 2: pass qualified_name to trace_path (trace_path only finds Functions/Methods, not Classes)
codebase_memory_trace_path({ function_name: "helperium.project.path.ClassName",
  project: "helperium", direction: "inbound", mode: "calls", depth: 3 })
```

### "How does data flow from A to B?"
```
# Step 1: find exact qualified names
codebase_memory_search_graph({ query: "function_or_struct", project: "helperium" })

# Step 2: trace calls in either direction
codebase_memory_trace_path({ function_name: "qualified_name", project: "helperium", direction: "both", mode: "calls", depth: 4 })
```

### "What does module X export?"
```
# First find the qualified_name:
codebase_memory_search_graph({ query: "mcp_client", project: "helperium" })

# Then get code:
codebase_memory_get_code_snippet({ qualified_name: "helperium.api-service.src...MCPClient", project: "helperium", include_neighbors: true })
```

### "What breaks if I change class X?"
```
codebase_memory_search_graph({ query: "X", project: "helperium" })
// Then pass exact qualified_name from result:
codebase_memory_trace_path({ function_name: "helperium.path.X", project: "helperium", direction: "inbound", depth: 2 })
```

### "Where is feature Y implemented across the codebase?"
```
codebase_memory_search_code({ pattern: "feature Y keyword", project: "helperium" })
codebase_memory_search_graph({ query: "feature Y", project: "helperium" })
```

### "What changed and what could break?" (after edits)
```
codebase_memory_detect_changes({ scope: ".", project: "helperium" })
codebase_memory_search_graph({ query: "changed_file", project: "helperium" })
```

### "Give me an architecture overview"
```
codebase_memory_get_architecture({ project: "helperium", aspects: ["all"] })
// Returns: packages, services, routes, hotspots, layers, clusters, file tree
```

### "Search code by regex"
```
codebase_memory_search_code({ pattern: "tenant", project: "helperium", file_pattern: "*.go", limit: 10, regex: false })
```

### "How do services talk to each other?" (cross-service HTTP)
```
# Graph HAS HTTP_CALLS edges.
codebase_memory_query_graph({ query: "MATCH (n)-[:HTTP_CALLS]->(m) RETURN n.name, m.name", project: "helperium" })

# Or search code:
codebase_memory_search_code({ pattern: "httpx", project: "helperium", file_pattern: "*.py" })
codebase_memory_search_code({ pattern: "http.Get", project: "helperium", file_pattern: "*.go", regex: true })  # regex:true for pipes
```

### Known weak areas
- `.env` files and environment variables
- `scripts/dev.sh` and shell scripts
- **Dynamic HTTP calls** (`requests.post(url, data=json.dumps)`) — graph sees static-path HTTP calls, but misses runtime-constructed URLs and SSE/JSON-RPC flows, use `doc/api-flow.md` for full matrix
- Runtime config (not in the graph's static analysis)

---

## 4. Community hubs — fast navigation

When you know which subsystem you're in, use these as entry points for graph queries:

| Community | Entry concept | What it covers |
|---|---|---|
| `API Service Proxy` | `demo/web/server.py` | All reverse proxy routes (→ data, rag, api) |
| `MCP Client Session` | `mcp_client.py` | SSE session → mcp-gateway |
| `MCP Gateway Multi-Tenant` | `createCompositeServer()` | Composite mode, tenant routing |
| `MCP Server Core` | `main.go`, `sseSession` | SSE lifecycle, JSON-RPC handling |
| `HTTP Client Layer` | `httpclient/client.go` | mcp-gateway → data-service HTTP |
| `Data Service Client SDK` | `data_client.py` | Python SDK → data-service |
| `RAG Client Operations` | `rag/client.py` / `ragclient/client.go` | → rag: search, list, context |

### Backend services
| Community | Entry concept | What it covers |
|---|---|---|
| `CRUD HTTP Handlers` | `handlers/default.go` | Generic list/find/get_by_id/custom_query |
| `HTTP Middleware Stack` | `server/middleware.go` | TenantID, BodyLimit, Throttle, Recovery |
| `Agent Store CRUD` | `agent_store.py` | SQLite agent registry |
| `Tenant Storage Management` | `tenant.go` | Tenant lifecycle, config hot-reload |
| `SSE Event Formatting` | `event_stream.py` | AgentEvent → SSE format |
| `MCP Tools Manifest` | `tools/tools.go` | Tool registration, registry |

### RAG
| Community | Entry concept | What it covers |
|---|---|---|
| `RAG Service DTOs` | `http_models.py` | Request/response models |
| `RAG Service Endpoints` | `service.py` | FastAPI endpoints |
| `RAG TTL Cache` | `cache/` | Local + Redis cache |

### Admin / docs
| Community | Entry concept | What it covers |
|---|---|---|
| `doc/api-flow.md` | `doc/api-flow.md` | Full HTTP communication matrix (10 channels) |
| `Admin Dashboard` | `server.go`, `app.js` | Admin UI, Alpine.js frontend |
| `AGENTS.md` | `AGENTS.md` | Technical passport, data flow diagrams |

**Refresh:**
```
codebase_memory_index_repository({ repo_path: ".", name: "helperium", mode: "moderate" })
```

---

## 5. Subagent delegation

### Delegate when ANY of these is true:
- Task will touch >5 files or require >10 tool calls
- Independent review of completed work is needed
- Context is >50% full and task is not done
- Multiple subtasks can run in parallel independently

### Do NOT delegate:
- Single-file edits
- Quick lookups (use codebase-memory / ctx_search instead)
- Tasks under ~5 tool calls
- Anything where shared context is critical (mid-refactor state)

### Template selection:

| Situation | Template |
|---|---|
| Requirements are unclear | `/gather-context-and-clarify` |
| Need to research before implementing | `/parallel-research` or `/parallel-context-build` |
| Plan exists, need implementation | `/parallel-handoff-plan` |
| Changes done, need verification | `/parallel-review` |
| Iterative fix-and-check loop | `/review-loop` |
| Cleanup or refactor pass | `/parallel-cleanup` |

### Project-specific subagent: browser-debugger

Установлен project-scoped субагент для отладки UI/бэкенд-багов:
- **name**: `browser-debugger`
- **description**: Открывает страницу в живом браузере (Firefox), снимает ARIA snapshot, читает console/network ошибки, выдаёт структурированный bug report
- **tools**: `read`, `bash`, `mcp:playwright` (все 34 playwright-тула — navigate, snapshot, console, network, click, type и т.д.)
- **model**: наследуется от твоей (сейчас deepseek через polza)
- **thinking**: high
- **не правит код** — только диагностика

#### Когда использовать:
- Пользователь говорит "страница не грузится", "кнопка не работает", "API возвращает 500", "в консоли ошибка"
- Нужно проверить HTTP статус, ответ сервера, ошибки JS, упавшие network запросы
- Баг воспроизводится в браузере и нужно понять "что именно сломалось"

#### Когда НЕ использовать:
- Баг в мобильном приложении или не в браузере
- Нужно починить код — browser-debugger не пишет код
- Баг специфичен для Safari/Chrome и не воспроизводится в Firefox

#### Контекст:
**Контекст НЕ передаётся.** Каждый запуск browser-debugger стартует с чистым fresh-контекстом. Если нужно передать URL или детали — укажи их прямо в задаче.

#### Как вызывать:
```
Запусти browser-debugger — проверь http://localhost:5173/login, там пустой экран
Use browser-debugger to debug the 500 error on /api/users
browser-debugger, открой админку и проверь консоль на ошибки
```

#### Пример задачи с URL:
```
browser-debugger task="Open http://localhost:5173, check console for errors, check network for failed API calls, report findings"
```

#### Ограничения:
- Использует bundled Playwright Firefox, НЕ твой Zen Browser
- Если баг воспроизводится только в Zen (с твоими расширениями/куками) — browser-debugger может не увидеть его
- Для проверки на реальном Zen нужно отдельно настроить `--executable-path`
- **Важно:** после изменения конфига агента или MCP-сервера нужен restart Pi, чтобы кеш метаданных тулов обновился. Иначе дочерний процесс не увидит playwright-тулы.

### Default flow for non-trivial tasks:
```
1. codebase_memory_search_graph / trace_path → understand scope
2. If scope is large → /parallel-research to map unknowns
3. Build plan → /parallel-handoff-plan to implement
4. After implementation → /parallel-review to verify
5. If issues found → /review-loop until clean
```

---

## 6. Asking the user (ask_user_ext / pi-intercom)

Use `ask_user_ext` (not a Bash prompt, not a comment in code) when:
- The task goal has become ambiguous mid-session
- You are about to make a destructive or irreversible change
- You found two valid approaches with real tradeoffs the user should decide
- The original request conflicts with something discovered in the graph

**Do not ask** for information you can find via codebase-memory or ctx_search.
**Do not ask** to confirm obvious steps — only ask when a real decision is needed.

Use **pi-intercom** to pass structured context between sessions or subagents
when you need to preserve state that won't survive compaction. Prefer this
over writing to temp files for inter-agent communication.

---

## 7. Web search

Use `@ollama/pi-web-search` when:
- The question is about external APIs, libraries, or recent events
- Documentation for a dependency is needed
- The graph and codebase have no answer and the question is inherently external

Always search before answering external questions from memory —
library APIs change and your training data may be stale.

---

## Constraints

- **Never use raw Bash for output >1KB** — use ctx_execute or ctx_batch_execute
- **Never grep/glob for class references** — use codebase-memory instead

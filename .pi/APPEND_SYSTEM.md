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

## 2. Graph-first workflow (codebase questions)

**The graph covers 4890 nodes, 9035 edges across the entire project.
Never read source files first. The graph answers faster and costs zero tokens.**

**Limitations the agent must know:**
- Graph built via static AST analysis (tree-sitter) — **does NOT see HTTP calls** between services
- `httpx.get()`, `http.Get()`, `sse_client()` are invisible to the graph
- For cross-service HTTP queries, check `doc/api-flow.md` or the annotated docstrings in client files
- 12 client files have docstring annotations like: `FetchConfigWithTenant() -> data-service:GET /mcp/manifest`
- These are: mcp-gateway httpclient/tools/ragclient, admin-dashboard server/abuse/client, api-service server/mcp_client, helperium-sdk data_client/rag_client, demo-web server
- ~65 isolated nodes (files with 0 edges) — mostly normal: go.mod, package-main utils, READMEs

### Step 1 — Orient via index
Always start with ctx_search to locate relevant terms in the indexed GRAPH_REPORT.md:
```
ctx_search "ClassName dependency injection"
ctx_search "module import flow"
```

### Step 2 — Traverse via graphify tools
Use the result from Step 1 to pick the right graphify call. See §3 for the cookbook.

### Step 3 — Read files only if graph has no answer
If graphify tools return "node not found" or coverage is missing (see §3, Weak areas),
only then fall back to ctx_execute to read specific files.

### Step 4 — Never use raw Bash/Read for output >1 KB
Any shell command that produces significant output must go through ctx_execute or ctx_batch_execute.
Direct Bash/Read for large outputs burns context. ctx_batch_execute combines multiple calls in one.

---

## 3. Graphify cookbook — concrete examples

### "What depends on class X? Who calls it?"
Pi's instinct: open files, grep for imports.
**Do this instead:**
```
graphify_explain({ concept: "ClassName" })
// Returns: all nodes pointing TO it (callers/importers) and FROM it (dependencies)

graphify_query({
  question: "What calls ClassName and what does it depend on?",
  mode: "bfs"
})
```

### "How does data flow from A to B?"
Pi's instinct: trace imports manually across files.
**Do this instead:**
```
graphify_path({ from: "UserRequest", to: "ChromaDBVectorStore" })
// Returns: shortest dependency path between two concepts

graphify_query({
  question: "How does a user request reach ChromaDB?",
  mode: "dfs"   // dfs = follows the flow direction
})
```

### "What does module X import / what are its direct deps?"
```
graphify_explain({ concept: "mcp_client.py" })
// Returns: all edges — imports, calls, inherits
```

### "What breaks if I change class X?"
```
graphify_path({ from: "X", to: "entry_point_or_api_handler" })
// Shows the ripple path upward

graphify_explain({ concept: "X" })
// Shows all nodes that reference X — these are the risk surface
```

### "Where is feature Y implemented across the codebase?"
```
ctx_search "feature Y keyword"          // locate by text
graphify_query({
  question: "Where is Y implemented?",
  mode: "bfs"
})
```

### "What changed and what could break?" (after edits)
```
// 1. Update the graph first (free, no API cost)
graphify_update({ path: "." })

// 2. Check ripple effect from the changed node
graphify_explain({ concept: "changed_file_or_class" })
graphify_path({ from: "changed_file_or_class", to: "api_handler" })
```

### "Give me an architecture diagram"
```
graphify_export_callflow({})
// Outputs Mermaid diagram to graphify-out/callflow.html
// Open the file — don't read it into context
```

### "How do services talk to each other?" (cross-service HTTP)
```
// Graph does NOT see HTTP calls directly.
// Use the annotated docstring files + doc/api-flow.md instead:
graphify_explain({ concept: "doc/api-flow.md" })
// Or check specific client files:
graphify_explain({ concept: "demo/web/server.py" })     // proxy_manifest, proxy_chat, etc.
graphify_explain({ concept: "mcp_client.py" })           // SSE → mcp-gateway
graphify_explain({ concept: "ragclient/client.go" })      // → rag:POST /search, /context
graphify_explain({ concept: "httpclient/client.go" })     // → data-service:GET /mcp/manifest
```

### Known weak areas (use ctx_execute + doc/api-flow.md instead)
- `.env` files and environment variables
- `scripts/dev.sh` and shell scripts
- **HTTP calls between services** — graph is AST-only, use `doc/api-flow.md`
- Runtime config (not in the graph's static analysis)

---

## 4. Community hubs — fast navigation

When you know which subsystem you're in, use these as entry points for graphify queries:

| Community | Entry concept | What it covers |
|---|---|---|
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

**Graph freshness:** built from commit `988523f2`. Check:
```
ctx_execute("shell", `echo "HEAD: $(git rev-parse HEAD)"`)
// If diverged → graphify_update({ path: "." })  (AST-only, 0 tokens)
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
- Quick lookups (use ctx_search / graphify instead)
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
1. ctx_search + graphify_explain → understand scope
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

**Do not ask** for information you can find via ctx_search or graphify.
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

## 8. Saving useful answers to the graph

After a correct or insightful answer about the codebase:
```
graphify_save_result({ question: "...", answer: "...", correct: true })
```

Periodically aggregate saved results into lessons:
```
graphify_reflect({})
// Writes LESSONS.md — accumulated best practices for this project
```

This creates a feedback loop: the graph gets smarter with each session.

---

## Constraints

- **Never read `graphify-out/graph.json` or `graphify-out/GRAPH_REPORT.md` directly** — ~5MB / ~95KB, use tools
- **Never delete `graphify-out/`** — rebuilding costs API tokens
- **Never use raw Bash for output >1KB** — use ctx_execute or ctx_batch_execute
- **Never grep/glob for class references** — graphify_explain is faster and token-free

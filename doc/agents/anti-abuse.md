# Rate Limiting & Anti-Abuse

## mcp-gateway
- `mcpRateLimitMiddleware()` — per-IP лимит на POST
- MaxSessions = 1000, Idle timeout = 5m, Max lifetime = 30m

## api-service
- TokenBucket: per-сессия (`ABUSE_RPS`, `ABUSE_BURST`)
- UA-block: curl, wget, python-requests, Go-http-client
- Message limits: max 2000 chars, min 1s interval, 50 msg/session
- Repeated text: >3 повторов → блокировка
- Emergency presets: Normal / Cautious / Lockdown
- Prompt injection guard: `GuardChecker.check_input()`

## Search Strategy Abuse Prevention

LLM склонна вызывать инструменты с пустыми аргументами (`grep_products({})`), что приводит к дампу всей таблицы и перерасходу. Внедрены 3 уровня защиты:

### Уровень 1 — JSON Schema Validation (MCP Gateway)
- `grep_*` и `filter_*` тулы имеют `pattern` с `required: true` + `minLength: 1`
- MCP гейтвей отклоняет pre-request если `pattern` отсутствует или пустой → `isError: true`
- Реализуется через `Strategy.ToolParams()`, которая задаёт `Required: &t`

### Уровень 2 — Server-side guard (data-service)
- `grep.go`: `ParseRequest()` проверяет `pattern != ""` и `len(pattern) >= 1`, возвращает 400 при нарушении
- `grep.go`: `maxPatternLen=500`, `maxRegexLen=200`, `maxTokens=10` — защита от ReDoS
- `filter.go`: `maxFilterValueLen=200`, `maxInValues=50`, `parseFilterLimit=10`
- `Config.MCPTool` carries `Required: &t` — приходит через manifest в mcp-gateway и проверяется там

### Уровень 3 — Empty Hints (db_describe)
- При `total=0` grep/filter возвращают `empty_hint` с подсказкой: `"Try db_describe(entity=<entity>) to discover available values"`
- LLM видит подсказку и вызывает `db_describe` вместо циклических пустых попыток

### Security limits per strategy

| Strategy | Limits |
|----------|--------|
| `grep` (grep.go) | `maxPatternLen=500`, `maxRegexLen=200`, `maxTokens=10`, `maxFields=20` |
| `filter` (filter.go) | `maxFilterValueLen=200`, `maxInValues=50`, `maxFilters=15` |
| `schema` (schema.go) | нет — только discovery (read-only) |

### Logging
- `stages.py`: логгирует `name`, `arguments`, `iteration` до/после/при ошибке
- `mcp_client.py`: логгирует `[MCP] Calling tool X with args=Y`, результат `[MCP] Tool X completed: N blocks, M chars`
- `server.py`: SSE events `token`/`audio` только в DEBUG; `tool_call`/`tool_result`/`final`/`error`/`done` — INFO

**Детали:** `data-service/internal/search/`, `data-service/internal/configgen/mcp.go`, [search-strategies.md](search-strategies.md)
---
**Last verified:** 2026-08-02 (commit `3aa1cdbc172fd7b95140a36577eee78f87ec218d`) — после верификации были изменения (см. AGENTS.md §Verification)

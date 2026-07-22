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

LLM склонна вызывать инструменты с пустыми аргументами (`search_auto_parts({})`), что приводит к дампу всей таблицы и перерасходу. Внедрены 3 уровня защиты:

### Уровень 1 — JSON Schema Validation (MCP Gateway)
- `search_*` тулы имеют `pattern` с `required: true` + `minLength: 1`
- MCP гейтвей отклоняет pre-request если `pattern` отсутствует или пустой → `isError: true`
- Реализуется через `Strategy.ToolParams()`, которая задаёт `Required: &t`

### Уровень 2 — Server-side guard (data-service)
- `search.go`: `ParseRequest()` проверяет `pattern != ""` и `len(pattern) >= 1`, возвращает 400 при нарушении
- `search.go`: `maxFilters=15`, `maxTotalConditions=25` — защита от ReDoS/token flood
- `filter.go`: `parseFilterLimit` default `10`
- `Config.MCPTool` carries `Required: &t` — приходит через manifest в mcp-gateway и проверяется там

### Уровень 3 — LLM Prompt Engineering
- `llm.go`: hints описывают эффективный воркфлоу `distinct → count → search`
- `llm.go`: explicit примеры `search_auto_parts(pattern='oil filter')`
- `_build_tool_result` (api-service): error message содержит конкретный пример вызова
- `llm.go` hints не содержат relationship tools (`products_by_category`) — они убраны из манифеста

### Filtering старых/relationship тулов
`mcp.go:GenerateMCPTools()` строит `hasStrategy` map. Если entity входит в strategy:
- `find_*` не генерируется (вместо него `search_*`)
- `list_*` не генерируется (вместо него `search_*`)
- `products_by_category` и прочие relationship тулы не генерируются

### Security limits per strategy

| Strategy | Limits |
|----------|--------|
| `search` (search.go) | `maxFilters=15`, `maxTotalConditions=25`, `pattern minLength=1` |
| `grep` (grep.go) | `maxFilterValueLen`, `maxRegexLen` (default 200), `maxValues` for `field__in` (default 100) |
| `filter` (filter.go) | `parseFilterLimit` default 10 |
| `simple` (simple.go) | — |

### Logging
- `stages.py`: логгирует `name`, `arguments`, `iteration` до/после/при ошибке
- `mcp_client.py`: логгирует `[MCP] Calling tool X with args=Y`, результат `[MCP] Tool X completed: N blocks, M chars`
- `server.py`: SSE events `token`/`audio` только в DEBUG; `tool_call`/`tool_result`/`final`/`error`/`done` — INFO

**Детали:** `data-service/internal/search/`, `data-service/internal/configgen/mcp.go`, [search-strategies.md](search-strategies.md)

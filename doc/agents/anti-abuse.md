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

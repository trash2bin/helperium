# Monitoring & Observability

Все сервисы отдают Prometheus-метрики на `/metrics`:

| Сервис | Порт | Ключевые метрики |
|---|---|---|
| **data-service** | :8084 | `data_requests_total`, `data_request_duration_ms` |
| **mcp-gateway** | :8083 | `mcp_tool_calls_total`, `mcp_sessions_active`, `mcp_rate_limit_hits_total` |
| **admin-dashboard** | :8085 | `admin_requests_total` |
| **api-service** | :8081 | `chat_sessions_total`, `chat_messages_total`, `llm_calls_total`, `llm_duration_ms`, `llm_token_usage`, `llm_cost_total`, `abuse_blocked_total`, `backlog_*` |

## Docker monitoring profile

```bash
docker compose --profile monitoring up -d
# Prometheus: http://localhost:9090
# Grafana: http://localhost:3000 (admin/admin)
```

Grafana дашборд (18 панелей): `docker/grafana/dashboards/helperium-overview.json`

## Logging

- **api-service**: structlog, JSON-логи (`LOG_FORMAT=json`)
- **data-service / mcp-gateway / admin-dashboard**: slog, structured JSON
- Все поддерживают `LOG_LEVEL`

## Admin dashboard дополнения (v1.1.0)

- Anti-Abuse tab
- Emergency Big Red Button (Normal → Cautious → Lockdown)
- i18n: RU/EN (309 ключей), language switcher
- `LOG_LEVEL=debug` для трассировки

# Мониторинг Helperium — Distributed Tracing & Logging

## Архитектура

```
┌─────────────────┐     OTLP HTTP (4318)      ┌──────────────────┐
│  Python Services│ ────────────────────────→ │                  │
│  (web, api, rag)│                           │  otel-collector  │
└─────────────────┘                           │                  │
                                              │  batch processor │
┌──────────────────┐     OTLP HTTP (4318)     │  100ms / 512     │
│  Go Services     │ ────────────────────────→│                  │
│  (data, mcp, adm)│                          └──────┬───────────┘
└──────────────────┘                                 │
                                                     │ OTLP HTTP
                                                     │ tempo:4318
                                                     ▼
                                                ┌──────────────────┐
                                                │  Grafana Tempo   │
                                                │  (traces store)  │
                                                │  :3200 (HTTP)    │
                                                └──────────────────┘

┌─────────────────┐     Prometheus (:9090)     ┌───────────────────┐
│  All Services   │ ────────────────────────→  │  Prometheus       │
│  /metrics       │                            │  (metrics store)  │
└─────────────────┘                            └───────┬───────────┘
                                                       │
                                                       ▼
┌─────────────────┐                             ┌──────────────────┐
│  .data/logs/*   │ ─→ Promtail (:9080) ──→     │  Loki            │
│  Docker logs    │                             │  (logs store)    │
└─────────────────┘                             │  :3100           │
                                                └──────────────────┘
                                                       │
                                                       ▼
                                                ┌──────────────────┐
                                                │  Grafana         │
                                                │  :3000           │
                                                │  admin/admin     │
                                                └──────────────────┘
```

## Быстрый старт

```bash
# 1. Поднять всё (7 core-сервисов + инфраструктура)
./scripts/stack.sh up

# 2. Проверить статус
./scripts/stack.sh check

# 3. Или только инфраструктуру (если сервисы уже запущены)
docker compose --profile monitoring --profile tracing --profile logging up -d
```

## Grafana

- **URL**: http://127.0.0.1:3000
- **Login**: `admin` / `admin`
- **Дашборд**: 🔍 Helperium — Full Monitoring (18 панелей)

### Разделы дашборда

| Раздел | Панели | Описание |
|---|---|---|
| 🟢 Service Health | 6 | Health check всех сервисов |
| 📡 Data Service | 4 | req/s, p99 latency, error rate, DB query duration |
| 🔌 MCP Gateway | 4 | tool calls, SSE sessions, rate limit, errors |
| 🧠 API — LLM & Chat | 8 | LLM calls, tokens, cost, abuse blocks, backlog |
| 📄 RAG Service | 8 | documents, ChromaDB, search rate, cache, imports |
| ⚙️ Admin Dashboard | 2 | request rate, error rate |

### Explore (Tempo)

```
# Найти трейсы по сервису
В Grafana → Explore → Tempo → Search:
  Service Name: helperium-data-service

# TraceQL запрос напрямую
{ .service.name = "helperium-mcp-gateway" }

# По trace ID (из логов)
<trace ID> → Explore → Tempo → вставить в поле
```

### Explore (Loki)

```
# Все логи
{job="native-services"}

# Только ошибки
{job="native-services"} |= "ERROR"

# По trace ID (автоматический переход в Tempo из лога)
{job="native-services"} |= "trace_id": "abc
```

**Derived fields**: В Loki настроен парсинг `"trace_id":"([a-f0-9]{32})"` — клик по TraceID в логе открывает этот трейс в Tempo.

_Примечание:_ `trace_id` автоматически инжектится в логи Go сервисов (slog)
и Python сервисов (structlog через log_config). Если `trace_id` пустой —
значит `OTEL_ENABLED=false` или запрос пришёл без активного span.

## Сервисы и идентификация в трейсах

| Сервис | Язык | Имя в Tempo | OTel endpoint |
|---|---|---|---|
| Web | Python | `helperium-demo-web` | `OTEL_EXPORTER_OTLP_ENDPOINT` (default: `http://localhost:4318`) |
| API | Python | `helperium-api-service` | тот же |
| RAG | Python | `helperium-rag-service` | тот же |
| Data | Go | `helperium-data-service` | env `OTEL_EXPORTER_OTLP_ENDPOINT` |
| MCP | Go | `helperium-mcp-gateway` | env `OTEL_EXPORTER_OTLP_ENDPOINT` |
| Admin | Go | `helperium-admin-dashboard` | env `OTEL_EXPORTER_OTLP_ENDPOINT` |

## Cross-service trace propagation

**traceparent header** пропагируется автоматически:

- **Python → Python**: `HTTPXClientInstrumentor` в `helperium_sdk.tracing` автоматически добавляет `traceparent` на исходящие HTTPX запросы
- **Python → Go**: Web прокси прокидывает все заголовки через `_get_proxy_headers()` → Go `tracing.Middleware` считывает `traceparent` и создаёт child span
- **Go → Go**: `tracing.Middleware` прокидывает контекст через HTTP-клиент в data-service

**Correlation ID** (`X-Correlation-ID`) остаётся для обратной совместимости и тоже прокидывается.

## Переменные окружения

| Переменная | По умолчанию | Описание |
|---|---|---|
| `OTEL_ENABLED` | `true` | Отключить tracing (`false`) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4318` | OTLP HTTP endpoint |
| `OTEL_SERVICE_NAME` | `helperium-{service_name}` | Имя сервиса в Tempo |

## Graceful degradation

- Если `otel-collector` не запущен, сервисы продолжают работу — ошибки логируются как `WARNING`
- `OTEL_ENABLED=false` полностью отключает OpenTelemetry
- Отсутствующие пакеты (`opentelemetry-*`) не ломают сервис — `ImportError` обрабатывается

## Port map

| Порт | Сервис | Назначение |
|---|---|---|
| 3000 | Grafana | UI |
| 3100 | Loki | Log storage |
| 3200 | Tempo | Trace storage (HTTP API) |
| 4317 | Tempo | OTLP gRPC (внутренний) |
| 4318 | otel-collector | OTLP HTTP приём |
| 9090 | Prometheus | Metrics storage |
| 9095 | Tempo | gRPC API (внутренний) |

## Colima troubleshooting

```bash
# Если Docker daemon не отвечает
colima stop && colima start

# Если порт 4318 занят SSH форвардингом после рестарта
# НЕ убивать PID — это colima forwarding, перезапустить colima
colima stop && colima start

# После рестарта — пересоздать сеть
docker network rm helperium_helperium-net 2>/dev/null || true
docker-compose up -d

# Проверить что контейнеры на одной сети
docker network inspect helperium_helperium-net
```

## Тестирование

```bash
# Проверить что трейсы доходят до Tempo
curl -s 'http://127.0.0.1:3200/api/search?q={}&limit=10'

# Prometheus targets
curl -s 'http://127.0.0.1:9090/api/v1/targets'

# Loki
curl -s 'http://127.0.0.1:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={job="native-services"}' \
  --data-urlencode 'limit=5'
```

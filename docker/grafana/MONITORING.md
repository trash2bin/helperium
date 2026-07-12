# 📊 Мониторинг Helperium — полное руководство

## Что это и зачем

В проекте три уровня observability:

1. **Structured logging** — каждый сервис пишет JSON-логи (slog / structlog)
2. **Prometheus /metrics** — каждый сервис отдаёт числовые счётчики и гистограммы
3. **Grafana дашборд** — визуализация метрик с предустановленными панелями

Уровни 2+3 — это **дополнительный слой**, который **не обязателен** для работы сервисов.
Сервисы запускаются и работают без Prometheus и Grafana. Мониторинг поднимается отдельно.

---

## Быстрый старт

### Предусловия

Сервисы должны быть запущены нативно через dev.sh (или Docker, но ниже описан нативный сценарий).

```bash
./scripts/dev.sh start
```

### Запуск мониторинга

```bash
docker compose --profile monitoring up -d
```

**Что поднимется:**
| Сервис | Порт | Логин |
|---|---|---|
| Grafana | http://127.0.0.1:3000 | admin / admin (скипнуть смену пароля) |
| Prometheus | http://127.0.0.1:9090/targets | — |

### Остановка

```bash
docker compose stop prometheus grafana
# или полностью:
docker compose down
```

---

## Архитектура

```
┌─────────────────────────────────────────────────────────────────┐
│                        Docker (colima)                          │
│                                                                 │
│  ┌──────────────┐      ┌────────────────┐                       │
│  │  Prometheus  │      │    Grafana     │                       │
│  │  :9090       │◄─────│  :3000         │                       │
│  │              │      │                │                       │
│  └──────┬───────┘      └────────────────┘                       │
│         │                                                       │
│         │  scrape via host.docker.internal                      │
│         ▼                                                       │
│  ┌──────────────────────────────────────────────────┐           │
│  │           Нативные сервисы (dev.sh)              │           │
│  │                                                  │           │
│  │  api-service     mcp-gateway    data-service     │           │
│  │  :8081/metrics   :8083/metrics  :8084/metrics    │           │
│  │                  admin-dashboard                 │           │
│  │                  :8085/metrics                   │           │
│  └──────────────────────────────────────────────────┘           │
└─────────────────────────────────────────────────────────────────┘
```

**Ключевой момент:** Prometheus scraпит сервисы через `host.docker.internal`,
потому что сервисы запущены **нативно** (вне Docker). Если бы сервисы тоже
были в Docker, таргеты были бы `data-service:8084` и т.д.

---

## Файловая структура

```
docker/
├── prometheus/
│   └── prometheus.yml              ← какие таргеты scraпить
│
└── grafana/
    ├── datasources/
    │   └── datasource.yml          ← откуда Grafana берёт данные
    ├── dashboards/
    │   ├── dashboard.yml           ← provisioning — автоподгрузка дашбордов
    │   └── helperium-overview.json  ← сам дашборд (12 панелей)
    └── MONITORING.md               ← этот файл
```

docker-compose.yml — секции `prometheus` и `grafana` в profile: monitoring.

---

## Какие метрики собираются

> `curl http://localhost:8081/metrics | grep -E '^# HELP|^[a-z]'` — увидит все метрики живьём

### data-service (:8084)

| Метрика | Тип | Labels | Когда растёт |
|---|---|---|---|
| `data_requests_total` | Counter | `entity`, `operation`, `status` | Каждый HTTP-запрос |
| `data_request_duration_ms` | Histogram | `entity`, `operation` | Каждый HTTP-запрос |
| `data_db_query_duration_ms` | Histogram | `tenant` | Каждый SQL-запрос к БД |

### mcp-gateway (:8083)

| Метрика | Тип | Labels | Когда растёт |
|---|---|---|---|
| `mcp_tool_calls_total` | Counter | `tool`, `tenant`, `status` | Каждый вызов MCP-инструмента |
| `mcp_sessions_active` | Gauge | `tenant` | Текущее количество SSE-сессий |
| `mcp_rate_limit_hits_total` | Counter | `tenant` | Каждый заблокированный rate-limit'ом запрос |

### api-service (:8081)

| Метрика | Тип | Labels | Когда растёт |
|---|---|---|---|
| `chat_sessions_total` | Counter | — | Каждая новая сессия чата |
| `chat_messages_total` | Counter | `status` | Каждое отправленное сообщение |
| `llm_calls_total` | Counter | `model`, `provider` | Каждый LLM-вызов |
| `llm_duration_ms` | Histogram | `model` | Каждый LLM-вызов |
| `llm_token_usage` | Counter | `type` (prompt/completion/total) | Каждый успешный LLM-ответ |
| `llm_cost_total` | Counter | — | Каждый успешный LLM-ответ (аппроксимация) |
| `abuse_blocked_total` | Counter | `reason` | Каждый заблокированный anti-abuse запрос |
| `embed_widget_requests_total` | Counter | `endpoint` | Каждый запрос к /embed/* |
| `backlog_records_total` | Counter | `type` | Каждая запись в backlog |
| `backlog_errors_total` | Counter | `error_type` | Каждая ошибка в backlog |

### admin-dashboard (:8085)

| Метрика | Тип | Labels | Когда растёт |
|---|---|---|---|
| `admin_requests_total` | Counter | `path`, `status` | Каждый HTTP-запрос к admin API |
| `admin_abuse_config_changes_total` | Counter | `scope` | Каждое изменение anti-abuse конфига |

---

## PromQL для дашборда

Дашборд `helperium-overview.json` содержит 12 панелей. PromQL-запросы:

### Data Service
- **Request Rate**: `rate(data_requests_total[1m])` — запросов/сек
- **p95 Duration**: `histogram_quantile(0.95, sum(rate(data_request_duration_ms_bucket[5m])) by (le))`
- **Avg DB Query Duration**: `rate(data_db_query_duration_ms_sum[1m]) / rate(data_db_query_duration_ms_count[1m])`

### MCP Gateway
- **Tool Calls**: `rate(mcp_tool_calls_total[1m])`
- **Active SSE Sessions**: `sum(mcp_sessions_active)`
- **Rate Limit Hits**: `rate(mcp_rate_limit_hits_total[1m])`

### API Service
- **LLM Calls**: `rate(llm_calls_total[1m])`
- **Avg LLM Duration**: `rate(llm_duration_ms_sum[1m]) / rate(llm_duration_ms_count[1m])`
- **Token Usage Rate**: `rate(llm_token_usage_total[1m])`
- **Abuse Blocks**: `rate(abuse_blocked_total[1m])`
- **LLM Cost**: `rate(llm_cost_total[1m])`

### Admin Dashboard
- **Requests**: `rate(admin_requests_total[1m])`

---

## Как это редактировать

### Добавить панель в дашборд

1. Открыть `docker/grafana/dashboards/helperium-overview.json`
2. Добавить объект в массив `panels[]`
3. `gridPos` — расположение на сетке (12 колонок, каждая строка 8h)
4. `targets[].expr` — PromQL-запрос
5. Рестартнуть Grafana: `docker compose restart grafana`

### Добавить новую метрику в сервис

**Go (data-service / mcp-gateway / admin-dashboard):**
1. Определить в `helperium-go/pkg/metrics/metrics.go`: `prometheus.NewCounterVec(...)`
2. Зарегистрировать в `RegisterMetrics()`
3. Вызвать `.WithLabelValues(...).Inc()` / `.Observe()` в нужном месте

**Python (api-service):**
1. Определить в `api-service/src/api_service/prometheus_metrics.py`
2. Импортировать и вызывать `.inc()` / `.observe()` в нужном месте
3. Метрика автоматически появится на `/metrics` (prometheus_client)

### Поменять Prometheus-конфиг

`docker/prometheus/prometheus.yml` — добавить/убрать таргеты, сменить интервал:

```yaml
scrape_configs:
  - job_name: 'data-service'
    metrics_path: '/metrics'
    params:
      tenant: ['default']           # data-service требует tenant
    static_configs:
      - targets: ['host.docker.internal:8084']
```

---

## Диагностика: "No Data" на панели

### Prometheus не видит сервис
1. http://127.0.0.1:9090/targets — проверить статус (UP / DOWN)
2. Если DOWN: сервис не запущен или порт другой
3. Если UP но No Data: метрика никогда не вызывалась (нужно что-то сделать в сервисе)

### Метрика не растёт
```bash
# Проверить что метрика есть в /metrics
curl -s http://127.0.0.1:8081/metrics | grep -E '^[a-z]'

# Проверить что значение >0
curl -s http://127.0.0.1:8084/metrics?tenant=default | grep data_requests_total

# В Prometheus: выполнить запрос вручную
# http://127.0.0.1:9090/graph?g0.expr=rate(data_requests_total[1m])
```

### Панель с No data — это нормально
- `mcp_sessions_active` — пока нет активных SSE-сессий
- `mcp_rate_limit_hits_total` — пока не было rate-limit хитов
- `llm_*` — пока не было LLM-вызовов
- `abuse_blocked_total` — пока не было abuse-блокировок
- `admin_abuse_config_changes_total` — пока не меняли конфиг

Просто открой чат и позадавай вопросы — метрики появятся.



## Советы

- **Prometheus UI** http://127.0.0.1:9090/graph — можно вводить любые PromQL-запросы
- **Сбросить данные Prometheus**: `docker compose down && docker volume rm helperium_prometheus_data`

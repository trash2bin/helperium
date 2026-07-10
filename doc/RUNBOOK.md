# Runbook: второй деплой за часы

Внутренняя шпаргалка для разворачивания стека у нового клиента.
Не для клиента — для себя. Актуально: июль 2026.

## 0. Что нужно от клиента

- [ ] Доступ к PostgreSQL / MySQL (хост, порт, юзер, пароль, база)
- [ ] Домен (для prod-режима с HTTPS)
- [ ] API-ключ к LLM провайдеру (OpenAI / Anthropic / Mistral), если не Ollama
- [ ] Документы для RAG (PDF, DOCX, TXT — хоть что)
- [ ] Куда встроить виджет (страница сайта, `<body>`)

## 1. Сервер + Docker

```bash
# Сервер: Ubuntu 22.04+, Docker, git
ssh root@client-server
apt install docker.io docker-compose-v2
git clone https://github.com/ivan-proger/agent-tutor.git
cd agent-tutor

# Создать data-директории (они бинд-маунтятся в контейнеры)
mkdir -p .data/{app,rag,hf_cache,uploads,pg}

# Скопировать конфиг
cp .env.example .env
```

## 2. Конфиг — обязательные переменные

Минимальный набор для старта нового клиента:

```bash
# Режим: prod, если HTTPS нужен; иначе dev
# dev:  docker compose up -d
# prod: docker compose --profile prod up -d

# ── Data source ──────────────────────────────────────────────────
DB_DRIVER=postgres
DATABASE_URL=postgres://user:pass@host:5432/dbname?sslmode=require
# Для SQLite (тесты / мелкие клиенты):
# DB_DRIVER=sqlite
# DB_PATH=/data/app/university.db

# ── LLM ──────────────────────────────────────────────────────────
# Вариант 1: Ollama (локально, качает модель)
OLLAMA_URL=http://host.docker.internal:11434
OLLAMA_MODEL=qwen2.5:0.5b

# Вариант 2: Mistral / OpenAI / Anthropic
MISTRAL_API_KEY=sk-...
MISTRAL_MODEL=mistral/mistral-small
# OPENAI_API_KEY=sk-...     # если OPENAI_API_KEY задан, llm_client использует его
# ANTHROPIC_API_KEY=sk-...  # или Anthropic

# ── Tenants ────────────────────���─────────────────────────────────
DEFAULT_TENANT_ID=client-name
DEMO_TENANTS=client-name    # comma-separated, если несколько

# ── Domain (только для prod) ────────────────────────────────────
DOMAIN=chat.client.com
# После: Caddy автоматом получит Let's Encrypt сертификат

# ── Anti-Abuse (дефолты безопасны, но можно зажать) ────────────
ABUSE_RPS=1.0              # Token bucket refill rate (requests/second)
ABUSE_BURST=5              # Token bucket burst capacity
ABUSE_MESSAGE_MAX_LENGTH=2000  # Макс. длина сообщения (символов)
ABUSE_MIN_INTERVAL=1.0     # Мин. интервал между сообщениями (сек)
ABUSE_SESSION_BUDGET=50    # Макс. сообщений за сессию
ABUSE_REPEATED_THRESHOLD=3 # Порог повторяющегося текста (раз)

# ── Logging ─────────────────────────────────────────────────────
LOG_FORMAT=text            # Формат логов: json или text
LOG_LEVEL=info             # Уровень: debug, info, warn, error

# ── Monitoring ──────────────────────────────────────────────────
# Метрики включены по умолчанию на всех сервисах — /metrics
```

Остальные ~170 переменных — дефолты работают. Править только под клиента.

## 3. Старт и проверка здоровья

```bash
# Dev
docker compose up -d
# Prod
docker compose --profile prod up -d

# Ждём 120s (RAG качает embedding-модель при первом старте)
docker compose logs rag --tail 20

# Проверка здоровья
docker compose ps
# Все 6 сервисов (db, data, rag, mcp, api, web) + admin + caddy

curl http://localhost:8080/                               # → 200, web
curl http://localhost:8084/health                          # → {"status":"ok"}
curl http://localhost:8082/health                          # → {"status":"ok"}
curl http://localhost:8081/health                          # → {"status":"ok"}

### Проверка метрик (v1.1.0)
```bash
curl http://localhost:8084/metrics?tenant=default | head -20   # data-service
curl http://localhost:8083/metrics | grep mcp_                  # mcp-gateway
curl http://localhost:8081/metrics | grep llm_                  # api-service
curl http://localhost:8085/metrics | head -5                    # admin-dashboard
```

## 3.5. Monitoring Stack (Prometheus + Grafana)

Стек мониторинга запускается поверх работающих сервисов через Docker profile:

```bash
docker compose --profile monitoring up -d
# Prometheus: http://localhost:9090
# Grafana:    http://localhost:3000 (admin / admin)
```

**Что мониторится:**

| Сервис | Порт | Метрики |
|---|---|---|
| **data-service** | :8084 | `data_requests_total`, `data_request_duration_ms` |
| **mcp-gateway** | :8083 | `mcp_tool_calls_total`, `mcp_sessions_active`, `mcp_rate_limit_hits_total` |
| **admin-dashboard** | :8085 | `admin_requests_total` |
| **api-service** | :8081 | `chat_sessions_total`, `chat_messages_total`, `llm_calls_total`, `llm_duration_ms`, `llm_token_usage`, `llm_cost_total`, `abuse_blocked_total`, `backlog_*` |

**Grafana дашборд** (12 панелей): `docker/grafana/dashboards/agent-tutor-overview.json`
- Общая сводка (chat sessions, active sessions)
- LLM метрики (calls, tokens, cost, duration)
- Data-service метрики (requests, latency)
- MCP метрики (tool calls, active sessions)
- Admin dashboard метрики
- Anti-abuse блокировки

**Нативный запуск (без Docker):** каждый сервис отдаёт `/metrics` независимо — можно скрапить любым Prometheus-экспортером.

## 4. Тенант + данные

```bash
# Зарегистрировать тенанта
uv run agent-db tenant register client-name

# Создать seed-данные (дисциплины, группы, студенты, расписание)
# из SQL-схемы клиента)
# Если у клиента своя БД — показывает таблицы через интроспекцию
curl http://localhost:8084/admin/introspect?tenant=client-name

# Импорт RAG-документов
# Через admin-dashboard: http://localhost:8085/rag
# Или через CLI:
uv run agent-rag-ingest import /path/to/client/doc.pdf -d client-name -t "Договор"
```

> Анти-спам включён по умолчанию — админ может отключить/настроить через **Anti-Abuse** вкладку в admin-dashboard.

## 5. Агент — настройка

Admin-dashboard: `http://localhost:8085`

1. **Tenants** — проверить, что client-name создан
2. **Config** — проверить LLM провайдер (если не Mistral по дефолту)
3. **Tools** — утвердить write-тулы (по умолчанию выключены)
4. **Agents** — создать агента, system prompt
5. **RAG** — загрузить документы, проверить поиск
6. **Monitoring** (новое, v1.1.0):
   - **Anti-Abuse** — настройки abuse engine: RPS, burst, session budget, интервал, детекция повторов
   - **Emergency Presets** — Big Red Button: Normal → Cautious → Lockdown одним кликом
   - Логи в JSON (`LOG_FORMAT=json`) для интеграции с системами сбора логов

## 6. Виджет — встройка на сайт клиента

```html
<script src="https://chat.client.com/embed/embed.js"
        data-agent="assistant"
        data-title="Помощник"
        data-accent="#0f766e"
        data-position="right"
        data-api-base="https://chat.client.com">
</script>
```

Вставить в `<body>` на сайте клиента. Никаких зависимостей. Shadow DOM — CSS сайта не ломается.

## 7. Проверка перед сдачей

```bash
# 1. Мультитенантная изоляция
uv run agent-db e2e-data
uv run agent-db e2e-mcp
uv run agent-db e2e-full

# 2. Пишем чат-сообщение через web (http://localhost:8080)
#    Проверить: стриминг, tool calling, ссылки на документы

# 3. Проверить, что write-тулы выключены (если не утверждены)
curl http://localhost:8084/admin/tools/pending
# → список ожидающих подтверждения

# 4. Логи — нет ошибок
docker compose logs --tail 100 2>&1 | grep -i error
```

## 8. Если что-то пошло не так

```bash
# Посмотреть логи конкр��тного сервиса
docker compose logs api --tail 50
docker compose logs rag --tail 50

# Перезапустить сервис без пересборки всего стека
docker compose restart api

# Сбросить RAG-индекс (удалить chroma_db, перезапустить, переимпортировать)
docker compose stop rag
rm -rf .data/rag/chroma_db
docker compose up -d rag

# Сбросить всего тенанта
uv run agent-db tenant delete client-name
# заново с шага 4
```

## 9. Production-режим (HTTPS)

```bash
docker compose --profile prod up -d
# Caddy автоматом:
# - получает сертификаты Let's Encrypt
# - проксирует web:8080 → :443
# - редиректит :80 → :443

# Логи Caddy
docker compose logs caddy -f
```

## Краткая памятка

```
1. git clone + mkdir -p .data/{app,rag,hf_cache,uploads,pg} + cp .env.example .env
2. Правим .env: DATABASE_URL, LLM ключ, DEFAULT_TENANT_ID
3. docker compose up -d
4. uv run agent-db tenant register client-name
5. Admin-dashboard: загрузить RAG, создать агента, утвердить тулы
6. Виджет: <script src="/embed/embed.js" data-agent="assistant">
7. uv run agent-db e2e-full
8. Monitoring: docker compose --profile monitoring up -d (Grafana :3000)
```

---

## 10. Переменные окружения — новые (v1.1.0)

```bash
# ── Anti-Abuse ──────────────────────────────────────────────────
ABUSE_RPS=1.0           # Token bucket refill rate (requests/second)
ABUSE_BURST=5           # Token bucket burst capacity
ABUSE_MESSAGE_MAX_LENGTH=2000  # Max message length (chars)
ABUSE_MIN_INTERVAL=1.0  # Min interval between messages (seconds)
ABUSE_SESSION_BUDGET=50 # Max messages per session
ABUSE_REPEATED_THRESHOLD=3  # Repeated text detection (times)

# ── Logging ──────────────────────────────────────────────────────
LOG_FORMAT=json         # Log format: json or text (api-service structlog)
LOG_LEVEL=info          # Log level: debug, info, warn, error

# ── Monitoring ───────────────────────────────────────────────────
ENABLE_METRICS=true     # Enable /metrics endpoint on all services (always on)
```

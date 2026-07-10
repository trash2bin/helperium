# API Service (demo-api)

Оркестратор LLM-агента с MCP-интеграцией, управлением сессиями и бэклогом.

## Роль в системе

`demo-api` — единственный компонент, который общается с LLM (через LiteLLM). Он:
- Формирует системный промпт + Persona агента
- Управляет MCP-клиентом (подключение к mcp-gateway:8083)
- Хранит историю диалогов (SQLite: `demo_sessions.sqlite`)
- Пишет полный бэклог взаимодействий (JSONL в `backlog/`)
- Проксирует SSE-стрим от агента к Web

## Эндпоинты

| Путь | Метод | Описание |
|---|---|---|
| `/health` | GET | Статус сервиса |
| `/api/chat` | POST | SSE-стрим чата с агентом (требует `X-Tenant-ID`) |
| `/api/chat/{name}` | POST | SSE-чат с именованным агентом (tenant_ids из Agent Store) |
| `/api/sessions` | GET | Список сессий |
| `/api/sessions/{id}` | GET | История конкретной сессии |
| `/api/backlog` | GET | Список бэклогов |
| `/api/backlog/{id}` | GET | Детали бэклога |
| `/api/backlog/stats/{session_id}` | GET | Статистика сессии (токены, cost, ошибки) |
| `/api/backlog/errors` | GET | Последние ошибки чата |
| `/metrics` | GET | Prometheus метрики (мониторинг) |
| `/api/agents` | POST | Создать агента (Agent Store) |
| `/api/agents` | GET | Список агентов |
| `/api/agents/{name}` | GET | Получить агента |
| `/api/agents/{name}` | PUT | Обновить агента (widget_config, llm_config) |
| `/api/agents/{name}` | DELETE | Удалить агента |
| `/api/agents/{name}/widget-config` | GET | Конфиг виджета для агента (используется embed.js) |
| `/embed/embed.js` | GET | JS-файл embed-виджета (Shadow DOM, стриминг) |
| `/embed/embed.css` | GET | CSS стили виджета |

## Per-Agent LLM Config

Каждый агент может иметь свою LLM-конфигурацию (`llm_config`), которая переопределяет глобальные настройки окружения.

### Поля llm_config

| Поле | Тип | Описание |
|---|---|---|
| `provider` | `str` | Провайдер: `ollama`, `mistral`, `openai`, `anthropic` |
| `api_key` | `str` | API-ключ (устанавливается в env-переменную для LiteLLM) |
| `model` | `str` | Имя модели (например `qwen2.5:0.5b`, `gpt-4`, `mistral-small`) |
| `api_base` | `str` | Кастомный API base URL (опционально) |
| `temperature` | `float` | Температура генерации (0–2) |
| `max_tokens` | `int` | Максимум токенов в ответе |
| `system_prompt` | `str` | Кастомный системный промпт (переопределяет глобальный) |

### Приоритет выбора LLM

1. **Per-agent `llm_config`** — если передан, используется он
2. **Mistral API** — если `MISTRAL_API_KEY` установлен глобально
3. **Ollama** — дефолтный fallback

### Примеры создания агента

**Ollama (локально):**
```bash
curl -X POST http://localhost:8081/api/agents \
  -H "Content-Type: application/json" \
  -d '{
    "name": "local-assistant",
    "tenant_ids": ["default"],
    "llm_config": {
      "provider": "ollama",
      "model": "qwen2.5:7b",
      "temperature": 0.3
    }
  }'
```

**Mistral API:**
```bash
curl -X POST http://localhost:8081/api/agents \
  -H "Content-Type: application/json" \
  -d '{
    "name": "mistral-agent",
    "tenant_ids": ["default"],
    "llm_config": {
      "provider": "mistral",
      "model": "mistral-small",
      "api_key": "your-mistral-key"
    }
  }'
```

**OpenAI:**
```bash
curl -X POST http://localhost:8081/api/agents \
  -H "Content-Type: application/json" \
  -d '{
    "name": "openai-agent",
    "tenant_ids": ["default"],
    "llm_config": {
      "provider": "openai",
      "model": "gpt-4o-mini",
      "api_key": "sk-..."
    }
  }'
```

> ⚠️ API-ключи устанавливаются в `os.environ` при старте запроса и **не сохраняются между запросами**. При каждом новом вызове чата ключ снова загружается из хранилища агента.

## Embed Widget

Виджет — это готовый JS-компонент для встраивания чата на любой сайт. Работает в Shadow DOM — никакие стили сайта не влияют на виджет, и наоборот.

### Как вставить на сайт

```html
<script src="https://your-server.com/embed/embed.js"
        data-agent="support-agent"
        data-title="Поддержка"
        data-greeting="Чем могу помочь?"
        data-accent="#0f766e"
        data-position="right">
</script>
```

### Data-атрибуты

| Атрибут | Обязательный | Дефолт | Описание |
|---|---|---|---|
| `data-agent` | ✅ | — | Имя агента из Agent Store |
| `data-api-base` | ❌ | `window.location.origin` | Базовый URL API |
| `data-title` | ❌ | "Ассистент" | Заголовок виджета |
| `data-greeting` | ❌ | "Чем могу помочь?" | Приветственное сообщение |
| `data-accent` | ❌ | `#0f766e` | Акцентный цвет |
| `data-position` | ❌ | `right` | Положение: `right` / `left` |

### Автозагрузка конфига из API

Если агент создан с `widget_config`, виджет при старте загружает настройки через:
```
GET /api/agents/{name}/widget-config
```

Эти настройки переопределяют data-атрибуты в HTML. Пример создания агента с полным конфигом виджета:

```bash
curl -X POST http://localhost:8081/api/agents \
  -H "Content-Type: application/json" \
  -d '{
    "name": "support-agent",
    "description": "Агент поддержки",
    "tenant_ids": ["customer-a"],
    "widget_config": {
      "title": "Техподдержка",
      "greeting": "Здравствуйте! Чем помочь?",
      "accent_color": "#2563eb",
      "position": "left"
    },
    "llm_config": {
      "provider": "openai",
      "model": "gpt-4o-mini",
      "api_key": "sk-...",
      "system_prompt": "Ты вежливый сотрудник поддержки. Отвечай кратко и по делу."
    }
  }'
```

### Что умеет виджет

- **Shadow DOM** — полная изоляция от CSS сайта
- **SSE стриминг** — ответы приходят по токену
- **Markdown** — таблицы, списки, **bold**, `code`
- **sessionStorage** — история сессии сохраняется при перезагрузке
- **Enter** — отправить, **Shift+Enter** — новая строка
- **Tool call индикатор** — 🔧 показывает какие инструменты вызывает
- **Адаптивность** — на мобильных на весь экран

## Переменные окружения

См. `.env.example` в корне проекта. Ключевые для API:

| Переменная | Дефолт | Описание |
|---|---|---|
| `DEMO_API_HOST` | `127.0.0.1` | Хост API сервера |
| `DEMO_API_PORT` | `8081` | Порт API |
| `DEMO_WEB_HOST` | `127.0.0.1` | Хост Web сервера |
| `DEMO_WEB_PORT` | `8080` | Порт Web |
| `MCP_SERVICE_URL` | `http://127.0.0.1:8083/mcp` | URL mcp-gateway |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | URL Ollama (LLM) |
| `OLLAMA_MODEL` | `qwen2.5:0.5b` | Модель Ollama |
| `MISTRAL_API_KEY` | — | Ключ Mistral (альтернатива Ollama) |
| `MISTRAL_MODEL` | `mistral/mistral-small` | Модель Mistral |
| `DEMO_SESSION_DB_PATH` | `./demo_sessions.sqlite` | Путь к БД сессий |
| `BACKLOG_DIR` | `./backlog` | Директория бэклогов |
| `BACKLOG_RETENTION_DAYS` | `30` | Дней хранения бэклогов |
| `DEMO_HISTORY_TURNS` | `8` | Кол-во ходов в контексте |
| `DEMO_HISTORY_CONTENT_CHARS` | `6000` | Макс. символов в истории |
| `DEMO_REQUEST_TIMEOUT` | `600` | Таймаут запросов к LLM (сек) |
| `PYTHON_EXECUTABLE` | `python3` | Python для subprocess |
| `ENABLE_THINK` | `true` | Thinking mode |
| `DEMO_DEBUG` | `false` | Debug логирование |
| `AGENT_TEMPERATURE` | `0.5` | Температура генерации |
| `AGENT_MAX_ITERATIONS` | `5` | Макс. итераций тулов за ход |
| `AGENT_MAX_TOKENS_THINKING` | `4096` | Макс. токенов thinking |
| `AGENT_MAX_EMPTY_ROUNDS` | `3` | Макс. пустых раундов thinking |
| `AGENT_MAX_TURN_TOKENS` | `8000` | Макс. токенов за ход (контекст) |
| `LOG_FORMAT` | `text` | Формат логов: `text` или `json` (structlog) |
| `LOG_LEVEL` | `info` | Уровень логирования: debug, info, warn, error |
| `ABUSE_RPS` | `1.0` | Token bucket refill rate (requests/second) |
| `ABUSE_BURST` | `5` | Token bucket burst capacity |
| `ABUSE_MESSAGE_MAX_LENGTH` | `2000` | Макс. длина сообщения (символов) |
| `ABUSE_MIN_INTERVAL` | `1.0` | Мин. интервал между сообщениями (сек) |
| `ABUSE_SESSION_BUDGET` | `50` | Макс. сообщений за сессию |
| `ABUSE_REPEATED_THRESHOLD` | `3` | Порог повторяющегося текста (раз) |

## Запуск

```bash
# Из корня проекта
cd /project/root
uv run python -m demo.api.server

# Или напрямую
uv run --package demo-api python -m uvicorn demo.api.server:app --port 8081
```

## Тестирование

```bash
uv run pytest api-service/src/api_service/tests/ -v
# 10 тестов MCP-клиента/оркестратора — skip (ожидают новый MCP SDK протокол)
```

---

## 🔧 Troubleshooting

| Симптом | Причина | Фикс |
|---|---|---|
| `Cannot connect to host 127.0.0.1:11434` | Ollama не запущен | `ollama serve` или `docker run -d -p 11434:11434 ollama/ollama` |
| `MISTRAL_API_KEY not set` и Ollama недоступен | Нет LLM бэкенда | Настроить `MISTRAL_API_KEY` или запустить Ollama |
| `MCP connection failed` / 502 | mcp-gateway не запущен на 8083 | `go run ./mcp-gateway/cmd/` |
| 401 на `/api/chat` | Не передан `X-Tenant-ID` | Добавить заголовок `X-Tenant-ID: <tenant-id>` |
| SSE обрывается / нет tool calls | LLM не вызывает инструменты | Проверить системный промпт, capabilities модели, логи `DEMO_DEBUG=1` |
| `demo_sessions.sqlite` locked | Остался процесс от прошлого запуска | `pkill -f "demo.api" && rm -f demo_sessions.sqlite* backlog/*.jsonl` |

### Быстрый smoke-тест
```bash
# 1. Зависимости
lsof -ti:11434  # Ollama
lsof -ti:8083   # mcp-gateway

# 2. Health
curl -s http://127.0.0.1:8081/health

# 3. SSE chat (требует запущенный mcp-gateway + data-service + registered tenant)
curl -N -X POST http://127.0.0.1:8081/api/chat \
  -H "Content-Type: application/json" -H "X-Tenant-ID: default" \
  -d '{"message":"привет","session_id":"test"}' | head -c 300
```

### Логи
- Ручное запуск: stdout/stderr терминала
- Через `dev.sh`: `.data/logs/api.log`
- Debug режим: `DEMO_DEBUG=1 uv run python -m demo.api.server`

---

## Monitoring & Observability

Сервис отдаёт Prometheus-метрики на `/metrics`:

| Метрика | Тип | Описание |
|---|---|---|
| `chat_sessions_total` | Counter | Всего создано сессий чата |
| `chat_messages_total` | Counter | Всего сообщений (labels: status) |
| `llm_calls_total` | Counter | Вызовов LLM (labels: provider, model, status) |
| `llm_duration_ms` | Histogram | Длительность LLM-вызова |
| `llm_token_usage` | Counter | Использовано токенов (labels: type=prompt/completion) |
| `llm_cost_total` | Counter | Общая стоимость LLM вызовов ($) |
| `abuse_blocked_total` | Counter | Заблокировано запросов по anti-abuse (labels: reason) |

### Logging
- Используется structlog (JSON-формат при `LOG_FORMAT=json`)
- `LOG_LEVEL` поддерживает: debug, info, warn, error
- Подробнее: `log_config.py`

### Anti-Abuse Engine
Встроенный механизм защиты от злоупотреблений для embed-виджета:
- **TokenBucket**: per-сессия, конфигурируемый (`ABUSE_RPS`, `ABUSE_BURST`)
- **User-Agent проверка**: блокирует curl, wget, python-requests, Go-http-client, Java, libwww, scrapy, PostmanRuntime
- **Message length**: кап 2000 символов
- **Min interval**: не быстрее 1 сообщения в секунду
- **Session budget**: не более 50 сообщений за сессию
- **Repeated text**: 3+ одинаковых сообщений подряд блокируются
- **Настройка**: через admin dashboard (глобально + per-agent через `abuse_config`)
- **Emergency presets**: Normal / Cautious / Lockdown — одним кликом
- Подробнее: `anti_abuse.py`

# API Service

Оркестратор LLM-агента с MCP-интеграцией, управлением сессиями и бэклогом.

**Не демка.** Это основной сервис, через который проходят все LLM-запросы.

## Роль в системе

`api-service` — единственный компонент, который общается с LLM (через LiteLLM). Он:
- Формирует системный промпт + Persona агента
- Управляет MCP-клиентом (подключение к mcp-gateway:8083)
- Хранит историю диалогов (SQLite, путь настраивается через `DEMO_SESSION_DB_PATH`)
- Хранит voice config: глобальный (SQLite: `agents.sqlite`, таблица `global_config`) и per-agent (колонка `voice_config` в таблице `agents`)
- Пишет полный бэклог взаимодействий (JSONL в `backlog/`)
- Проксирует SSE-стрим от агента к Web

## Эндпоинты

| Путь | Метод | Описание |
|---|---|---|
| `/health` | GET | Статус сервиса |
| `/api/chat` | POST | Public SSE chat, жёстко привязан к server-configured demo tenant `DEFAULT_TENANT_ID` |
| `/api/chat/voice` | POST | Public voice SSE chat; без named agent использует только `DEFAULT_TENANT_ID` |
| `/api/chat/{name}` | POST | SSE-чат с именованным агентом; `tenant_ids` только из persisted Agent Store |
| `/api/session/history` | GET | История сессии (query params: session_id, agent_name) |
| `/api/backlog` | GET | Список бэклогов |
| `/api/backlog/{id}` | GET | Детали бэклога |
| `/api/backlog/stats/{session_id}` | GET | Статистика сессии (токены, cost, ошибки) |
| `/api/backlog/errors` | GET | Последние ошибки чата |
| `/api/backlog/export/{session_id}` | GET | Экспорт сессии бэклога (JSONL для fine-tuning) |
| `/metrics` | GET | Prometheus метрики (мониторинг) |
| `/api/agents` | POST | Создать агента (Agent Store) |
| `/api/agents` | GET | Список агентов |
| `/api/agents/{name}` | GET | Получить агента |
| `/admin/guardrails` | GET | Настройки guardrails (prompt injection) |
| `/admin/guardrails` | POST | Обновить конфиг guardrails |
| `/admin/spending` | GET | Обзор лимитов расходов |
| `/admin/spending/{tenant_id}` | GET | Расходы тенанта |
| `/admin/spending/{tenant_id}` | POST | Установить бюджет тенанта |
| `/api/agents/{name}` | PUT | Обновить агента (widget_config, llm_config) |
| `/api/agents/{name}` | DELETE | Удалить агента |
| `/api/agents/{name}/widget-config` | GET | Конфиг виджета для агента (используется embed.js) |
| `/embed/embed.js` | GET | JS-файл embed-виджета (Shadow DOM, стриминг) |
| `/embed/embed.css` | GET | CSS стили виджета |
| `/admin/abuse-config` | GET | Anti-abuse конфигурация |
| `/admin/abuse-config/reload` | POST | Перезагрузить abuse config |
| `/admin/abuse-config` | POST | Обновить abuse config |
| `/admin/llm-providers` | GET | Список LLM-провайдеров |
| `/admin/llm-provider-list` | GET | Доступные провайдеры (из litellm) |
| `/admin/llm-providers/{name}` | GET | Детали провайдера |
| `/admin/llm-providers` | POST | Создать провайдера |
| `/admin/llm-providers/{name}` | PUT | Обновить провайдера |
| `/admin/llm-providers/{name}` | DELETE | Удалить провайдера |
| `/admin/llm-providers/{name}/toggle` | POST | Вкл/выкл провайдера |
| `/admin/llm-config` | GET | Глобальная LLM-конфигурация |
| `/api/voice-config` | GET | Voice config (STT) |
| `/api/voice-config` | PUT | Обновить voice config |

## Per-Agent LLM Config

Каждый агент может иметь свою LLM-конфигурацию (`llm_config`), которая переопределяет глобальные настройки окружения.

### Поля llm_config

| Поле | Тип | Описание |
|---|---|---|
| `provider` | `str` | Провайдер: `ollama`, `mistral`, `openai`, `anthropic` |
| `api_key` | `str` | API-ключ (устанавливается в переменную окружения для LiteLLM) |
| `model` | `str` | Имя модели (например `qwen2.5:0.5b`, `gpt-4`, `mistral-small`) |
| `api_base` | `str` | Кастомный API base URL (опционально) |
| `temperature` | `float` | Температура генерации (0–2) |
| `max_tokens` | `int` | Максимум токенов в ответе |
| `system_prompt` | `str` | Кастомный системный промпт (переопределяет глобальный) |

### Per-agent `provider_priority`

Помимо `llm_config`, каждый агент может иметь поле `provider_priority: list[str]` — упорядоченный список имён провайдеров из `LlmProviderStore` (SQLite `.data/providers.json`).
При запросе `ProviderPool` перебирает их по порядку и использует первого, кто прошёл health check.

### Приоритет выбора LLM

Реальный приоритет сложнее трёх пунктов:

1. **`provider_priority`** (из Agent Store) — если задан, `ProviderPool` перебирает провайдеров по порядку, первый здоровый используется
2. **Per-agent `llm_config`** — если задан (и нет `provider_priority`), создаётся временный `LiteLLMProvider`
3. **ProviderPool** — если не задано ни то, ни другое, используется `_provider_pool` (системный пул, инициализируется из ProviderStore при старте)
4. **Env fallback** — если пул пуст, создаётся `LiteLLMProvider` из переменных окружения (`MISTRAL_API_KEY` → Ollama)

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

> ⚠️ API-ключи хранятся в **двух местах**:
> - **LlmProviderStore** — SQLite-файл `.data/providers.json`, персистентное хранение, управляется через админку (радел LLM Provider Fallback). Ключи **шифруются** при наличии `ENCRYPTION_KEY`.
> - **Per-agent `llm_config`** — зашифрованное поле в таблице `agents` (`agents.sqlite`), читается в `os.environ` при старте запроса.
> - **Переменные окружения** — `MISTRAL_API_KEY`, `OPENAI_API_KEY` и т.п. импортируются как провайдеры при старте, если `LlmProviderStore` пуст.

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
| `data-lang` | ❌ | `en` | Язык сообщений об ошибках: `ru` или `en` |
| `data-width` | ❌ | `min(380px, calc(100vw - 28px))` | Ширина панели (любое CSS-значение) |
| `data-height` | ❌ | `min(620px, calc(100vh - 44px))` | Высота панели |
| `data-placeholder` | ❌ | `"Ask a question..."` | Текст-плейсхолдер в поле ввода |
| `data-header-color` | ❌ | (равно accent) | Цвет фона шапки |
| `data-show-header` | ❌ | `"true"` | Показывать шапку: `"true"` / `"false"` |
| `data-bot-bubble-color` | ❌ | `"#eef3f4"` | Цвет фона пузырька ассистента |
| `data-bot-bubble-text` | ❌ | `"var(--ink)"` | Цвет текста пузырька ассистента |
| `data-voice-input` | ❌ | `"true"` | Голосовой ввод: `"true"` / `"false"` |
| `data-voice-toggle` | ❌ | `"classic"` | Режим голоса: `"classic"` (toggle) / `"telegram"` (зажать=запись, текст=send) |

### Голосовой ввод

#### Classic режим (`data-voice-toggle="classic"`)

Кнопка микрофона рядом с textarea. Нажатие = вкл/выкл запись. Работает параллельно с текстовым вводом.

#### Telegram режим (`data-voice-toggle="telegram"`)

Одна кнопка, которая меняется в зависимости от ввода:

- **Пустое поле** → кнопка микрофона. Зажмите и удерживайте для записи, отпустите — отправится голосовое сообщение.
- **Текст введён** → кнопка отправки (send) с анимацией замены.

```html
<script src="/embed/embed.js"
        data-agent="shop"
        data-voice-toggle="telegram"
        data-voice-input="true">
</script>
```

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

### Сообщения об ошибках

Вместо сырых исключений (``litellm.RateLimitError``, ``429 Too Many Requests``)
пользователь видит понятное сообщение на выбранном языке.

| Ситуация | Русский | English |
|---|---|---|
| Rate limit | Сервер временно перегружен. Пожалуйста, повторите ваш вопрос через несколько секунд. | Server is temporarily overloaded. Please retry your question in a few seconds. |
| Ошибка модели | Ошибка доступа к модели. Попробуйте позже или обратитесь к администратору. | Model access error. Please try again later or contact the administrator. |
| Длинный диалог | Диалог слишком длинный. Пожалуйста, начните новый разговор. | The conversation is too long. Please start a new chat. |
| Ошибка подключения | Не удалось подключиться к серверу данных. Попробуйте позже. | Failed to connect to the data server. Please try again later. |
| Модель не отвечает | Модель не отвечает. Пожалуйста, попробуйте снова или задайте более короткий вопрос. | The model is not responding. Please try again or ask a shorter question. |
| Ошибка провайдера | Ошибка при обработке запроса моделью. Попробуйте позже. | An error occurred while processing your request. Please try again later. |
| Ошибка MCP/БД | Не удалось выполнить запрос к базе данных. Попробуйте позже. | Failed to query the database. Please try again later. |
| Внутренняя ошибка | Извините, произошла внутренняя ошибка. Попробуйте ещё раз. | Sorry, an internal error occurred. Please try again. |
| Нет ответа | Не удалось получить ответ от модели. Пожалуйста, переформулируйте вопрос. | No response from the model. Please rephrase your question. |

Язык определяется:
1. **Embed-виджет** — через атрибут `data-lang="ru"` на `<script>`
2. **HTTP API** — через заголовок `Accept-Language` (передаётся браузером автоматически)

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
| `MCP_GATEWAY_URL` | `http://127.0.0.1:8083` | Base URL mcp-gateway for schema and mapping requests |
| `MCP_STREAMABLE_HTTP_URL` | derived `http://127.0.0.1:8083/mcp` | Единственный standard Streamable HTTP MCP endpoint |
| `MCP_HTTP_TIMEOUT` | `10` | Connect/request timeout MCP transport (сек) |
| `MCP_HTTP_READ_TIMEOUT` | `1800` | Max Streamable HTTP read duration (сек) |
| `MCP_CLIENT_API_KEY` | — | Required production service bearer credential; совпадает с gateway `MCP_API_KEY` при `MCP_REQUIRE_AUTH=true` |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | URL Ollama (LLM) |
| `OLLAMA_MODEL` | `qwen2.5:0.5b` | Модель Ollama |
| `MISTRAL_API_KEY` | — | Ключ Mistral (альтернатива Ollama) |
| `MISTRAL_MODEL` | `mistral/mistral-small` | Модель Mistral |
| `DEMO_SESSION_DB_PATH` | `./demo_sessions.sqlite` | Путь к БД сессий |
| `AGENT_DB_PATH` | `<session_db_dir>/agents.sqlite` | Путь к БД агентов + global config |
| `BACKLOG_DIR` | `./backlog` | Директория бэклогов |
| `BACKLOG_RETENTION_DAYS` | `30` | Дней хранения бэклогов |
| `BACKLOG_MODE` | `full` | `full` — всё пишется, `errors` — только ошибки, `off` — ничего не пишется |
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
| `AGENT_MAX_TOOL_CALLS` | `10` | Макс. вызовов тулов за ход |
| `AGENT_FALLBACK_MAX_MESSAGES` | `7` | Макс. сообщений в контексте Fallback |
| `LOG_FORMAT` | `text` | Формат логов: `text` или `json` (structlog) |
| `LOG_LEVEL` | `info` | Уровень логирования: debug, info, warn, error |
| `ABUSE_RPS` | `1.0` | Token bucket refill rate (requests/second) |
| `ABUSE_BURST` | `5` | Token bucket burst capacity |
| `ABUSE_MESSAGE_MAX_LENGTH` | `2000` | Макс. длина сообщения (символов) |
| `ABUSE_MIN_INTERVAL` | `1.0` | Мин. интервал между сообщениями (сек) |
| `ABUSE_SESSION_BUDGET` | `50` | Макс. сообщений за сессию |
| `ABUSE_REPEATED_THRESHOLD` | `3` | Порог повторяющегося текста (раз) |
| `EMBED_DIR` | `<project>/embed/dist/` | Путь к статике embed-виджета (absolute override) |
| `ENABLE_METRICS` | `true` | Включить Prometheus-метрики |
| `API_BEARER_TOKEN` | — | Bearer token для API (обязателен в production) |
| `VOICE_ENABLED` | `true` | Мастер-выключатель голосовых функций |
| `VOICE_STT_PROVIDER` | `litellm` | Тип STT: `litellm` или `local` |
| `VOICE_STT_MODEL` | `whisper-1` | Модель STT |
| `VOICE_STT_API_KEY` | — | API-ключ STT |
| `VOICE_STT_API_BASE` | — | Кастомный API base URL STT |

| `VOICE_MAX_SIZE_BYTES` | `10485760` | Макс. размер голосового сообщения (байт) |
| `VOICE_MIN_INTERVAL_SEC` | `10` | Мин. интервал между голосовыми сообщениями (сек) |
| `VOICE_MAX_DURATION_SEC` | `120` | Макс. длительность записи (сек) |

## MCP v2 Streamable HTTP

`api-service` использует официальный Python SDK `mcp` v2 и подключается только к standard Streamable HTTP gateway endpoint `/mcp`. Прямые public routes `/api/chat` и `/api/chat/voice` всегда используют server-configured demo scope `[DEFAULT_TENANT_ID]` (fallback `default`) и **игнорируют browser `X-Tenant-ID`**. Только named-agent route разрешает один или несколько `tenant_ids` из persisted Agent Store до запуска agent loop; MCP client получает уже готовый authorized scope и передаёт его в gateway только как routing context.

В production необходимо задать один сильный secret одновременно в `MCP_CLIENT_API_KEY` api-service и `MCP_API_KEY` gateway, а в gateway включить `MCP_REQUIRE_AUTH=true`. Не публикуй mcp-gateway без service authentication. Legacy GET-SSE/POST MCP transport намеренно удалён: откат этой migration выполняется git revert/redeploy предыдущего image, а не переключением runtime transport.

### Контракт api-service → mcp-gateway

| Аспект | Поведение |
|---|---|
| Transport | `streamable_http_client(MCP_STREAMABLE_HTTP_URL)` и `Client(transport)` из SDK v2; application не собирает JSON-RPC и не управляет session IDs вручную |
| Tenant authority | Direct public chat всегда передаёт `[DEFAULT_TENANT_ID]`; только named-agent route может передать persisted `tenant_ids`. Browser `X-Tenant-ID` не читается API routes |
| Tenant scope | Resolved `tenant_ids` передаётся на каждое transport request как `X-Tenant-ID`; query parameter не используется и не принимается gateway |
| Composite agent | Несколько persisted IDs передаются как `tenant-a,tenant-b`; gateway возвращает только prefixed tools (`tenant-a__db_map`) и отклоняет duplicate/oversized scopes |
| Service auth | В production non-empty `MCP_CLIENT_API_KEY` обязателен и добавляется в каждый request как `Authorization: Bearer …`; он должен совпасть с gateway `MCP_API_KEY` |
| Connection lifecycle | Один persistent connection на tenant scope; `asyncio.Lock` сохраняет порядок tool calls; idle GC и `Client` context teardown закрывают connection корректно |

### Диагностика ошибок MCP

| Наблюдение | Значение | Действие |
|---|---|---|
| `400 X-Tenant-ID header is required` | Внутренний caller потерял уже разрешённый tenant scope | Проверить agent `tenant_ids` и propagation header, не добавлять query fallback |
| `401 Unauthorized` | Missing/mismatched `MCP_CLIENT_API_KEY` | Синхронизировать один secret с gateway `MCP_API_KEY` и перезапустить оба сервиса |
| `403 Origin is not allowed` | Gateway получил browser-like Origin вне allow-list | MCP должен остаться internal service path; если browser ingress намерен, согласовать explicit `MCP_ALLOWED_ORIGINS` |
| `429 Too Many Requests` | Gateway rate limiter защитил `/mcp` | Снизить параллелизм/повторы либо пересмотреть `MCP_RATE_LIMIT_*` осознанно |
| `503 too many active Streamable HTTP tenant scopes` | Достигнут bounded cache `MCP_MAX_STREAMABLE_TENANT_SCOPES` | Проверить churn tenant sets, не увеличивать limit без memory-capacity оценки |
| MCP result `is_error=true` | Transport работает; tool отверг аргументы или data-service вернул domain error | Показывать пользователю безопасный tool error, анализировать audit logs и tool schema |

## Запуск

```bash
uv run --package api-service python -m uvicorn api_service.server:app --port 8081
```

## Тестирование

```bash
# Unit and integration checks for api-service, including MCP client failures,
# reconnect, initialization timeout and idle connection GC.
uv run pytest services/api-service/src/api_service/tests/ -v

# API tenant authority regression: direct header spoofing is ignored while
# named agents retain their persisted composite scope.
uv run pytest services/api-service/src/api_service/tests/unit/test_chat_tenant_scope.py -v

# Real persisted named-agent composite pipeline. It starts a ScriptedLLM API
# process, sends a hostile browser tenant header and proves the Agent Store scope
# reaches a prefixed MCP tool and data-service result.
MCP_API_KEY="$MCP_API_KEY" MCP_CLIENT_API_KEY="$MCP_CLIENT_API_KEY" \
  uv run pytest services/agent-db/tests/e2e/test_named_agent_composite_pipeline.py -v

# Live gateway contract against started data-service + mcp-gateway. The official
# MCP v2 client covers tools, composite scopes, session replay isolation, query
# rejection, cardinality bounds and configured auth/Origin rejection.
MCP_API_KEY="$MCP_API_KEY" MCP_ALLOWED_ORIGINS="$MCP_ALLOWED_ORIGINS" \
  uv run pytest services/agent-db/tests/e2e/test_mcp_streamable_http.py -v
```

## Архитектура Agent Pipeline

Все LLM-запросы проходят через Pipeline — циклический обработчик Stage'ов с Middleware-фильтрами.

```
Pipeline.run() ─► while loop ─► for stage in stages ─► for event in stage.run(ctx)
                                  │                        │
                                  │                        └──► Middleware chain
                                  │                      SpendingMiddleware
                                  │                      TokenBudgetMiddleware
                                  └──► ctx.should_stop? ──► break
                     ─► Фаза 2 (finalization): FallbackStage → GuardOutputStage → SaveHistoryStage
```

### Два фазы выполнения

**Фаза 1 — Основной цикл**

Stage'ы выполняются последовательно в цикле `while not ctx.should_stop`. LLMStage и ToolExecutionStage чередуются: LLM отвечает → если есть tool_calls → ToolExecution выполняет их → LLM получает результаты и отвечает снова. GuardInputStage и ToolDiscoveryStage выполняются один раз (через `_done_flags`).

Цикл завершается когда:
- `ctx.should_stop = True` (лимит tool calls, max iterations)
- `ctx.turn.final_content` установлен (LLM дал финальный ответ)
- `ctx.turn.empty_rounds >= max_empty_rounds` (модель не отвечает)
- `ctx.turn.iteration >= max_iterations - 1` (максимальное число итераций)

**Фаза 2 — Финализация**

После выхода из цикла выполняются finalizer-стейджи (FallbackStage → GuardOutputStage → SaveHistoryStage) — каждый ровно один раз.

### PipelineContext

`PipelineContext` — dataclass, хранящий всё runtime-состояние:
- `turn: TurnContext` — сообщения, история, tenant_ids, итерация
- `llm_provider: LLMProvider` — текущий LLM-провайдер
- `mcp_session: MCPSession` — MCP-сессия для tool calls
- `store/spending/backlog` — runtime-зависимости через протоколы
- `max_iterations/max_empty_rounds/max_turn_tokens` — лимиты (из ENV)
- `bench: dict` — аккумулятор метрик (токены, cost, tool_calls)
- `_done_flags: set[str]` — флаги one-shot stage'ов

### Протоколы Stage и Middleware

```python
# Stage — любой асинхронный генератор событий
class Stage(Protocol):
    def run(self, ctx: PipelineContext) -> AsyncIterator[AgentEvent]: ...

# Middleware — фильтр событий (модифицирует или блокирует)
class Middleware(Protocol):
    async def process(self, ctx: PipelineContext, event: AgentEvent) -> AgentEvent | None: ...
```

## Список Stage'ов

| Stage | Файл | Назначение |
|---|---|---|
| `GuardInputStage` | `stages/guard_input.py` | Проверка пользовательского ввода на prompt injection |
| `ToolDiscoveryStage` | `stages/tool_discovery.py` | Формирование JSON-схемы MCP-инструментов для LLM |
| `LLMStage` | `stages/llm.py` | Вызов LLM, парсинг ответа, определение outcome (tool_calls / final / empty) |
| `ToolExecutionStage` | `stages/tool_execution.py` | Выполнение MCP tool calls, возврат результатов |
| `GuardOutputStage` | `stages/guard_output.py` | Фильтрация чувствительных данных в ответе LLM |
| `FallbackStage` | `stages/fallback.py` | Обработка пустых/безответных случаев |
| `SaveHistoryStage` | `stages/save_history.py` | Сохранение истории диалога в ConversationStore |

Все stage'и находятся в `api_service/agent/stages/`. Импортируются через `stages/__init__.py`.

## Как добавить свой Stage

1. **Создать файл** в `agent/stages/` (например `my_stage.py`)
2. **Реализовать протокол `Stage`**:

```python
from collections.abc import AsyncIterator
from api_service.agent.types import AgentEvent
from api_service.agent.pipeline import PipelineContext, Stage

class MyStage:
    async def run(self, ctx: PipelineContext) -> AsyncIterator[AgentEvent]:
        # Ваша логика
        yield AgentEvent(type="status", data={"message": "done"})
```
3. **Добавить импорт** в `agent/stages/__init__.py`
4. **Зарегистрировать** в `LLMAgent.__init__` (`orchestrator.py`):   - Добавить в список `stages=[...]` — выполняется в основном цикле   - ИЛИ в `finalizer_stages=[...]` — выполняется один раз после цикла
5. **Определить тип**:   - Loop stage (выполняется на каждой итерации) → `stages=[...]`   - One-shot stage (выполняется один раз) → используйте `ctx._stage_ran("MyStage")` / `ctx._mark_done("MyStage")` для гейтинга   - Finalizer (выполняется после цикла) → `finalizer_stages=[...]`
6. **Написать тесты** в `tests/unit/agent/`

Пример регистрации:

```python
# agent/orchestrator.py
self._pipeline = Pipeline(
    stages=[
        GuardInputStage(),
        ToolDiscoveryStage(),
        MyStage(),          # ← новый stage
        LLMStage(),
        ToolExecutionStage(),
    ],
    finalizer_stages=[
        FallbackStage(),
        GuardOutputStage(),
        SaveHistoryStage(),
    ],
    middlewares=[SpendingMiddleware(), TokenBudgetMiddleware()],
)
```

## LLM Provider Resolution

Провайдер LLM определяется функцией `resolve_llm()` в `agent/factory.py`. Приоритет (от высшего к низшему):

| # | Источник | Когда используется | Пример |
|---|---|---|---|
| 1 | **Scripted** | `USE_SCRIPTED_LLM=1` — детерминированный режим для тестов | Возвращает фиксированные ответы |
| 2 | **Explicit llm_client** | Вызывающий передаёт провайдер напрямую (тесты, priority routing) | `LLMStage(llm_client=mock)` |
| 3 | **Per-agent llm_config** | Агент создан с `llm_config={model, provider, api_key, ...}` | `POST /api/agents` с `llm_config` |
| 4 | **ProviderPriority** | У агента задан `provider_priority: ["openai", "mistral"]` | `ProviderPool` перебирает по порядку |
| 5 | **Pool / env fallback** | По умолчанию — `ProviderPool` → env vars (`MISTRAL_API_KEY`) → Ollama | Автоматически |

API-ключи передаются напрямую в `LiteLLMProvider(api_key=...)` — НЕ через `os.environ`, что безопасно при параллельных запросах.

## Pipeline Configuration (ENV vars)

| Переменная | Дефолт | Описание |
|---|---|---|
| `AGENT_MAX_ITERATIONS` | `5` | Максимальное число итераций tool calls за ход |
| `AGENT_MAX_EMPTY_ROUNDS` | `3` | Максимум пустых ответов LLM перед остановкой |
| `AGENT_MAX_TURN_TOKENS` | `8000` | Максимум токенов в контексте за ход |
| `AGENT_MAX_TOOL_CALLS` | `10` | Максимум вызовов тулов за ход |
| `AGENT_FALLBACK_MAX_MESSAGES` | `7` | Макс. сообщений в контексте Fallback (с окружением) |
| `AGENT_TEMPERATURE` | `0.5` | Температура генерации LLM |
| `AGENT_MAX_TOKENS_THINKING` | `4096` | Максимум thinking-токенов |
| `ENABLE_THINK` | `true` | Включить thinking mode |

## MCP Client Configuration (ENV vars)

| Переменная | Дефолт | Описание |
|---|---|---|
| `MCP_MAX_CONSECUTIVE_FAILURES` | `3` | Circuit breaker: порог отказов перед пропуском reconnect |
| `MCP_CIRCUIT_BREAKER_TIMEOUT` | `30.0` | Время (сек) до half-open retry после размыкания circuit breaker |
| `MCP_GC_INTERVAL` | `60.0` | Интервал (сек) фонового GC для неактивных Streamable HTTP connections |
| `MCP_MAX_IDLE_SECONDS` | `600.0` | Время (сек) бездействия Streamable HTTP connection до закрытия |
| `MCP_LOCK_ACQUIRE_TIMEOUT` | `10.0` | Таймаут (сек) захвата per-tenant блокировки tool call |
| `MCP_TOOL_EXECUTION_TIMEOUT` | `15.0` | Таймаут (сек) выполнения одного tool call |
| `MCP_HTTP_TIMEOUT` | `10.0` | Таймаут (сек) открытия Streamable HTTP соединения |
| `MCP_HTTP_READ_TIMEOUT` | `1800.0` | Таймаут (сек) чтения Streamable HTTP ответа/стрима |
| `MCP_SESSION_INIT_TIMEOUT` | `15.0` | Таймаут (сек) инициализации MCP сессии (session.initialize) |

Заменяют предыдущие хардкоды в `mcp_client.py`, читаются из `helperium_sdk.settings` при каждом вызове.

## Middleware


Middleware обрабатывают каждый `AgentEvent` после stage'а. Могут модифицировать, блокировать или записывать побочные эффекты.

| Middleware | Файл | Что делает |
|---|---|---|
| `SpendingMiddleware` | `middlewares.py` | Записывает cost в SpendingTracker для tenant'ов; проверяет лимиты — при превышении заменяет событие на `error` |
| `TokenBudgetMiddleware` | `middlewares.py` | После `tool_result`/`final`/`error` проверяет суммарное число токенов; при превышении `max_turn_tokens` выставляет `ctx.should_stop = True` |

Middleware добавляются при создании `Pipeline`:

```python
Pipeline(
    stages=[...],
    middlewares=[SpendingMiddleware(), TokenBudgetMiddleware()],
)
```

---
**Last verified:** 2026-08-18 (working tree after `8725612`) — MCP SDK v2 lifecycle, production service auth, Origin allow-list, direct-chat authority and real persisted named-agent composite pipeline through api-service, gateway and data-service verified locally.

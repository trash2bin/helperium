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

Это **execution-time failover**, а не одноразовый выбор по health check. Если есть `llm_config`, он всегда является первым кандидатом, а уникальные enabled entries из `provider_priority` дополняют его в заданном порядке. Если `llm_config` нет, первым кандидатом становится первый enabled provider из списка. При ошибке completion текущий кандидат сменяется следующим; успешно ответивший upstream сохраняется для оставшихся model calls того же agent turn, включая continuation после MCP tool result. Следующий пользовательский turn снова начинает с primary. Дубликат primary config в `provider_priority` пропускается. Global `fallback_enabled=false` оставляет только первый кандидат.

### Приоритет выбора LLM

Реальный порядок разрешения следующий:

1. **Scripted provider** — deterministic dev/test режим имеет абсолютный приоритет.
2. **Явно переданный `llm_client`** — применяется для controlled dependency injection.
3. **Per-agent `llm_config` + `provider_priority`** — `llm_config` остаётся primary; stored priority образует ordered runtime fallback chain.
4. **Только `provider_priority`** — первый enabled stored provider является primary, следующие — fallback-кандидаты.
5. **ProviderPool** — если agent config не задал кандидатов, используется системный пул (инициализируется из ProviderStore при старте).
6. **Env fallback** — если пул пуст, создаётся `LiteLLMProvider` из переменных окружения (`MISTRAL_API_KEY` → Ollama).

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
| `LOG_FORMAT` | `text` | Формат логов: `text` или `json` (structlog) |
| `LOG_LEVEL` | `info` | Уровень логирования: debug, info, warn, error |
| `ABUSE_RPS` | `1.0` | Token bucket refill rate (requests/second) |
| `ABUSE_BURST` | `5` | Token bucket burst capacity |
| `ABUSE_MAX_MSG_LENGTH` | `2000` | Макс. длина пользовательского сообщения (символов) |
| `ABUSE_MIN_INTERVAL_MS` | `1000` | Мин. интервал между принятыми user turns (миллисекунды) |
| `ABUSE_MAX_USER_TURNS` | `50` | Макс. принятых user turns за сессию; provider/tool failure quota не возвращает |
| `ABUSE_MAX_REPEATED` | `3` | Порог повторяющегося текста (раз) |
| `EMBED_DIR` | `<project>/embed/dist/` | Путь к статике embed-виджета (absolute override) |
| `ENABLE_METRICS` | `true` | Включить Prometheus-метрики |
| `API_BEARER_TOKEN` | — | Bearer token для API (обязателен в production) |
| `SPENDING_RESERVATIONS_ENABLED` | `false` | Двухфазное reserve/commit-допущение расходов. Не включать: см. `doc/agents/spending-reserve-commit-decision.md` |
| `SPENDING_LEDGER_PATH` | `<project>/.data/spending-ledger.sqlite3` | SQLite-ledger резерваций |
| `SPENDING_PRINCIPAL_DEFAULT_BUDGET` | `0` | Бюджет billing principal (account/agent), USD. 0 = без лимита |
| `SPENDING_RESERVATION_TTL_SECONDS` | `1800` | Время жизни резервации; должно превышать максимальную длительность turn |
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

В production задайте один сильный secret одновременно в `MCP_CLIENT_API_KEY` api-service и `MCP_API_KEY` gateway, а в gateway включите `MCP_REQUIRE_AUTH=true`. Legacy GET-SSE/POST JSON-RPC path удалён из runtime; rollback выполняется deploy предыдущего tested image.

### Контракт api-service → mcp-gateway

| Аспект | Поведение |
|---|---|
| Transport | `streamable_http_client(MCP_STREAMABLE_HTTP_URL)` и `Client(transport, mode="legacy")` из SDK v2; application не собирает JSON-RPC и не управляет session IDs вручную. `mode="legacy"` фиксирует standard `initialize` handshake до появления auto-mode `server/discover` в mcp-go. |
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
# reaches two prefixed MCP tools and results from two distinct tenant databases.
MCP_API_KEY="$MCP_API_KEY" MCP_CLIENT_API_KEY="$MCP_CLIENT_API_KEY" \
  uv run pytest services/agent-db/tests/e2e/test_named_agent_composite_pipeline.py -v

# Live gateway contract against started data-service + mcp-gateway. The official
# MCP v2 client covers tools, composite scopes, session replay isolation, query
# rejection, cardinality bounds and configured auth/Origin rejection.
MCP_API_KEY="$MCP_API_KEY" MCP_ALLOWED_ORIGINS="$MCP_ALLOWED_ORIGINS" \
  uv run pytest services/agent-db/tests/e2e/test_mcp_streamable_http.py -v
```

## Append-Only Agent Loop

The agentic core is deliberately small. `LLMAgent` owns only request lifecycle: it resolves a provider, loads conversation history, opens an already tenant-scoped MCP session, persists the completed turn, and writes backlog metrics. `AppendOnlyLoop` in `api_service/agent/loop.py` owns execution.

There is no pipeline, stage registry, middleware chain, fallback stage, secondary runtime package, catalog mode, text tool parser, or shadow `TurnContext`. The only provider context is `Transcript.messages`, an append-only list sent in full to the provider on every model call.

```text
system prompt + persisted history + current user
                    │
                    ▼
      provider.complete(messages, scoped MCP tools)
                    │
        ┌───────────┴────────────┐
        ▼                        ▼
native tool_calls            final text
        │                        │
append assistant tool-call       append assistant text
message to Transcript             output guard → final SSE
        │
validate each call against the immutable scoped MCP tool set
        │
execute calls sequentially through the existing MCP session
        │
append every `{role: "tool", tool_call_id, content}` result to Transcript
        │
        └──────────────► next provider.complete(same Transcript, same tools)
```

### Tool-call protocol

`models.py` defines the typed provider boundary. `CompletionRequest` always includes the full transcript and the complete scoped MCP tool schema. A provider returns a `CompletionResponse`; every tool call is a Pydantic `ToolCall(id, name, arguments)`.

`LiteLLMProvider` accepts **only native structured function calls** from LiteLLM's response fields. JSON, XML, Markdown, MiniMax delimiters, or any other tool-looking text inside assistant `content` is never executed. This is intentional: a provider without native structured tool calling can still provide ordinary chat, but cannot use MCP tools until its LiteLLM integration returns native `tool_calls`.

A fresh user turn always advertises the complete scoped MCP schema, even when persisted history contains completed `role: tool` messages from earlier turns. Only the immediate continuation after the current turn's unresolved tool call/result consults LiteLLM's function-calling capability before deciding whether schemas remain on the wire. The adapter logs the model, provider, schema count, current-turn continuation flag, and capability result for each completion without logging request or tool-result content.

The deterministic `ScriptedLLMProvider` implements the same protocol for tests and E2E. Its JSONL fixtures contain typed `content` or `tool_calls`; they do not emulate provider-specific text parsing.

### Transcript, ordering, and persistence

A successful tool round is serialized exactly as `assistant(tool_calls) → tool(result) → … → assistant(final)`. The original `ToolCall.id` becomes `tool_call_id` on the corresponding result, so multiple calls remain unambiguous. Calls execute sequentially to keep MCP session ordering deterministic.

The next model request uses the same augmented transcript. After the run, the orchestrator persists exactly the messages appended after the current user message. There is no separate mutable context that can drift from the provider prompt or history store.

### Limits, terminals, and events

The loop applies explicit bounds before a model call or tool call: `AGENT_MAX_ITERATIONS`, `AGENT_MAX_TOOL_CALLS`, `AGENT_MAX_TURN_TOKENS`, and `AGENT_MAX_EMPTY_ROUNDS`. Input and output guards remain direct checks. Spending is recorded after each provider response; a single-tenant budget denial is terminal.

### Spending admission

By default cost accounting is post-hoc: the provider response cost is recorded per tenant, and a single-tenant budget denial stops the *next* call. `SPENDING_RESERVATIONS_ENABLED=true` switches the loop to two-phase admission — a micro-USD reservation against the billing principal before each provider call, then commit of realized cost (or release on failure). A refused reservation is a `limit_reached` terminal, not an internal error, and per-tenant recording continues so the admin spending API keeps working.

The flag is **off in every environment** and must stay off until the pre-call token estimate is a true upper bound and configured models have per-token pricing. Selection depends only on the flag, never on whether a provider was injected, so tests exercise the same path as runtime. See `doc/agents/spending-reserve-commit-decision.md`.

A tool failure, provider failure, cancellation, dependency outage, limit, blocked input, clarification request, or final answer produces one explicit terminal outcome. The loop never creates a hidden fallback or recovery completion. Public SSE is emitted directly as existing `AgentEvent` values: `tool_call`, `tool_result`, `final`, or `error`; `final` is buffered until the output guard completes, not token streaming. The chat route remains responsible for its terminal `done` frame.

### Extending the agent safely

Do not add stages, middleware, text parsers, or provider-specific execution branches. To add a tool, expose it through the scoped MCP schema and make its JSON schema accurate. To add a provider, implement the narrow `LLMProvider.complete(CompletionRequest) -> CompletionResponse` protocol and return native structured tool calls. Add a scripted-provider regression proving the complete transcript and SSE behavior.

## LLM Provider Resolution

`resolve_llm()` in `agent/factory.py` selects the provider in this order:

| Priority | Source | When used |
|---:|---|---|
| 1 | Explicit `llm_client` | Tests or a caller-injected provider |
| 2 | Per-agent `llm_config` | Persisted model/provider configuration for a named agent |
| 3 | Per-agent `provider_priority` | Ordered healthy provider selection from `ProviderPool` |
| 4 | Pool or environment fallback | Provider store, then environment-backed LiteLLM provider |

Provider resolution changes transport selection only. It does not change the append-only loop, MCP scope, tool protocol, or tenant authority.

## Agent Loop Configuration

| Variable | Default | Meaning |
|---|---:|---|
| `AGENT_MAX_ITERATIONS` | `5` | Maximum provider completions in one run |
| `AGENT_MAX_EMPTY_ROUNDS` | `3` | Maximum empty provider responses before a clarification terminal |
| `AGENT_MAX_TURN_TOKENS` | `8000` | Approximate append-only transcript context bound |
| `AGENT_MAX_TOOL_CALLS` | `10` | Maximum MCP calls in one run |
| `AGENT_TEMPERATURE` | `0.5` | Provider sampling temperature |
| `AGENT_MAX_TOKENS_THINKING` | `4096` | Provider-specific thinking limit where supported |
| `ENABLE_THINK` | `true` | Enable provider-specific thinking mode where supported |
| `LLM_MAX_ATTEMPTS` | `3` | Total physical attempts for one logical model completion; `1` disables retries |
| `LLM_RETRY_MAX_ELAPSED_SECONDS` | `60.0` | Total monotonic budget for attempts and backoff of one logical completion |
| `LLM_RETRY_TRANSIENT_BASE_SECONDS` | `0.25` | Full-jitter base delay for temporary transport and upstream failures |
| `LLM_RETRY_THROTTLED_BASE_SECONDS` | `1.0` | Full-jitter base delay for `429` throttling |
| `LLM_RETRY_MAX_BACKOFF_SECONDS` | `4.0` | Maximum selected retry delay before deadline clipping |

Retries are internal to the physical LiteLLM completion call. They repeat no transcript mutation or MCP tool execution, preserve cancellation, honour valid `Retry-After` values within the deadline, and exhaust before `FallbackProvider` tries the next provider. These process-wide controls are intentionally not fields of public per-agent `LLMConfig` or OpenAPI.

`AGENT_FALLBACK_MAX_MESSAGES` is no longer used: the runtime has no fallback context rewrite.

## MCP Client Configuration

| Variable | Default | Meaning |
|---|---:|---|
| `MCP_MAX_CONSECUTIVE_FAILURES` | `3` | Circuit-breaker failures before a reconnect pause |
| `MCP_CIRCUIT_BREAKER_TIMEOUT` | `30.0` | Seconds before half-open retry |
| `MCP_GC_INTERVAL` | `60.0` | Idle Streamable HTTP connection GC interval |
| `MCP_MAX_IDLE_SECONDS` | `600.0` | Idle connection lifetime before close |
| `MCP_LOCK_ACQUIRE_TIMEOUT` | `10.0` | Per-tenant tool-call lock acquisition timeout (bounds only the lock wait, not execution) |
| `MCP_TOOL_EXECUTION_TIMEOUT` | `15.0` | One MCP tool execution timeout (independent of lock wait) |
| `MCP_CLOSE_ESCALATION_TIMEOUT` | `5.0` | Grace window for MCP session teardown before forced transport close; also bounds total owner-task shutdown wait (x3) |
| `MCP_ZOMBIE_TOOL_TIMEOUTS` | `2` | Consecutive tool timeouts on one connection before quarantine (connection replacement) |
| `MCP_HTTP_TIMEOUT` | `10.0` | Streamable HTTP connection timeout |
| `MCP_HTTP_READ_TIMEOUT` | `1800.0` | Streamable HTTP read timeout |
| `MCP_SESSION_INIT_TIMEOUT` | `15.0` | MCP session initialization timeout |

## Ссылки

- [MCP session lifecycle](doc/agents/mcp-session-lifecycle.md) — полный lifecycle MCP-сессии
- [Tool call safety layers](doc/agents/tool-call-safety-layers.md) — safety orchestration
- [Anti-abuse](doc/agents/anti-abuse.md) — guardrails и rate limiting
- [Security isolation](doc/agents/security-isolation.md) — tenant isolation


---
**Last verified:** 2026-08-24 (working tree following `0add4ea`) — documentation restructure (P0-P5 sweep).

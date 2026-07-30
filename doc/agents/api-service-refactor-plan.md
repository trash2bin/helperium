# api-service Refactor Plan — Phase-by-Phase

## Goals
1. Уменьшить кодовую базу
2. Убрать сервер.py 1448 строк → сервер/ пакет
3. Убрать stages.py 1034 строк → 7 отдельных файлов
4. Оркестратор.py 519 строк → оркестратор + фабрика + адаптеры
5. Разделить бизнес-логику от данных
6. Сделать настройку агента легко меняемой
7. Усилить протоколы и обработку ошибок

---

## Phase 1: Удаление мертвого кода и хардкода

### 1.1 Удалить pricing.py
- Файл мёртвый (ноль импортов из других модулей)
- Удалить: `api_service/pricing.py`
- Переписать `api_service/__init__.py` если есть экспорт

### 1.2 Удалить orphan .pyc файлы
- `agent/__pycache__/_sync_adapters.cpython-31*.pyc`
- `agent/__pycache__/fallback_handler.cpython-31*.pyc`
- `agent/__pycache__/legacy_adapters.cpython-31*.pyc`
- `agent/__pycache__/llm_client.cpython-31*.pyc`
- `agent/__pycache__/llm_handler.cpython-31*.pyc`
- `agent/__pycache__/tool_handler.cpython-31*.pyc`

### 1.3 Удалить PARTIAL_REMINDER из prompts.py
- Константа `PARTIAL_REMINDER` не используется (осталась от удалённого кода)
- Убрать из `agent/prompts.py`

### 1.4 Очистить server.py от мёртвого кода
- `AGENT_DB_PATH` fallback дублируется в agent_store.py → оставить одно место

### 1.5 Очистить pipeline.py от хардкода
- `max_iterations=5`, `max_empty_rounds=3`, `max_turn_tokens=8000` — дублируют settings
- Убрать дефолты, читать из settings

---

## Phase 2: server.py → server/ пакет

### 2.1 Создать структуру
```
server/
├── __init__.py
├── app.py              ← FastAPI app, lifespan, middleware
├── deps.py             ← get_agent(), get_agent_store(), get_limiter()
├── sse.py              ← _sse(), _event_payload(), _single_error(), _get_lang()
├── routes/
│   ├── __init__.py
│   ├── chat.py         ← chat_handler, chat_agent_handler, chat_voice_endpoint
│   ├── agents.py       ← Agent CRUD
│   ├── admin.py        ← guardrails, spending, abuse-config, llm-providers
│   ├── backlog.py      ← backlog endpoints
│   ├── health.py       ← /health
│   └── voice.py        ← /api/voice-config
└── middleware/
    ├── __init__.py
    ├── correlation.py  ← correlation ID
    └── embed.py        ← embed security headers
```

### 2.2 Что выносим из server.py (1448 → ~200 строк в app.py)

| Блок | Строки | Куда |
|------|--------|------|
| `_check_abuse()` | 173-249 | `security/abuse_live.py` (расширить) |
| `_get_lang()` | 251-264 | `server/sse.py` |
| `_sync_pool_from_store()` | 266-306 | `server/deps.py` |
| `_resolve_llm_for_agent()` | 309-326 | `agent/factory.py` |
| `_sse()`, `_single_error()`, `_event_payload()` | 395-428 | `server/sse.py` |
| chat_handler | 325-382 | `server/routes/chat.py` |
| chat_agent_handler | 1157-1234 | `server/routes/chat.py` |
| chat_voice_endpoint | 1242-1410 | `server/routes/chat.py` |
| Admin endpoints (12 штук) | 1016-1198 | `server/routes/admin.py` |
| Agent CRUD (5 штук) | 1050-1155 | `server/routes/agents.py` |
| Backlog endpoints (5 штук) | 750-900 | `server/routes/backlog.py` |
| Health endpoint | 193-198 | `server/routes/health.py` |
| Voice config endpoints | 1010-1032 | `server/routes/voice.py` |
| Middleware (2 штуки) | 540-600 | `server/middleware/` |

---

## Phase 3: stages.py → agent/stages/*.py

### 3.1 Создать структуру
```
agent/stages/
├── __init__.py         ← re-exports
├── guard_input.py      ← GuardInputStage
├── tool_discovery.py   ← ToolDiscoveryStage + _entity_tool_name + _build_schema_message
├── llm.py              ← LLMStage + _looks_like_raw_json_tool_calls + _format_tool_calls_for_message
├── tool_execution.py   ← ToolExecutionStage
├── guard_output.py     ← GuardOutputStage
├── fallback.py         ← FallbackStage
└── save_history.py     ← SaveHistoryStage
```

### 3.2 Что выносим из stages.py (1034 → ~100 строк в каждом файле)

| Класс | Строки | Куда |
|-------|--------|------|
| GuardInputStage | 70-135 | `stages/guard_input.py` |
| ToolDiscoveryStage + _build_schema_message | 136-310 | `stages/tool_discovery.py` |
| LLMStage + safety net + formatting | 343-650 | `stages/llm.py` |
| ToolExecutionStage | 670-880 | `stages/tool_execution.py` |
| GuardOutputStage | 892-940 | `stages/guard_output.py` |
| FallbackStage | 946-1000 | `stages/fallback.py` |
| SaveHistoryStage | 1006-1034 | `stages/save_history.py` |
| _looks_like_raw_json_tool_calls | 620-660 | `stages/llm.py` |
| _format_tool_calls_for_message | 590-620 | `stages/llm.py` |

---

## Phase 4: orchestrator.py → orchestrator + factory + adapters

### 4.1 Создать структуру
```
agent/
├── orchestrator.py      ← LLMAgent (только stream_events + health)
├── factory.py           ← ProviderFactory (5 веток LLM resolution)
└── adapters.py          ← _AsyncSpendingTracker, _AsyncBacklogWriter
```

### 4.2 Что выносим из orchestrator.py (519 → ~200 строк в orchestrator.py)

| Блок | Строки | Куда |
|------|--------|------|
| `_AsyncSpendingTracker` | 55-63 | `agent/adapters.py` |
| `_AsyncBacklogWriter` | 66-85 | `agent/adapters.py` |
| `_OLLAMA_PREFIXES` | 99-106 | `agent/factory.py` |
| `_create_env_provider()` | 128-172 | `agent/factory.py` |
| `_resolve_pool_or_env()` | 175-190 | `agent/factory.py` |
| `_set_provider_env_key()` | 195-213 | `agent/factory.py` |
| `_prefix_model()` | 215-231 | `agent/factory.py` |
| `_pool` singleton | 90-94 | `agent/factory.py` |
| Provider resolution chain в stream_events() | 249-425 | `agent/factory.py` → `ProviderFactory.resolve()` |
| `agent = LLMAgent()` singleton | 497-500 | оставить или перенести в deps.py |

---

## Phase 5: Протоколы и обработка ошибок

### 5.1 Новые протоколы в protocols.py
```python
class ErrorReporter(Protocol):
    def classify(self, exc: Exception, lang: str = "ru") -> str: ...

class SessionManager(Protocol):
    async def get_history(self, session_id: str) -> list[dict]: ...
    async def save_turn(self, session_id: str, messages: list[dict]) -> None: ...
    def normalize(self, session_id: str) -> str: ...

class AntiAbuseChecker(Protocol):
    async def check(self, request, session_id, message, agent_config=None) -> ...: ...
```

### 5.2 Error handling улучшения
- `classify_error()` → логировать WARNING для каждой ошибки с `error_code`
- Ввести `ErrorContext` dataclass с `session_id`, `correlation_id`, `error_code`
- Каждый Stage должен логировать ошибки с контекстом
- `server/routes/chat.py` → добавить `correlation_id` в error response

### 5.3 Удалить дублирование типов
- `types.py` TypedDict'ы → мигрировать на Pydantic модели из `models.py`
- `UsageInfo` дублируется (TypedDict + Pydantic) → оставить Pydantic

---

## Execution Plan — Пофазное делегирование

### Phase 1: Один воркер, быстрая очистка
- Один subagent удаляет pricing.py, .pyc файлы, PARTIAL_REMINDER
- Длительность: ~2 минуты

### Phase 2: 2 воркера параллельно
- Воркер A: Создаёт server/ пакет (routes, middleware, deps)
- Воркер B: Создаёт agent/stages/ пакет
- Длительность: ~5-7 минут

### Phase 3: 2 воркера параллельно
- Воркер A: Extracts from orchestrator.py (factory, adapters)
- Воркер B: Протоколы + error handling + cleanup types.py
- Длительность: ~5-7 минут

### Phase 4: Один воркер — проверка
- Проверка импортов, исправление циклических зависимостей
- Добавление re-export shims
- Длительность: ~3 минуты

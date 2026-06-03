# Agent Workflow Documentation

> **Кратко:** Агент — это оркестратор между LLM-моделью, MCP-инструментами и историей сессии. 
> **Цель:** Получать точные ответы на вопросы о университете, **НИКОГДА не выдумывая данные** без использования инструментов.

---

## 📋 Системный промпт

Системный промпт задает **критически важные правила** работы агента:

**Файл:** [`orchestrator.py`](orchestrator.py)

```python
SYSTEM_PROMPT = """
Ты университетский ассистент с доступом к базе данных через MCP-инструменты.

КРИТИЧЕСКИ ВАЖНЫЕ ПРАВИЛА:
1. Ты НЕ знаешь никаких данных о студентах, расписании, оценках, преподавателях или документах без инструментов.
2. При любом вопросе о данных университета сначала используй MCP-инструмент.
3. Не выдумывай ответ из памяти.
4. Если вопрос общий — отвечай кратко и по делу.

ПРАВИЛА ОТВЕТА:
- Отвечай на языке пользователя, по умолчанию используй русский.
- Если данных нет — прямо скажи об этом.
- Если не понял запрос — уточни.
""".strip()
```

**Где используется:** В начале каждого запроса добавляется как первое сообщение в список `messages`:

**Файл:** [`orchestrator.py`](orchestrator.py) → метод `_build_messages_raw()`

```python
def _build_messages_raw(self, user_message: str, session_id: SessionId) -> list[dict[str, Any]]:
    history = self.conversation_manager.get_history_messages(session_id)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},  # <-- СЮДА
        *history,
        {"role": "user", "content": user_message},
    ]
```

---

## ⚙️ Конфигурация по умолчанию

| Параметр | Значение | Описание |
|----------|----------|----------|
| `agent_max_iterations` | **5** | Максимальное количество итераций модели за один запрос |
| `agent_max_empty_rounds` | **3** | Сколько раз модель может ответить "ничего" до остановки, проще говоря провести цепочку размышлений |
| `ollama_model` | `carstenuhlig/omnicoder-9b:latest` | Используемая модель |
| `think_mode` | `True` | Включено ли reasoning (рассуждения) |
| `agent_temperature` | `0.5` | Температура модели |

**Файл:** [`settings.py`](../../settings.py) → подхватывается в конструкторе `LLMAgent`:

**Файл:** [`orchestrator.py`](orchestrator.py)

```python
self.max_iterations = settings.agent_max_iterations
self.max_empty_rounds = settings.agent_max_empty_rounds
```

---

## 🔄 Поток выполнения (Реальный пример)

**Логи из твоего теста:**

```
Пользователь: "привет найди расписание для студентки Мамонтова Нина Аскольдовна"
    ↓
┌───────────────────────────────────────────────────────────────────────┐
│ ИТЕРАЦИЯ 1/5                                                          │
│  ↓                                                                    │
│  [LLM] Модель думает (reasoning):                                     │
│     "Нужно сначала найти студента по имени через find_student_by_name"│
│  ↓                                                                    │
│  [LLM] Возвращает: tool_call → find_student_by_name({"name": "..."})  │
│  ↓                                                                    │
│  [MCP] → Вызов инструмента find_student_by_name                       │
│  ↓                                                                    │
│  [MCP] ← Результат: {"student_id": "d148f...", "group_id": "ac5f..."} │
└───────────────────────────────────────────────────────────────────────┘
    ↓
┌──────────────────────────────────────────────────────────────────────────────┐
│ ИТЕРАЦИЯ 2/5                                                                 │
│  ↓                                                                           │
│  [LLM] Получает результат инструмента в контексте                            │
│  ↓                                                                           │
│  [LLM] Думает (reasoning):                                                   │
│     "Отлично, студентка найдена. Теперь получу расписание через get_schedule"│
│  ↓                                                                           │
│  [LLM] Возвращает: tool_call → get_schedule({"group_id": "ac54fa8f..."})     │
│  ↓                                                                           │
│  [MCP] → Вызов инструмента get_schedule                                      │
│  ↓                                                                           │
│  [MCP] ← Результат: [{"day": "Понедельник", "lessons": [...]}]               │
└──────────────────────────────────────────────────────────────────────────────┘
    ↓
┌──────────────────────────────────────────────────────────────────────┐
│ ИТЕРАЦИЯ 3/5                                                         │
│  ↓                                                                   │
│  [LLM] Получает расписание                                           │
│  ↓                                                                   │
│  [LLM] Думает (reasoning):                                           │
│     "Отлично! Расписание получено. Теперь оформлю его в удобном виде"│
└──────────────────────────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────────────────────┐
│ ИТЕРАЦИЯ 4/5                                                    │
│  ↓                                                              │
│  [LLM] Пытается сформулировать ответ                            │
│  ↓                                                              │
│  reasoning_content: ПУСТО → empty_rounds = 1                    │
└─────────────────────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────────────────────┐
│ ИТЕРАЦИЯ 5/5 (последняя)                                        │
│  ↓                                                              │
│  [LLM] Финально формулирует ответ                               │
│  ↓                                                              │
│  [LLM] Возвращает: content → "Вот расписание для Мамонтовой..." │
└─────────────────────────────────────────────────────────────────┘
    ↓
[FINAL] → Ответ отдается пользователю + сохраняется в историю сессии
```

---

## 🔄 Детальный алгоритм работы

### 1. Точка входа: получение запроса

**Файл:** [`orchestrator.py`](orchestrator.py) → метод `stream_events()`

```python
async def stream_events(
    self, user_message: str, session_id: SessionId = "default"
) -> AsyncIterator[AgentEvent]:
    session_id = self.conversation_manager.normalize_session_id(session_id)
    logger.info("[AGENT] User message for session %s: %s...", session_id, user_message[:100])
    
    # Блокируем сессию, чтобы запрос обрабатывался последовательно
    lock = self.conversation_manager.get_session_lock(session_id)
    async with lock:
        async for event in self._run_turn(user_message, session_id):
            yield event  # <-- Отдаем события (токены, tool_calls, final и т.д.)
```

---

### 2. Сбор сообщений для модели

**Файл:** [`orchestrator.py`](orchestrator.py) → метод `_build_messages_raw()`

```python
def _build_messages_raw(self, user_message: str, session_id: SessionId) -> list[dict[str, Any]]:
    history = self.conversation_manager.get_history_messages(session_id)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},  # Системный промпт
        *history,                                      # История сессии
        {"role": "user", "content": user_message},    # Текущий запрос
    ]
```

---

### 3. Основной цикл обработки запроса

**Файл:** [`orchestrator.py`](orchestrator.py) → метод `_run_turn()`

```python
async def _run_turn(self, user_message: str, session_id: SessionId) -> AsyncIterator[AgentEvent]:
    # Собираем начальные сообщения
    messages = self._build_messages_raw(user_message, session_id)
    turn_messages = [{"role": "user", "content": user_message}]
    turn_id = backlog.turn_start(session_id, user_message)  # Логирование в backlog
    
    try:
        # Открываем MCP сессию
        async with self.mcp_client.get_session() as session:
            tools = await self.mcp_client.list_tools(session)  # Получаем список инструментов
            logger.info("[AGENT] Available tools: %s", [t.get("function", {}).get("name") for t in tools])
            
            empty_rounds = 0  # Счетчик пустых раундов
            
            # === ОСНОВНОЙ ЦИКЛ ИТЕРАЦИЙ ===
            for iteration in range(self.max_iterations):
                async for event in self._handle_iteration(
                    iteration, session, session_id, turn_id,
                    messages, turn_messages, tools, empty_rounds
                ):
                    yield event
                
                # Проверяем, не превысили ли лимит пустых раундов
                if empty_rounds >= self.max_empty_rounds:
                    break
            
            # Fallback если не было финального ответа
            async for event in self._run_fallback(messages, turn_messages, session_id):
                yield event
```

---

### 4. Обработка одной итерации

**Файл:** [`orchestrator.py`](orchestrator.py) → метод `_handle_iteration()`

```python
async def _handle_iteration(self, iteration: int, session: Any, ...) -> AsyncIterator[AgentEvent]:
    logger.info("[AGENT] Iteration %s/%s - calling model...", iteration + 1, self.max_iterations)
    backlog.model_request(session_id, turn_id, iteration, messages, tools)
    
    # === ВЫЗОВ МОДЕЛИ ===
    final_message: dict[str, Any] | None = None
    async for token, final in self.llm_client.stream_completion(messages, tools):
        if token:
            yield AgentEvent("token", TokenEventData(data=token))  # --> СТРИМИМ ТОКЕНЫ
        elif final:
            final_message = final
    
    # Если модель ничего не вернула
    if final_message is None:
        final_message = self.llm_client.last_final_message
    
    if final_message is None:
        new_empty_rounds = empty_rounds + 1
        backlog.empty_round(session_id, turn_id, iteration, "", messages)
        yield AgentEvent("status", StatusEventData(
            phase="empty_round",
            iteration=iteration,
            empty_rounds=new_empty_rounds
        ))
        return
    
    # Логируем ответ модели
    backlog.model_response(
        session_id, turn_id, iteration, final_message,
        duration_ms=0, token_usage=final_message.pop("_usage", None)
    )
    
    # === ИЗВЛЕКАЕМ ДАННЫЕ ИЗ ОТВЕТА МОДЕЛИ ===
    reasoning: str | None = final_message.get("reasoning_content")      # <-- РАССУЖДЕНИЯ
    tool_calls: list[ParsedToolCall] = self.tool_parser.extract_tool_calls(final_message)  # <-- ВЫЗОВЫ ИНСТРУМЕНТОВ
    content: str = (final_message.get("content") or "").strip()          # <-- ФИНАЛЬНЫЙ ОТВЕТ
    
    # Логируем рассуждения в backlog
    if reasoning:
        backlog.empty_round(session_id, turn_id, iteration, reasoning, messages)
    
    # === ОБРАБОТКА В ЗАВИСИМОСТИ ОТ ТИПА ОТВЕТА ===
    
    # Если есть вызовы инструментов
    if tool_calls:
        async for event in self._handle_tool_calls(tool_calls, ...):
            yield event
        return  # Продолжаем итерации
    
    # Если есть финальный контент
    if content:
        async for event in self._handle_final_content(final_message, content, ...):
            yield event
        return  # Завершаем
    
    # Если пустой ответ (нет content, нет tool_calls)
    async for event in self._handle_partial_response( # Просто помыслила но решила ничего не делать
        reasoning, iteration, empty_rounds, session_id, turn_id, messages
    ):
        yield event
```

---

### 5. Обработка вызовов инструментов

**Файл:** [`orchestrator.py`](orchestrator.py) → метод `_handle_tool_calls()`

```python
async def _handle_tool_calls(
    self, tool_calls: list[ParsedToolCall], session: Any, ...
) -> AsyncIterator[AgentEvent]:
    # Сообщаем о начале обработки инструментов
    yield AgentEvent("status", StatusEventData(
        phase="tool_calls",
        iteration=iteration,
        count=len(tool_calls)
    ))
    
    # Форматируем tool_calls для истории (чтобы модель видела что она вызывала)
    final_message["tool_calls"] = self.tool_parser.format_for_model(tool_calls)
    messages.append(final_message)
    turn_messages.append(final_message)
    
    # === ВЫЗЫВАЕМ КАЖДЫЙ ИНСТРУМЕНТ ===
    for tool_call in tool_calls:
        name: str = tool_call["name"]
        arguments: dict[str, Any] = tool_call["arguments"]
        tool_call_id: str = tool_call.get("id") or f"call_{name}_{uuid.uuid4().hex[:8]}"
        
        # Логируем вызов инструмента
        backlog.tool_call(session_id, turn_id, iteration, name, arguments)
        yield AgentEvent("tool_call", ToolCallEventData(
            id=tool_call_id, name=name, arguments=arguments
        ))
        
        # === ВЫЗОВ MCP ИНСТРУМЕНТА ===
        tool_result: str = await self.mcp_client.call_tool(session, name, arguments)
        backlog.tool_result(session_id, turn_id, iteration, name, tool_result, duration_ms=0)
        
        yield AgentEvent("tool_result", ToolResultEventData(
            id=tool_call_id, name=name, result=tool_result
        ))
        
        # === СОХРАНЯЕМ РЕЗУЛЬТАТ В ИСТОРИЮ ДЛЯ МОДЕЛИ ===
        tool_message: dict[str, Any] = {
            "role": "tool",
            "content": tool_result,
            "tool_call_id": tool_call_id,
            "name": name,
        }
        messages.append(tool_message)      # <-- В ОБЩУЮ ИСТОРИЮ
        turn_messages.append(tool_message)  # <-- В ИСТОРИЮ ЭТОГО TURN'А
```

---

### 6. Обработка финального контента

**Файл:** [`orchestrator.py`](orchestrator.py) → метод `_handle_final_content()`

```python
async def _handle_final_content(
    self, final_message: dict[str, Any], content: str, messages: list[dict[str, Any]],
    turn_messages: list[dict[str, Any]], session_id: SessionId
) -> AsyncIterator[AgentEvent]:
    final_message["content"] = content
    messages.append(final_message)
    turn_messages.append(final_message)
    
    # === СОХРАНЕНИЕ В ИСТОРИЮ СЕССИИ ===
    self.conversation_manager.remember_turn(session_id, cast(TurnMessages, turn_messages))
    
    yield AgentEvent("final", FinalEventData(content=content))
```

---

### 7. Обработка частичного ответа (нет content, нет вызова инструмента)

**Файл:** [`orchestrator.py`](orchestrator.py) → метод `_handle_partial_response()`

```python
async def _handle_partial_response(
    self, reasoning: str | None, iteration: int, empty_rounds: int,
    session_id: SessionId, turn_id: TurnId, messages: list[dict[str, Any]]
) -> AsyncIterator[AgentEvent]:
    new_empty_rounds = empty_rounds + 1
    
    yield AgentEvent("status", StatusEventData(
        phase="empty_round",
        iteration=iteration,
        empty_rounds=new_empty_rounds
    ))
    
    # Добавляем рассуждения в историю, если есть
    if reasoning:
        messages.append({
            "role": "assistant",
            "content": reasoning,  # <-- РАССУЖДЕНИЯ ДОБАВЛЯЮТСЯ В ИСТОРИЮ
        })
    
    # Добавляем системный промпт, чтобы подтолкнуть модель к действию
    messages.append({
        "role": "system",
        "content": (
            "Верни только tool_calls или финальный ответ. "
            "Опирайся на предыдущие сообщения и reasoning_content и действуй"
        ),
    })
```

**Важно:** Рассуждения (reasoning) **НЕ** отдаются пользователю, но **добавляются в историю** для следующих итераций, чтобы модель могла их использовать!

---

### 8. Fallback: если итерации закончились, а ответа нет

**Файл:** [`orchestrator.py`](orchestrator.py) → метод `_run_fallback()`

```python
async def _run_fallback(
    self, messages: list[dict[str, Any]], turn_messages: list[dict[str, Any]], session_id: SessionId
) -> AsyncIterator[AgentEvent]:
    final_parts: list[str] = []
    
    # Пробуем получить финальное сообщение ещё раз
    async for token in self.llm_client.get_final_message(messages):
        final_parts.append(token)
        yield AgentEvent("token", TokenEventData(data=token))
    
    # Если всё равно пусто — дефолтный ответ
    if not final_parts:
        fallback_msg = "Извините, модель завершила работу без ответа. Попробуйте уточнить запрос."
        final_parts.append(fallback_msg)
        yield AgentEvent("token", TokenEventData(data=fallback_msg))
    
    # Сохраняем fallback ответ в историю
    turn_messages.append({"role": "assistant", "content": "".join(final_parts)})
    self.conversation_manager.remember_turn(session_id, cast(TurnMessages, turn_messages))
```

---

## 💾 Что сохраняется в сессию

### Структура истории сессии

В `session_store` сохраняется **вся** история общения в следующем формате:

**Файл:** [`session_store`](../../api/sessions.py) → метод `history_messages()`

```python
# Пример для запроса "найди расписание для Мамонтова Нина Аскольдовна"
session_store.history_messages(session_id) → 
[
    # === ПЕРВЫЙ ЗАПРОС ===
    {"role": "user", "content": "привет найди расписание для студентки Мамонтова Нина Аскольдовна"},
    
    # Ответ агента с вызовом первого инструмента
    {
        "role": "assistant",
        "content": "",  # Пустой контент, так как были tool_calls
        "tool_calls": [
            {
                "id": "call_find_student_by_name_ab12cd34",
                "type": "function",
                "function": {
                    "name": "find_student_by_name",
                    "arguments": '{"name": "Мамонтова Нина Аскольдовна"}'
                }
            }
        ]
    },
    
    # Результат первого инструмента
    {
        "role": "tool",
        "content": '{"ok": true, "data": {"student_id": "d14c888f-a666-4090-8f83-a0596334d85c", "group_id": "ac54fa8f-73b4-4e31-a500-37833773c8c3"}}',
        "tool_call_id": "call_find_student_by_name_ab12cd34",
        "name": "find_student_by_name"
    },
    
    # Ответ агента с вызовом второго инструмента
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_get_schedule_5678ef90",
                "type": "function",
                "function": {
                    "name": "get_schedule",
                    "arguments": '{"group_id": "ac54fa8f-73b4-4e31-a500-37833773c8c3"}'
                }
            }
        ]
    },
    
    # Результат второго инструмента
    {
        "role": "tool",
        "content": '[{"day": "Понедельник", "lessons": [{"time": "09:00", "discipline": "Математика", ...}]}]',
        "tool_call_id": "call_get_schedule_5678ef90",
        "name": "get_schedule"
    },
    
    # Финальный ответ агента
    {"role": "assistant", "content": "Вот расписание для Мамонтовой Нины Аскольдовны:\n\nПонедельник:\n- 09:00-10:30: Математика..."}
]
```

---

### Куда и как сохраняется

Сохранением занимается `ConversationManager`:

**Файл:** [`conversation.py`](conversation.py)

```python
class ConversationManager:
    def __init__(self) -> None:
        self._session_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()

    def get_history_messages(self, session_id: SessionId) -> list[dict[str, Any]]:
        """Получить историю сообщений для сессии."""
        return session_store.history_messages(session_id)

    def remember_turn(self, session_id: SessionId, messages: TurnMessages) -> None:
        """Сохранить turn (обмен сообщениями) в историю сессии."""
        session_store.append_turn(session_id, cast(list[dict[str, Any]], messages))
        logger.debug("[CONVERSATION] Stored turn for session %s", session_id)

    @staticmethod
    def normalize_session_id(session_id: str) -> SessionId:
        """Нормализовать ID сессии."""
        return session_store.normalize_session_id(session_id)

    def get_session_lock(self, session_id: SessionId) -> asyncio.Lock:
        """Получить или создать блокировку для сессии."""
        return self._session_locks.setdefault(session_id, asyncio.Lock())
```

**Что сохраняется:**
- ✅ Все сообщения пользователя (`role: user`)
- ✅ Все ответы модели (`role: assistant`)
- ✅ Все вызовы инструментов (`role: assistant` с `tool_calls`)
- ✅ Все результаты инструментов (`role: tool`)
- ❌ **Рассуждения (reasoning_content) НЕ сохраняются в историю сессии** — только в backlog для дебага и во время размышлений

---

## 🧠 Как передаются рассуждения (reasoning_content)

### Где генерируются

**Файл:** [`llm_client.py`](llm_client.py) → метод `_build_response_dict()`

```python
def _build_response_dict(self, msg_obj: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "role": msg_obj.role or "assistant",
        "content": msg_obj.content or "",
    }
    
    # ...
    
    reasoning = getattr(msg_obj, "reasoning_content", None)
    if reasoning:
        result["reasoning_content"] = reasoning  # <-- ПОЛЕ РАССУЖДЕНИЙ
    
    return result
```

---

### Куда передаются

1. **Логируются в backlog** (для отладки):

**Файл:** [`orchestrator.py`](orchestrator.py)

```python
if reasoning:
    backlog.empty_round(session_id, turn_id, iteration, reasoning, messages)
```

2. **Добавляются в messages для следующей итерации** (чтобы модель видела свои мысли):

```python
if reasoning:
    messages.append({
        "role": "assistant",
        "content": reasoning,  # <-- РАССУЖДЕНИЯ ДОБАВЛЯЮТСЯ В ИСТОРИЮ ДЛЯ МОДЕЛИ
    })
```

3. **НЕ отдаются пользователю** — только для внутреннего использования

---

## Защита от замыкания

Агент защищен от бесконечных циклов двумя параметрами:

| Параметр | Значение | Что происходит |
|----------|----------|-----------------|
| `max_iterations` | 5 | Максимум 5 вызовов модели на один запрос |
| `max_empty_rounds` | 3 | Если 3 раза подряд модель не дала ни content, ни tool_calls — прерываем |

**Реализация:**

**Файл:** [`orchestrator.py`](orchestrator.py) → метод `_run_turn()`

```python
for iteration in range(self.max_iterations):
    async for event in self._handle_iteration(...):
        yield event
    
    if empty_rounds >= self.max_empty_rounds:
        break  # <-- ВЫХОД ИЗ ЦИКЛА
```

Если итерации закончились, а финального ответа нет — вызывается `_run_fallback()`:

```python
# Fallback если не было финального ответа
async for event in self._run_fallback(messages, turn_messages, session_id):
    yield event
```

---

## 🎯 Резюме: Как агент решает проблемы

### Алгоритм в 5 шагов

```
1. 📥 ПОЛУЧИТЬ ЗАПРОС
   → Собрать: SYSTEM_PROMPT + история сессии + новый запрос пользователя
   → Заблокировать сессию (чтобы не мешали другие запросы)

2. 🤖 ВЫЗВАТЬ МОДЕЛЬ
   → Стримить токены пользователю в реальном времени
   → Дождаться сообщения

3. 🔍 АНАЛИЗИРУЕМ ОТВЕТ
   → Если есть tool_calls → перейти к шагу 4
   → Если есть content → отдать пользователю (FINAL EVENT)
   → Если пусто → empty_rounds++ и вернуть модель к работе

4. 🛠️ ВЫЗВАТЬ ИНСТРУМЕНТЫ
   → Для каждого tool_call: вызвать MCP инструмент
   → Добавить результат инструмента в историю
   → Вернуться к шагу 2 (новая итерация)

5. ✅ ЗАВЕРШЕНИЕ
   → Если max_iterations или max_empty_rounds → fallback с дефолтным сообщением
   → Иначе → отдать финальный ответ и сохранить в историю
```

---

### Ключевые принципы работы

| Принцип | Реализация |
|---------|------------|
| **НЕ выдумывать данные** | Системный промпт явно требует использовать инструменты для любых данных о университете |
| **Использовать инструменты** | Модель сама решает, какие MCP инструменты вызвать на основе контекста |
| **Сохранять контекст** | Вся история (вопросы, ответы, результаты инструментов) передаётся модели в каждом запросе |
| **Не замыкаться** | `max_iterations=5` и `max_empty_rounds=3` защищают от бесконечных циклов |
| **Стримить ответ** | Токены отдаются пользователю по мере генерации (SSE) |
| **Сохранять рассуждения** | `reasoning_content` передаётся модели в следующей итерации, но не показывается пользователю |

---

### Что гарантирует корректность ответов

1. **Системный промпт** — заставляет модель использовать инструменты, а не выдумывать
2. **MCP инструменты** — единственный источник достоверных данных о университете
3. **История сессии** — модель видит предыдущие вызовы инструментов и их результаты
4. **Reasoning в контексте** — модель видит свои собственные рассуждения из предыдущих итераций
5. **Backlog** — логирует все действия для отладки и анализа

---

## 🎯 Итог в одном предложении

> **Агент берёт вопрос пользователя, крутит модель в цикле (максимум 5 итераций), на каждой итерации модель может подумать (reasoning), вызвать инструменты через MCP, получить результаты и продолжить думать — пока не даст финальный ответ, который сохраняется в историю сессии и отдаётся пользователю по SSE, с защитой от замыкания через лимиты на итерации и пустые раунды.**

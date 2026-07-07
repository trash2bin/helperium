# Agent Tutor — Embeddable Chat Widget

Виджет чата для встраивания на любой сайт. Vanilla JS, без зависимостей, изолирован в Shadow DOM.

## Быстрый старт

```html
<script src="/embed/embed.js"
        data-agent="shop-assistant"
        data-api-base="https://your-server.com"
        data-title="Помощник по товарам"
        data-greeting="Спрашивайте о товарах!"
        data-accent="#0f766e"
        data-position="right">
</script>
```

Всё. Виджет появится в правом нижнем углу.

## Параметры конфигурации

Все параметры задаются через `data-*` атрибуты на `<script>`.

| Атрибут | По умолчанию | Описание |
|---|---|---|
| `data-agent` | _(обязательный)_ | Имя агента — определяет SSE endpoint `/api/chat/{agent}` |
| `data-api-base` | `window.location.origin` | Базовый URL сервера с API |
| `data-title` | `"Ассистент"` | Заголовок виджета (жирный текст в шапке) |
| `data-greeting` | `"Чем могу помочь?"` | Приветственное сообщение при пустой истории |
| `data-accent` | `"#0f766e"` | Акцентный цвет (CSS hex, поддерживает transparent) |
| `data-position` | `"right"` | Положение: `"right"` или `"left"` |

## Как это работает

### Архитектура

```
Браузер (ваш сайт)
  │
  ├── <script src="/embed/embed.js" data-agent="shop">  ← загружает виджет
  │
  └── POST /api/chat/{agent}  ← SSE endpoint
        Body: { message: "...", session_id: "..." }
        Response: text/event-stream
          data: {"type":"token","text":"..."}
          data: {"type":"tool_call","name":"find_products"}
          data: {"type":"final","text":"..."}
          data: {"type":"done"}
```

### Shadow DOM изоляция

Виджет создаёт свой хост-элемент с `attachShadow({ mode: 'open' })`. Стили и разметка внутри Shadow DOM не пересекаются с CSS сайта. Единственное, что выходит наружу — позиционирование `.at-trigger` и `.at-panel` (через `position: fixed`).

### Хранение сессий

- **sessionStorage**: история сообщений (ключ `at_messages_{agent}`) и текущий session_id (ключ `at_session_{agent}`)
- **sessionStorage** привязан к вкладке браузера — при закрытии вкладки история теряется
- На сервере история хранится в SQLite (смотрим `SessionStore` в `api-service/src/api_service/sessions.py`)
- При переключении агента сообщения предыдущего агента не теряются — лежат под своим ключом

### SSE протокол

Виджет шлёт `POST /api/chat/{agent}` с `{ "message": "...", "session_id": "..." }` и читает SSE поток:

| Тип события | Описание |
|---|---|
| `token` | Очередной токен ответа. `{ "type":"token", "text":"частичный текст..." }` |
| `tool_call` | Агент вызвал инструмент. `{ "type":"tool_call", "name":"find_products" }` |
| `final` | Финальный текст ответа (может отличаться от суммы токенов). `{ "type":"final", "text":"полный ответ" }` |
| `done` | Поток завершён. Виджет сохраняет сообщение в sessionStorage. |
| `error` | Ошибка. `{ "type":"error", "text":"сообщение ошибки" }` |

## Интеграция с app.js (Agent Dashboard)

Если виджет используется вместе с [Agent Dashboard](../../demo/web/static/app.js), работает глобальный bridge:

```js
// app.js устанавливает state.agentId через localStorage и dropdown
window.__agentTutorSetAgent("shop-assistant");
```

Bridge делает:
1. Меняет `CONFIG.agent` на нового агента
2. Обновляет ключи sessionStorage (`at_messages_{agent}`)
3. Создаёт новый session_id
4. Обновляет заголовок виджета (показывает имя агента)
5. Очищает сообщения и загружает историю нового агента

Актуальное состояние агента хранится в `localStorage` под ключом `agentTutorAgentId`.
Виджет проверяет его при загрузке страницы и синхронизируется автоматически.

## Кастомизация через CSS переменные

Внутри Shadow DOM используются CSS-переменные. Можно переопределить через `data-accent`, но при желании — и через Shadow DOM:

```css
.at-root {
  --accent: #0f766e;
  --muted: #64748b;
  --line: #e2e8f0;
  --panel: #ffffff;
  --rose: #e11d48;
  --blue: #2563eb;
  --radius: 8px;
}
```

## Запуск в dev-режиме

```bash
# Сервер api-service раздаёт embed.js и embed.css
# Web-сервис проксирует /embed/* на api-service:
#   demo/web/server.py → _proxy_to_api(request, "/embed/{path}")
```

## Структура файлов

```
api-service/embed/
├── embed.js      # Виджет (единственный файл для встраивания)
├── embed.css     # CSS-запасной (можно добавить на страницу вручную)
└── README.md     # Этот файл
```

## Совместимость

- **Браузеры**: все современные (Chrome, Firefox, Safari, Edge)
- **ES**: ES5 (транспиляция не нужна)
- **Зависимости**: нет

## Продвинутое: несколько виджетов на одной странице

Можно разместить несколько скриптов для разных агентов. Каждый создаст свой хост в Shadow DOM с независимым состоянием.

```html
<script src="/embed/embed.js" data-agent="shop" data-title="Магазин"></script>
<script src="/embed/embed.js" data-agent="support" data-title="Поддержка" data-position="left"></script>
```

## Отладка

- В консоли: `window.__agentTutorSetAgent — глобальный bridge`
- `sessionStorage` ключи: `at_messages_{agent}`, `at_session_{agent}`
- `localStorage` ключ: `agentTutorAgentId` (используется dashboard'ом)

# Helperium — Embeddable Chat Widget

Виджет чата для встраивания на любой сайт. TypeScript, Shadow DOM, 0 runtime-зависимостей.

## Быстрый старт

```html
<script src="/embed/embed.js"
        data-agent="shop-assistant"
        data-api-base="https://your-server.com"
        data-title="Помощник по товарам"
        data-greeting="Спрашивайте о товарах!"
        data-accent="#0f766e"
        data-position="right"
        data-placeholder="Наберите вопрос..."
        data-width="min(420px, calc(100vw - 28px))"
        data-header-color="#0f766e"
        data-show-header="true">
</script>
```

Всё. Виджет появится в правом нижнем углу.

### Альтернатива: window.EMBED_CONFIG

Если нужно задать конфиг программно (без data-* атрибутов):

```html
<script>
  window.EMBED_CONFIG = {
    agent: 'shop-assistant',
    apiBase: 'https://your-server.com',
    title: 'Помощник',
    accent: '#0f766e'
  };
</script>
<script src="/embed/embed.js"></script>
```

## Параметры конфигурации

Все параметры задаются через `data-*` атрибуты на `<script>`.

| Атрибут | По умолчанию | Описание |
|---|---|---|
| `data-agent` | _(обязательный)_ | Имя агента — определяет SSE endpoint `/api/chat/{agent}` |
| `data-api-base` | `window.location.origin` | Базовый URL сервера с API |
| `data-title` | `"Assistant"` | Заголовок виджета (жирный текст в шапке) |
| `data-greeting` | `"How can I help?"` | Приветственное сообщение при пустой истории |
| `data-accent` | `"#0f766e"` | Акцентный цвет (CSS hex, поддерживает transparent) |
| `data-position` | `"right"` | Положение: `"right"` или `"left"` |
| `data-placeholder` | `"Ask a question..."` | Текст-плейсхолдер в поле ввода |
| `data-width` | `"min(380px, calc(100vw - 28px))"` | Ширина панели (любое CSS-значение) |
| `data-height` | `"min(620px, calc(100vh - 44px))"` | Высота панели (любое CSS-значение) |
| `data-trigger-offset-bottom` | `"16px"` | Отступ от нижнего края для кнопки и панели |
| `data-header-color` | (равно accent) | Цвет фона шапки (если нужен отличный от accent) |
| `data-show-header` | `"true"` | Показывать шапку: `"true"` или `"false"` |
| `data-bot-bubble-color` | `"#eef3f4"` | Цвет фона пузырька ассистента |
| `data-bot-bubble-text` | `"var(--ink)"` | Цвет текста пузырька ассистента |
| `data-lang` | auto | Язык: `"ru"` или `"en"`. Если не указан — определяется по `navigator.language` |
| `data-voice-input` | `"true"` | Голосовой ввод: `"true"` или `"false"` |
| `data-voice-output` | `"true"` | Голосовой вывод: `"true"` или `"false"` (зарезервировано) |
| `data-voice-toggle` | `"telegram"` | Режим голоса: `"telegram"` (зажать = запись, отпустить = отправить; если есть текст в поле — показывает кнопку отправки вместо микрофона) или `"classic"` (toggle on/off) |

### Сообщения об ошибках

Вместо сырых исключений (например `litellm.RateLimitError`) пользователь видит человеческое сообщение на выбранном языке:

| Ситуация | Русский | English |
|---|---|---|
| Лимит запросов (rate limit) | Сервер временно перегружен. Пожалуйста, повторите ваш вопрос через несколько секунд. | Server is temporarily overloaded. Please retry your question in a few seconds. |
| Ошибка доступа к модели | Ошибка доступа к модели. Попробуйте позже или обратитесь к администратору. | Model access error. Please try again later or contact the administrator. |
| Диалог слишком длинный | Диалог слишком длинный. Пожалуйста, начните новый разговор. | The conversation is too long. Please start a new chat. |
| Модель не отвечает | Модель не отвечает. Пожалуйста, попробуйте снова или задайте более короткий вопрос. | The model is not responding. Please try again or ask a shorter question. |
| Внутренняя ошибка | Извините, произошла внутренняя ошибка. Попробуйте ещё раз. | Sorry, an internal error occurred. Please try again. |

Язык определяется:
1. Через `data-lang="ru"` на `<script>` (для embed-виджета)
2. Через HTTP-заголовок `Accept-Language` (для HTTP API)

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
          data: {"type":"tool_call","name":"find_products"}
          data: {"type":"final","text":"..."}
          data: {"type":"done"}
```

### Shadow DOM изоляция

Виджет создаёт свой хост-элемент с `attachShadow({ mode: 'open' })`. Стили и разметка внутри Shadow DOM не пересекаются с CSS сайта.

### Хранение сессий

- **sessionStorage**: история сообщений (ключ `at_messages_{agent}`) и текущий session_id (ключ `at_session_{agent}`)
- **localStorage**: выбранный агент (ключ `agentTutorAgentId`) — для синхронизации с admin dashboard

### SSE протокол

Виджет шлёт `POST /api/chat/{agent}` с `{ "message": "...", "session_id": "..." }` и читает SSE поток:

| Тип события | Описание |
|---|---|
| `final` | Один проверенный output guard финальный ответ. Это buffered delivery, не token streaming. `{ "type":"final", "text":"полный ответ" }` |
| `tool_call` | Агент вызвал инструмент. `{ "type":"tool_call", "name":"find_products" }` |
| `audio` | Голосовой ответ (base64 WAV). `{ "type":"audio", "data":"base64..." }` |
| `done` | Поток завершён. |
| `error` | Ошибка. `{ "type":"error", "text":"сообщение ошибки" }` |

## Интеграция с app.js (Agent Dashboard)

Глобальный bridge для runtime переключения агента:

```js
// Переключить агента
window.__agentTutorSetAgent("shop-assistant");
```

Bridge делает:
1. Меняет агента в конфиге
2. Обновляет ключи sessionStorage
3. Создаёт новый session_id
4. Обновляет заголовок виджета
5. Очищает сообщения и загружает историю нового агента
6. Сохраняет выбор в `localStorage.agentTutorAgentId`

При загрузке страницы виджет проверяет `localStorage.agentTutorAgentId` и автоматически синхронизируется.

## Голосовой ввод

### Classic режим (`data-voice-toggle="classic"`)

Кнопка микрофона рядом с textarea. Нажатие = вкл/выкл запись. Работает параллельно с текстовым вводом.

### Telegram режим (`data-voice-toggle="telegram"`)

Одна кнопка, которая меняется в зависимости от ввода:

- **Пустое поле** → кнопка микрофона. Зажмите и удерживайте для записи, отпустите — отправится голосовое сообщение.
- **Текст введён** → кнопка отправки (send) с анимацией замены.

Анимация: кнопка плавно поворачивается и масштабируется при переключении.

```html
<!-- Telegram-style voice -->
<script src="/embed/embed.js"
        data-agent="shop"
        data-voice-toggle="telegram"
        data-voice-input="true">
</script>
```

## Сборка (для разработчиков)

```bash
cd api-service/embed
npm install
npm run build        # typecheck + esbuild → dist/embed.js + dist/embed.css
npm run test         # vitest (70 тестов)
npm run dev          # watch mode
```

### Структура файлов

```
api-service/embed/
├── src/                    # TypeScript source
│   ├── index.ts           # Entry point, auto-init, event wiring
│   ├── config.ts          # Parse data-* attributes + window.EMBED_CONFIG
│   ├── types.ts           # All interfaces (WidgetConfig, SSE events, etc.)
│   ├── dom.ts             # buildWidget() + DOM query helpers
│   ├── sse.ts             # streamChat() + shared SSE stream reader
│   ├── voice.ts           # MediaRecorder, voice streaming, audio playback
│   ├── markdown.ts        # Lightweight markdown → HTML
│   ├── messages.ts        # addMessage(), restoreHistory()
│   ├── storage.ts         # sessionStorage + session-aware storage
│   ├── tools.ts           # Tool strip (makeToolStrip, ensureToolStrip)
│   ├── typewriter.ts      # Token-by-token rendering
│   ├── icons.ts           # SVG icons (chat, close, send, mic)
│   └── icons/              # SVG icon files
│       ├── chat.svg
│       ├── close.svg
│       ├── mic-off.svg
│       ├── mic.svg
│       └── send.svg
├── css/                    # Component CSS files
│   ├── variables.css      # Design tokens
│   ├── root.css           # .at-root (all: initial)
│   ├── trigger.css        # Floating button
│   ├── panel.css          # Chat panel
│   ├── header.css         # Panel header
│   ├── messages.css       # Message bubbles
│   ├── form.css           # Input area
│   ├── tools.css          # Tool strip
│   ├── animations.css     # Keyframes
│   └── responsive.css     # Mobile adjustments
├── dist/                   # Build output
│   ├── embed.js           # Bundled widget (IIFE, minified, ~44KB)
│   └── embed.css          # CSS (standalone, ~19KB)
├── tests/                  # Vitest unit tests (70 tests)
├── build.sh               # Concat CSS → esbuild bundle
├── package.json            # 0 runtime deps, 3 dev deps
├── tsconfig.json           # strict mode
├── vitest.config.ts        # node + jsdom for DOM tests
└── README.md               # This file
```

### Зависимости

- **Runtime**: 0
- **Dev**: esbuild, typescript, vitest

### TypeScript

Строгий режим: `strict`, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`, `verbatimModuleSyntax`.

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

## Совместимость

- **Браузеры**: все современные (Chrome, Firefox, Safari, Edge)
- **ES**: ES2018 (транспиляция не нужна)
- **Зависимости**: нет

## Продвинутое: несколько виджетов на одной странице

Можно разместить несколько скриптов для разных агентов. Каждый создаст свой хост в Shadow DOM с независимым состоянием.

```html
<script src="/embed/embed.js" data-agent="shop" data-title="Магазин"></script>
<script src="/embed/embed.js" data-agent="support" data-title="Поддержка" data-position="left"></script>
```

## Отладка

- `window.__agentTutorSetAgent` — глобальный bridge для переключения агентов
- `sessionStorage` ключи: `at_messages_{agent}`, `at_session_{agent}`
- `localStorage` ключ: `agentTutorAgentId` (используется dashboard'ом)

## CSP для сайта, куда встраивается виджет

Если сайт использует Content-Security-Policy, ему нужно разрешить:

```
script-src https://ваш-сервер.com;
connect-src https://ваш-сервер.com;
```

Виджет **не** использует inline-скрипты, `style-src` не нужен благодаря Shadow DOM.

## Ссылки

- [API contracts](doc/agents/api-contracts.md) — HTTP и SSE контракты виджета
- [MCP session lifecycle](doc/agents/mcp-session-lifecycle.md) — MCP v2 negotiate

---
**Last verified:** 2026-08-21 (working tree) — documentation links added.

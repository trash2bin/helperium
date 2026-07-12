---
name: browser-e2e-test
description: "Manual E2E тестирование Helperium через живой браузер (Playwright): admin dashboard, tenant persistence, write-tool approval, demo web UI."
---

# Browser E2E Testing — Helperium

Этот skill описывает, как тестировать Helperium через браузер с помощью Playwright.

**Когда использовать:**
- Нужно проверить, что тенанты пережили restart (persistence)
- Нужно добавить tenant через admin dashboard и убедиться, что он работает в demo UI
- Нужно проверить write-tool approval flow
- Нужно убедиться, что upload SQLite через UI работает
- Нужно найти JS/network ошибки на UI

**Инструменты:** `playwright_browser_*` и `playwright_browser_run_code_unsafe` (для сложных сценариев).

## 🚀 Pre-flight

Сначала определись **кто ты сейчас**: [👤 Сценарии использования](#-сценарии-использования-кто-и-зачем-тестирует) — вуз, магазин, админ или production.
От этого зависит какой seed выбрать и что проверять.

Перед тестом сервисы должны быть запущены:

```bash
cd /Users/ivan/code/helperium
./scripts/dev.sh start   # или docker compose up -d
```

Убедись что `/admin/tenants` отвечает:

```bash
curl -s -H "Authorization: Bearer secret" http://127.0.0.1:8084/admin/tenants
```

## 👤 Сценарии использования (кто и зачем тестирует)

В зависимости от того, какую роль ты сейчас играешь — меняются ожидания от теста.

### 🏫 Владелец вуза (scenario `sqlite-testseed`)

**Кто:** Иван, проректор по цифровизации. Хочет поставить AI-агента студентам.

**Что за БД:** университетская — `groups`, `students`, `teachers`, `disciplines`, `grades`, `schedule`.

**Чего боится:**
- Агент напишет не те данные (исправит оценки, удалит студента)
- SQL-инъекция через промпт
- Утечка данных студентов (ФИО, оценки)

**Что должно работать:**
- Read-only доступ к БД (`read_only: true` в конфиге)
- Write-tool approval: агент может ТОЛЬКО читать, пока админ не аппрувнет write-тулы
- Readonly DSN (`readonly_dsn`) — второй read-only коннект если настоящая БД на PG

**Seed:**
```bash
uv run agent-db materialize sqlite-testseed
uv run agent-db tenant register sqlite-testseed --id default
# Проверить что students доступны:
curl -s -H "X-Tenant-ID: default" http://127.0.0.1:8084/students | head -3
# Проверить что нельзя писать (если write-tool не аппрувнут):
curl -s -X POST -H "X-Tenant-ID: default" -H "Content-Type: application/json" \
  http://127.0.0.1:8084/students -d '{"name":"test"}' | python3 -m json.tool
```

### 🏪 Владелец магазина (scenario `shop`)

**Кто:** Олег, владелец интернет-магазина. Хочет чтобы AI-агент отвечал покупателям про товары и заказы.

**Что за БД:** торговая — `categories`, `products`, `customers`, `orders`, `order_items`, `reviews`.

**Чего боится:**
- Агент наврет про цену/наличие
- Данные о заказах утекут конкурентам
- Виджет не работает на мобилках

**Что должно работать:**
- Tenant isolation: данные магазина не видны другим tenant'ам
- READ-ONLY: агент только читает, не пишет (даже админ не может включить write-тулы без approval'а)
- SSE стриминг: покупатель видит "печатает…" а не белую страницу
- Shadow DOM виджет: работает на любом сайте без конфликтов CSS

**Seed:**
```bash
uv run agent-db materialize shop
uv run agent-db tenant register shop --id shop --config-path data-service/testdata/scenarios/shop/config.json
# Проверить товары:
curl -s -H "X-Tenant-ID: shop" http://127.0.0.1:8084/products | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'{len(d)} products')"
```

### 🏢 Администратор (dev/ops)

**Кто:** Ты, когда запускаешь тесты. Отвечаешь за то что вся система работает.

**Чего боится:** после restart данные tenant'ов пропадут, write-tool approval слетит, аплоад SQLite не работает.

### ☁️ Владелец PostgreSQL (production)

Своя настоящая БД на PostgreSQL. Подключается как tenant через конфиг с read-only DSN:

```bash
# Пример конфига для реальной БД: specs/config.postgres.json
# readonly_dsn = postgres://readonly:password@host:5432/db?sslmode=require
# read_only = true
# introspection.enabled = true  ← авто-сканирование всех таблиц

# Зарегистрировать real-tenant с PG:
curl -s -X POST http://127.0.0.1:8084/admin/tenants \
  -H "Authorization: Bearer secret" \
  -H "Content-Type: application/json" \
  -d @specs/config.postgres.json

# Проверить что интроспекция нашла таблицы:
curl -s -H "X-Tenant-ID: production" http://127.0.0.1:8084/admin/manifest | python3 -m json.tool | head -20

# Проверить что read-only работает (запись должна упасть):
curl -s -X POST -H "X-Tenant-ID: production" -H "Content-Type: application/json" \
  http://127.0.0.1:8084/groups -d '{"name":"hack"}'
# → 403 Forbidden или 500 — read_only сработал
```

### 💡 Шпаргалка: какой сценарий что проверяет

| Персона | Сценарий seed | Данные | Ключевая проверка |
|---|---|---|---|
| 🏫 Вуз | `sqlite-testseed` | students, teachers, grades… | Read-only, write-tool approval, no data leak |
| 🏪 Магазин | `shop` | products, orders, customers… | Tenant isolation, SSE streaming, widget |
| ☁️ Production | `config.postgres.json` | любые PG таблицы | Readonly DSN, introspection, safety |
| 🏢 Dev/Ops | любой | — | Persistence после restart, admin UI upload |

## 🏪 Типовой тест

### 1. Login в admin dashboard

```js
// 1. Navigate
await page.goto('http://127.0.0.1:8085');

// 2. Alpine.js может помнить старый tokenSet через localStorage
//    При проблемах — очистить:
await page.evaluate(() => localStorage.clear());
await page.reload();
await page.waitForTimeout(500);

// 3. Заполнить поле пароля напрямую (Alpine x-model ловит input event)
await page.evaluate(() => {
  const input = document.querySelector('input[type="password"]');
  if (input) {
    input.value = 'secret';
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }
});
await page.waitForTimeout(200);

// 4. Кликнуть "Войти"
await page.getByRole('button', { name: 'Войти' }).click();
await page.waitForTimeout(1000);
```

**Важно:** Alpine.js использует `x-model`, который слушает `input` event. Заполнение через `fill()` не всегда работает, потому что элемент может быть невидим. `dispatchEvent(new Event('input'))` гарантирует что Alpine увидит изменение.

### 2. Проверка списка тенантов

```js
// Navigate to Тенанты
await page.getByText('Тенанты').first().click();
await page.waitForTimeout(500);

// Snap
const text = await page.evaluate(
  () => document.body.innerText.substring(0, 1500)
);
console.log('TENANTS:', text);
// Ожидается: default active, shop active (если добавлен)
```

### 3. Проверка demo web UI (данные из БД)

```js
// Open demo
await page.goto('http://127.0.0.1:8080');
await page.waitForTimeout(3000);  // wait for data to load

// Check ARIA snapshot
const snapshot = await page.evaluate(
  () => document.body.innerText.substring(0, 1000)
);
// Ожидается: список сущностей с count (Categories 3, Products 4, ...)
```

## 💬 Тестирование чата с агентом через demo web

### Архитектура чата

```
Браузер (http://127.0.0.1:8080)
  │
  ├── демо UI (app.js)       ← отображает данные + виджет чата
  │     │
  │     └── POST /api/chat     ← SSE прокси через web:8080
  │           │
  │           └── POST /api/chat  → api-service:8081 → orchestrator → LLM (LiteLLM)
  │                                                         │
  │                                                         └── MCP tools → mcp-gateway → data-service → SQL
  │
  └── SSE поток: token → tool_call → token → final → done
```

Чат-виджет встроен прямо в демо страницу (кнопка в правом нижнем углу).

### Как открыть чат в браузере

Виджет находится в **Shadow DOM**. Обычные `page.locator()` и `document.querySelector()` туда не достают — нужно лезть через `host.shadowRoot`.

```js
// 1. Открыть демо
await page.goto('http://127.0.0.1:8080');
await page.waitForTimeout(2000);

// 2. Клик по триггеру чата (через Shadow DOM)
await page.evaluate(() => {
  const host = document.querySelector('[id^="helperium-widget-"]');
  if (!host || !host.shadowRoot) throw new Error('Widget host not found');
  const trigger = host.shadowRoot.querySelector('.at-trigger');
  if (!trigger) throw new Error('Trigger not found');
  trigger.click();
});
await page.waitForTimeout(500);

// 3. Выбрать агента (через main DOM — selector вне Shadow DOM)
await page.evaluate(() => {
  const select = document.getElementById('agentSelect');
  if (select) {
    select.value = 'shop';
    select.dispatchEvent(new Event('change', { bubbles: true }));
  }
});
await page.waitForTimeout(500);

// 4. Написать сообщение через Shadow DOM
await page.evaluate(() => {
  const host = document.querySelector('[id^="helperium-widget-"]');
  if (!host || !host.shadowRoot) return;
  const textarea = host.shadowRoot.querySelector('.at-form textarea');
  if (!textarea) return;
  textarea.value = 'Покажи всех студентов';
  textarea.dispatchEvent(new Event('input', { bubbles: true }));
});

// 5. Отправить (через Shadow DOM — submit form)
await page.evaluate(() => {
  const host = document.querySelector('[id^="helperium-widget-"]');
  if (!host || !host.shadowRoot) return;
  const submitBtn = host.shadowRoot.querySelector('.at-form button[type="submit"]');
  if (submitBtn) submitBtn.click();
});

// 6. Ждать ответ (SSE стриминг — может быть долго если LLM думает)
await page.waitForTimeout(20000);  // 20+ секунд на ответ LLM
```

**Важно:** После установки `textarea.value` нужно обязательно `dispatchEvent(new Event('input', {bubbles: true}))` — иначе Shadow DOM не увидит изменение.

### Или через встраиваемый виджет (embed.js)

Виджет можно загрузить отдельно на любую страницу:

```html
<script src="/embed/embed.js"
        data-agent="shop"
        data-api-base="http://127.0.0.1:8080"
        data-title="Помощник"
        data-greeting="Спрашивайте о товарах!"
        data-accent="#0f766e"
        data-position="right">
</script>
```

Или прямо в Playwright:

```js
await page.goto('http://127.0.0.1:8080');
// Виджет уже встроен — кнопка в правом нижнем углу с иконкой
```

### SSE протокол (что приходит от LLM)

После отправки сообщения, сервер отвечает SSE потоком (`text/event-stream`):

```
data: {"type":"token","text":"частичный "}
data: {"type":"token","text":"текст "}
data: {"type":"tool_call","name":"find_students"}   ← агент вызвал MCP тул
data: {"type":"token","text":"вот "}
data: {"type":"token","text":"результаты"}
data: {"type":"final","text":"полный ответ"}        ← финальный текст
data: {"type":"done"}                                 ← стрим завершён
```

| Тип события | Что означает |
|---|---|
| `token` | Очередной токен от LLM (частичный текст). Виджет добавляет в окно по мере получения. |
| `tool_call` | Агент решил вызвать MCP инструмент (например `find_products`). Виджет показывает "🔍 Агент ищет данные...". |
| `final` | Финальный ответ LLM. Может отличаться от суммы токенов (LLM иногда переписывает ответ пост-фактум). |
| `done` | Поток завершён. Виджет сохраняет сообщение в sessionStorage. |
| `error` | Ошибка (нет LLM, нет tenant'а, таймаут). `{"type":"error","text":"..."}` |

### Как смотреть SSE и сетевые запросы в Playwright

```js
// Перехватить все сетевые запросы к /api/chat
const [response] = await Promise.all([
  page.waitForResponse(resp => resp.url().includes('/api/chat')),
  // ... action that triggers the request
]);

// Читать SSE поток
const streamReader = response.body().getReader();
// ... или просто посмотреть консоль:

// Включить мониторинг консоли (все console.log из браузера видны)
// Использовать playwright_browser_console_messages({ level: "info" })
```

## ✅ Валидация ответа LLM

### Как понять что ответ корректный

1. **SSE поток дошёл до `done`** — если последнее событие `done`, значит агент завершил ответ
2. **Нет `error` событий** — если был `error`, значит что-то сломалось (нет LLM, нет конфига, таймаут)
3. **В ответе есть данные из БД** — LLM должен вернуть что-то что связано с запросом
4. **Tool call был успешным** — если агент вызвал тул, проверь что в ответе есть результат

### Что проверять если ответ невалидный

#### 1. Ошибка "Ollama недоступна"

Симптом: на странице написано `API: Ollama недоступна`. После отправки сообщения сразу `error`.

Причина: LiteLLM не может достучаться до Ollama или другого LLM-провайдера.

Что делать:
- Убедиться что Ollama запущена: `ollama list`
- Проверить `.env`: `LLM_PROVIDER`, `OLLAMA_BASE_URL`, `LITELLM_API_KEY`
- Попробовать прямой запрос: `curl http://localhost:11434/api/generate -d '{"model":"qwen2.5","prompt":"hi"}'`
- Проверить логи api-service: `.data/logs/api.log`

#### 2. Ответ пустой или оборвался

Симптом: SSE приходят token'ы, потом `final` но текст пустой/короткий.

Причина:
- LLM вернул пустой ответ (контекст переполнен, модель галлюцинирует)
- Таймаут на ответ LLM
- MCP тул вернул пустой результат

Что делать:
- Проверить `data: {"type":"tool_call","name":"..."}` — какие тулы дёргает агент
- Проверить что MCP gateway жив и инструменты зарегистрированы:
  ```bash
  curl -s -H "X-Tenant-ID: shop" http://127.0.0.1:8083/mcp/manifest | python3 -m json.tool | head -20
  ```
- Проверить логи: `.data/logs/api.log`, `.data/logs/mcp.log`

#### 3. LLM вернула данные, но они не совпадают с реальными

Симптом: LLM написала "В базе 100 студентов" а на самом деле в таблице 10.

Причина: LLM галлюцинирует или tool call не отработал.

Что делать:
- Проверить что реально лежит в БД:
  ```bash
  sqlite3 data-service/testdata/scenarios/shop/data.db "SELECT COUNT(*) FROM products"
  ```
- Проверить что MCP тул корректен:
  ```bash
  curl -s -H "X-Tenant-ID: shop" http://127.0.0.1:8084/products | python3 -m json.tool | head -10
  ```
- Если тул возвращает правильные данные, но LLM говорит неправильно — проблема в промпте/system prompt'е

#### 4. Ошибка при tool call

Симптом: SSE приходит `tool_call`, потом сразу `error` или агент "зависает".

Причина: MCP gateway упал, data-service ответил 500, или tenant не найден.

Что делать:
- Проверить явно через curl:
  ```bash
  curl -s -H "X-Tenant-ID: shop" http://127.0.0.1:8084/products
  # Если 404 — проверь что tenant shop существует и у него есть endpoint /products
  ```
- Проверить логи: `.data/logs/mcp.log`, `.data/logs/data.log`

#### 5. Таймаут (нет ответа > 30 сек)

Симптом: отправил сообщение, ждёшь, ответа нет.

Причина: LLM медленная (большая модель), или data-service таймаутится, или MCP gateway ждёт.

Что делать:
- Уменьшить модель в `.env`: `LLM_MODEL=qwen2.5:1.5b` вместо `qwen2.5:7b`
- Проверить что нет deadlock'ов в goroutine'ах
- Проверить `ds_request_timeout` в конфиге tenant'а

#### 6. LLM не отвечает на русском

Причина: system prompt на английском, модель по умолчанию отвечает на английском.

Фикс: обновить system prompt для агента в Agent Store.

### Проверка через curl (без браузера)

Если LLM работает, но через UI непонятно:

```bash
# 1. Получить session_id
SESSION_ID=$(python3 -c "import uuid; print(str(uuid.uuid4()))")

# 2. Отправить сообщение и читать SSE поток
curl -s -N -X POST "http://127.0.0.1:8081/api/chat/shop" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: shop" \
  -d "{\"message\":\"Сколько товаров в базе?\",\"session_id\":\"$SESSION_ID\"}" \
  | head -30
```

Если curl возвращает ответ — проблема во фронтенде.
Если curl не возвращает — проблема в бэкенде (смотреть `api.log`).

## 🪟 Тестирование embed-виджета

### Как проверить что виджет загружается

```js
// 1. Загрузить страницу с виджетом
await page.goto('http://127.0.0.1:8080');
await page.waitForTimeout(2000);

// 2. Проверить что Shadow DOM хост создан
const hasWidget = await page.evaluate(() => {
  // Виджет создаёт элемент с классом at-root внутри Shadow DOM
  const allDivs = document.querySelectorAll('div');
  for (const div of allDivs) {
    if (div.shadowRoot) {
      return div.shadowRoot.innerHTML.includes('chat') ||
             div.shadowRoot.innerHTML.includes('at-root');
    }
  }
  return false;
});
console.log('Widget loaded:', hasWidget);
```

### Как кликнуть по триггеру виджета

Триггер — кнопка в правом нижнем углу:

```js
// Через Shadow DOM
await page.evaluate(() => {
  const divs = document.querySelectorAll('div');
  for (const div of divs) {
    if (div.shadowRoot) {
      const trigger = div.shadowRoot.querySelector('button, [class*="trigger"]');
      if (trigger) trigger.click();
    }
  }
});
await page.waitForTimeout(500);

// Написать сообщение
await page.evaluate(() => {
  const divs = document.querySelectorAll('div');
  for (const div of divs) {
    if (div.shadowRoot) {
      const input = div.shadowRoot.querySelector('textarea, input');
      if (input) {
        input.value = 'Покажи все товары';
        input.dispatchEvent(new Event('input', { bubbles: true }));
        // Найти кнопку отправки
        const sendBtn = div.shadowRoot.querySelector('button:last-child, [class*="send"]');
        if (sendBtn) sendBtn.click();
      }
    }
  }
});
```

### Как проверить sessionStorage (состояние сессии)

```js
const sessionState = await page.evaluate(() => {
  const prefix = 'at_messages_';
  const keys = Object.keys(sessionStorage).filter(k => k.startsWith(prefix));
  return keys.map(k => ({
    agent: k.replace(prefix, ''),
    messagesCount: JSON.parse(sessionStorage.getItem(k)).length
  }));
});
console.log('Session state:', sessionState);
```

## 🔄 Тестирование chat-виджета с переключением tenant'ов

Виджет встроен в **Shadow DOM** — кнопка, панель, поле ввода и SSE-стриминг находятся внутри `<script src="/embed/embed.js" data-agent="...">`. Селекторы tenant'ов и агентов — в основном DOM.

### Как устроен виджет (Shadow DOM)

```
document.body
  └── <div id="helperium-widget-{agent}">         ← хост
        └── #shadow-root (open)
              ├── <style>…</style>
              ├── <div class="at-root">
              │     ├── <button class="at-trigger at-right">     ← кнопка-триггер (SVG-иконка)
              │     └── <div class="at-panel at-right at-hidden">  ← панель чата
              │           ├── <div class="at-header">Ассистент<button class="at-close">✕</button></div>
              │           ├── <div class="at-messages">             ← история сообщений
              │           └── <form class="at-form">
              │                 <textarea placeholder="Напишите вопрос…">
              │                 <button type="submit">↗</button>
              │               </form>
```

### Tenant-зависимость

Виджет привязан к **одному агенту** (`data-agent` атрибут). При смене tenant'а через селектор `#tenantSelect` (main DOM):

- Данные во вкладках (Categories, Products…) перезагружаются под новым tenant'ом
- Виджет **НЕ переключается** — он живёт со своим агентом
- Чтобы чат отвечал данными другого tenant'а, нужно **создать отдельного агента** для этого tenant'а и переключить `#agentSelect`

### Полный E2E тест: tenant switching + chat

```js
// ====================================
// Tenant Switching + Chat Widget Test
// ====================================

async function runTenantChatTest(page) {
  // 1. Open demo page
  await page.goto('http://127.0.0.1:8080');
  await page.waitForTimeout(2000);

  // 2. Switch to "shop" tenant via the main-DOM select
  await page.evaluate(() => {
    const select = document.getElementById('tenantSelect');
    if (!select) throw new Error('tenantSelect not found');
    select.value = 'shop';
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await page.waitForTimeout(2000);  // wait for manifest + data reload

  // 3. Verify shop data loaded in the main UI (Categories, Products counts)
  const shopData = await page.evaluate(() => document.body.innerText.substring(0, 500));
  console.log('Shop data after switch:', shopData);

  // 4. Open the chat widget (click trigger button inside Shadow DOM)
  await page.evaluate(() => {
    const host = document.querySelector('[id^="helperium-widget-"]');
    if (!host || !host.shadowRoot) throw new Error('Widget host not found');
    const trigger = host.shadowRoot.querySelector('.at-trigger');
    if (!trigger) throw new Error('Trigger button not found in Shadow DOM');
    trigger.click();
  });
  await page.waitForTimeout(500);

  // 5. Switch agent to "shop" (must exist in Agent Store)
  await page.evaluate(() => {
    const select = document.getElementById('agentSelect');
    if (!select) throw new Error('agentSelect not found');
    // Find the "shop" agent option
    const options = Array.from(select.options);
    const shopOption = options.find(o => o.value === 'shop');
    if (!shopOption) {
      console.log('⚠️ No shop agent found in Agent Store, skipping chat test');
      return;
    }
    select.value = 'shop';
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await page.waitForTimeout(1000);

  // 6. Send a message via the widget (inside Shadow DOM)
  await page.evaluate(() => {
    const host = document.querySelector('[id^="helperium-widget-"]');
    if (!host || !host.shadowRoot) return;
    const textarea = host.shadowRoot.querySelector('.at-form textarea');
    const submitBtn = host.shadowRoot.querySelector('.at-form button[type="submit"]');
    if (textarea && submitBtn) {
      textarea.value = 'Сколько товаров в базе?';
      textarea.dispatchEvent(new Event('input', { bubbles: true }));
      submitBtn.click();
    }
  });

  // 7. Wait for SSE response with tool_call events
  await page.waitForTimeout(20000);  // 20s for LLM to respond

  // 8. Read the assistant's response from Shadow DOM
  const response1 = await page.evaluate(() => {
    const host = document.querySelector('[id^="helperium-widget-"]');
    if (!host || !host.shadowRoot) return null;
    const msgs = host.shadowRoot.querySelectorAll('.at-messages .at-msg');
    for (const msg of msgs) {
      if (msg.classList.contains('at-assistant')) {
        return msg.dataset.raw || msg.textContent?.substring(0, 500);
      }
    }
    return null;
  });
  console.log('Shop agent response:', response1);

  // 9. Switch back to "default" tenant and a direct-chat agent
  await page.evaluate(() => {
    const select = document.getElementById('tenantSelect');
    if (select) {
      select.value = 'default';
      select.dispatchEvent(new Event('change', { bubbles: true }));
    }
  });
  await page.waitForTimeout(2000);

  // 10. Send a different question (now against default tenant's DB)
  await page.evaluate(() => {
    const host = document.querySelector('[id^="helperium-widget-"]');
    if (!host || !host.shadowRoot) return;
    const textarea = host.shadowRoot.querySelector('.at-form textarea');
    const submitBtn = host.shadowRoot.querySelector('.at-form button[type="submit"]');
    if (textarea && submitBtn) {
      textarea.value = 'Покажи всех студентов';
      textarea.dispatchEvent(new Event('input', { bubbles: true }));
      submitBtn.click();
    }
  });

  await page.waitForTimeout(20000);

  const response2 = await page.evaluate(() => {
    const host = document.querySelector('[id^="helperium-widget-"]');
    if (!host || !host.shadowRoot) return null;
    const msgs = host.shadowRoot.querySelectorAll('.at-messages .at-msg');
    for (const msg of msgs) {
      if (msg.classList.contains('at-assistant') && msg.dataset.raw) {
        return msg.dataset.raw.substring(0, 500);
      }
    }
    return null;
  });
  console.log('Default tenant response:', response2);

  // 11. Validate: shop response should mention products/товары,
  //     default response should mention students/студенты
  const shopHasProducts = /товар|product|категори/i.test(response1 || '');
  const defaultHasStudents = /студент|student|ученик/i.test(response2 || '');
  console.log(
    'Validation:',
    'shop→products:', shopHasProducts ? '✅' : '❌',
    'default→students:', defaultHasStudents ? '✅' : '❌'
  );
}
```

### Tenant isolation: проверка на уровне HTTP (curl)

Если LLM не отвечает или виджет неудобен — проверь tenant isolation напрямую через API:

```bash
# 1. Запросить данные tenant-a "shop"
echo "=== SHOP data ==="
curl -s -H "X-Tenant-ID: shop" http://127.0.0.1:8084/products | head -5
curl -s -H "X-Tenant-ID: shop" http://127.0.0.1:8084/categories | head -5

# 2. Запросить данные tenant-a "default"
echo "=== DEFAULT data ==="
curl -s -H "X-Tenant-ID: default" http://127.0.0.1:8084/students | head -5
curl -s -H "X-Tenant-ID: default" http://127.0.0.1:8084/teachers | head -5

# 3. Убедиться что shop НЕ видит students (должно быть 404 или пусто)
echo "=== Cross-tenant check (shop → students) ==="
curl -s -H "X-Tenant-ID: shop" http://127.0.0.1:8084/students | head -3
```

### Watch SSE stream напрямую (не открывая браузер)

```bash
# Отправить сообщение агенту "shop" и читать SSE поток
curl -s -N -X POST "http://127.0.0.1:8081/api/chat/shop" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: shop" \
  -d '{"message":"Сколько товаров?","session_id":"e2e-tenant-test"}' \
  | tee /tmp/sse_shop.log \
  | head -50

# Разобрать SSE события: какие tool_call были, какой final ответ
grep 'tool_call' /tmp/sse_shop.log
grep 'final' /tmp/sse_shop.log
grep 'error' /tmp/sse_shop.log
```

### Чеклист tenant switching

- [ ] После смены `#tenantSelect` — вкладки (Categories, Products…) перезагружаются под новым tenant'ом
- [ ] После смены `#agentSelect` — виджет переключает агента (меняется session key в localStorage)
- [ ] Агент для "shop" возвращает данные про товары (products, categories)
- [ ] Агент для "default" возвращает данные про университет (students, teachers)
- [ ] Cross-tenant запрос (shop → /students) возвращает 404 или пустоту
- [ ] SSE поток доходит до `done` (нет `error` событий)
- [ ] sessionStorage сохраняет историю:
  ```js
  // Ключи: at_messages_{agent}, at_session_{agent}
  Object.keys(sessionStorage).filter(k => k.startsWith('at_'))
  ```

### Известные проблемы с tenant switching

1. **Агент не привязан к tenant'у** — если у агента в конфиге нет `tenant_ids` для "shop", то данные будут из дефолтной БД
2. **Виджет не переключается за tenant'ом** — виджет привязан к `data-agent`, при смене `#tenantSelect` виджет продолжает работать со своим агентом
3. **SSE не приходит** — частая причина: Ollama не запущена (смотри `./scripts/dev.sh ollama`)
4. **Shadow DOM не виден Playwright'у** — всегда используй `host.shadowRoot.querySelector(...)`, не `page.locator(...)`
5. **textarea.value не ловится виджетом** — после `textarea.value = '...'` обязательно `dispatchEvent(new Event('input', {bubbles: true}))`

## 🧪 Сквозная E2E проверка чата (быстрый чеклист)

Перед сложным тестированием — быстрая проверка что всё alive:

```bash
# 1. Все сервисы живы
./scripts/dev.sh status

# 2. Data-service отвечает
curl -s -H "X-Tenant-ID: shop" http://127.0.0.1:8084/health

# 3. MCP gateway жив
curl -s http://127.0.0.1:8083/health

# 4. API-service жив
curl -s http://127.0.0.1:8081/health

# 5. LLM работает
curl -s http://localhost:11434/api/tags | python3 -c "import sys,json; d=json.load(sys.stdin); [print(m['name']) for m in d.get('models',[])]"

# 6. Агенты в Agent Store
curl -s http://127.0.0.1:8081/api/agents | python3 -m json.tool | head -20

# 7. Embed виджет доступен (отдаётся api-service)
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8081/embed/embed.js
```

Если хоть один шаг упал — E2E чат не заработает.

## 📋 Полный E2E сценарий (через API, без UI)

```bash
# 1. Зайти на демо
open http://127.0.0.1:8080

# 2. Выбрать tenant shop в селекторе "База"
# 3. Выбрать агента (создать если нет)

# 4. Написать вопрос в чат
curl -s -N -X POST "http://127.0.0.1:8081/api/chat/shop" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: shop" \
  -d '{"message":"покажи все товары","session_id":"e2e-1"}' \
  > /tmp/chat_response.txt 2>&1 &

# 5. Ждём и смотрим что ответило
sleep 30
cat /tmp/chat_response.txt
# Ожидается SSE поток с token → tool_call → token → final → done
```

### 4. Проверка persistence после restart

1️⃣ Сохранить состояние до restart:

```bash
curl -s -H "Authorization: Bearer secret" http://127.0.0.1:8084/admin/tenants
```

2️⃣ Перезапустить все сервисы:

```bash
./scripts/dev.sh restart
```

3️⃣ Проверить что тенанты на месте:

```bash
curl -s -H "Authorization: Bearer secret" http://127.0.0.1:8084/admin/tenants
```

4️⃣ Открыть демо в браузере — данные должны загружаться (те же сущности, те же counts).

### 5. Создание tenant через API (если UI upload не работает)

Когда не удаётся загрузить SQLite через UI — используй API напрямую:

```bash
curl -s -X POST http://127.0.0.1:8084/admin/tenants \
  -H "Authorization: Bearer secret" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "shop",
    "config": {
      "version": 1,
      "data_source": {
        "driver": "sqlite",
        "dsn": "'"$(pwd)"'/data-service/testdata/scenarios/shop/data.db",
        "read_only": true
      },
      "entities": [...],
      "endpoints": [...]
    }
  }'
```

Можно использовать готовый конфиг как шаблон:

```bash
python3 -c "
import json
cfg = json.load(open('specs/config.example.json'))
cfg['data_source']['dsn'] = '/absolute/path/to/shop/data.db'
with open('/tmp/shop_cfg.json', 'w') as f:
    json.dump(cfg, f, indent=2)
"
```

## 🛠️ Write-tool approval (per-tenant)

Эндпоинты:

```
GET  /admin/tenants/{id}/tools/pending            — посмотреть pending write-tools
POST /admin/tenants/{id}/tools/{toolName}/approve  — аппрувнуть write-tool
```

Через API проверить можно так:

```bash
# Получить список pending tools для tenant shop
curl -s -H "Authorization: Bearer secret" \
  http://127.0.0.1:8084/admin/tenants/shop/tools/pending

# Аппрувнуть тул (имя из deriveToolName)
curl -s -X POST -H "Authorization: Bearer secret" \
  http://127.0.0.1:8084/admin/tenants/shop/tools/list_products/approve
```

Approval'ы хранятся внутри tenant config'а в `.data/tenants/{id}.json` (поле `approved_tools`).
После restart — восстанавливаются.

## 🚨 Известные проблемы с чатом

### 1. LLM не настроен (ollama не запущена)

Симптом: на странице написано `"API: Ollama недоступна"`. Чат не отвечает.

Причина: LiteLLM не может достучаться до Ollama.

Фикс:
```bash
ollama serve           # запустить сервер
ollama pull qwen2.5    # скачать модель
curl http://localhost:11434/api/generate -d '{"model":"qwen2.5","prompt":"hi"}'
```

### 2. SSE поток не приходит (виджет висит на "печатает...")

Симптом: отправил сообщение, видно что `POST /api/chat` прошёл (200), но токены не приходят.

Причина: api-service не может запустить stream — нет LLM, нет agent'а, или orchestrator упал.

Фикс:
- Проверить `.data/logs/api.log`: `grep -i error .data/logs/api.log | tail -10`
- Проверить что агент существует: `curl -s http://127.0.0.1:8081/api/agents | python3 -m json.tool`
- Проверить что tenant зарегистрирован: `curl -s -H "Authorization: Bearer secret" http://127.0.0.1:8084/admin/tenants`

### 3. Агента нет в списке

Симптом: селектор "🤖 Агент" пустой или не содержит нужного агента.

Причина: Agent Store пуст.

Фикс: создать агента через API:
```bash
curl -s -X POST http://127.0.0.1:8081/api/agents \
  -H "Content-Type: application/json" \
  -d '{"name":"shop","tenant_ids":["shop"],"model":"qwen2.5:1.5b","provider":"ollama","system_prompt":"Ты помощник магазина. Отвечай на русском."}'
```

### 4. Session потерялась (виджет не помнит историю)

Симптом: после перезагрузки страницы история чата пуста.

Причина: sessionStorage привязан к вкладке. На сервере история в SQLite — живёт.

Фикс: это ожидаемое поведение. sessionStorage чистится при закрытии вкладки.

### 5. Неправильный tenant_id в чате

Симптом: агент отвечает данными не из той БД.

Причина: виджет не передаёт `X-Tenant-ID` или передаёт не тот.

Фикс: проверить заголовки в network-запросе. tenant_id должен совпадать с тем что в конфиге агента.

### 6. Shadow DOM не создался

Симптом: виджет не появился на странице, в консоли ошибка.

Причина: embed.js не загрузился или `attachShadow` не поддерживается.

Фикс: проверить что `/embed/embed.js` отвечает 200.

### 7. Виджет есть, но не отвечает на клик

Симптом: кнопка-триггер видна, но клик не открывает панель.

Причина: CSS `pointer-events: none` на контейнере или Shadow DOM не принимает события.

Фикс: проверить z-index и position контейнера. Возможно, поверх виджета другой элемент.

## 📁 Data directories — per-service справочник

Каждый сервис использует свои env-переменные для путей к данным. При native-запуске (`./scripts/dev.sh`) все пути относительны `PROJECT_ROOT` (= корень репозитория).

**Полный список всех env-переменных проекта (180+):** [`.env.example`](.env.example)

**Какие бывают сценарии использования:** см. [👤 Сценарии использования](#-сценарии-использования-кто-и-зачем-тестирует)

### ⚙️ data-service (Go, :8084)

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `TENANTS_DIR` | `.data/tenants/` | Tenant configs (`{id}.json`)
| `DB_PATH` | `university.db` | SQLite для `default` tenant (если не multi-tenant)
| `DATABASE_URL` | — | PostgreSQL DSN (если не SQLite)
| `CONFIG_SCHEMA` | `specs/config.schema.json` | JSON Schema для валидации конфигов

Путь по умолчанию для `TENANTS_DIR` вычисляется как `{dir_of_config_file}/../.data/tenants/`, где конфиг — из `CONFIG_PATH` или первый найденный `config.{json,yaml,yml}`.

**Логи:** `.data/logs/data.log`

### ⚙️ mcp-gateway (Go, :8083)

Пути к данным не хранит (stateless прокси). Читает конфиги tenant'ов на старте через data-service `/admin/tenants`.

### ⚙️ admin-dashboard (Go, :8085)

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `DATA_DIR` | `.data/uploads` | Куда сохранять загруженные SQLite

**Логи:** `.data/logs/admin.log`

### ⚙️ api-service (Python, :8081)

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `DEMO_SESSION_DB_PATH` | `demo_sessions.sqlite` (в корне) | SQLite с сессиями чатов |
| `BACKLOG_DIR` | `backlog/` (в корне) | Полный trace LLM-взаимодействий (".turn" файлы) |
| `AGENT_DB_PATH` | `agents.sqlite` (рядом с session db) | Agent Store |
| `EMBED_DIR` | `api-service/embed/` | Статика embed-виджета |
| `ENCRYPTION_KEY` | — | Ключ для Fernet-шифрования секретов в Agent Store |
| `CORS_ALLOW_ORIGINS` | `*` | CORS origins (через запятую)
| `CHAT_RATE_LIMIT` | `30/minute` | Rate limit на chat endpoint

### ⚙️ rag (Python, :8082)

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `CHROMA_PATH` | `chroma_db/` (в корне проекта) | ChromaDB persist directory |
| `RAG_DB_PATH` | `rag_documents.db` (в корне rag/) | SQLite с метаданными документов |
| `RAG_EMBEDDING_MODEL` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | Модель эмбеддингов |
| `RAG_DEVICE` | `cpu` | Устройство для эмбеддингов (`cpu`/`mps`/`cuda`)

### ⚙️ demo/web (Python, :8080)

Наследует те же переменные что api-service (использует `demo/settings.py`, который re-экспортит часть api-переменных).

### 🐳 Docker: маппинг томов

| Volume | Хост (bind) | Монтируется в |
|---|---|---|
| `app_data` | `./.data/app` | `/data/app` — DB, сессии, бэклог |
| `rag_data` | `./.data/rag` | `/data/rag` — ChromaDB, документы |
| `uploads_data` | `./.data/uploads` | `/data/uploads` — uploaded SQLite |
| `hf_cache` | `./.data/hf_cache` | `/home/app/.cache/huggingface` — кэш моделей |
| `caddy_data` | `./.data/caddy` | `/data` — TLS-сертификаты |
| `pg_data` | `./.data/pg` | `/var/lib/postgresql/data` — PostgreSQL |

### ✅ Чеклист: что должно существовать для E2E теста

- `.data/tenants/` — tenant configs (создаётся автоматически при добавлении tenant'а)
- `.data/uploads/` — uploaded SQLite (создаётся `dev.sh`, иначе mkdir вручную)
- `demo_sessions.sqlite` — создаётся при первом chat запросе
- `agents.sqlite` — создаётся при первом запросе к Agent Store
- `chroma_db/` — создаётся при первой индексации документа
- `backlog/` — создаётся при первом LLM-запросе

### 🚩 Быстрая проверка всех путей

```bash
# Все пути существуют?
ls -la .data/tenants/ 2>/dev/null && echo "✅ tenants" || echo "❌ tenants"
ls -la .data/uploads/ 2>/dev/null && echo "✅ uploads" || echo "❌ uploads"
ls -la demo_sessions.sqlite 2>/dev/null && echo "✅ sessions" || echo "❌ sessions"
ls -la agents.sqlite 2>/dev/null && echo "✅ agents" || echo "❌ agents"
ls -d chroma_db/ 2>/dev/null && echo "✅ chroma" || echo "❌ chroma"
ls -la backlog/ 2>/dev/null && echo "✅ backlog" || echo "❌ backlog"

# Все нужные env vars установлены?
echo "TENANTS_DIR=$TENANTS_DIR"
echo "DB_PATH=$DB_PATH"
echo "DATA_DIR=$DATA_DIR"
echo "DEMO_SESSION_DB_PATH=$DEMO_SESSION_DB_PATH"
echo "BACKLOG_DIR=$BACKLOG_DIR"
echo "CHROMA_PATH=$CHROMA_PATH"
echo "RAG_DB_PATH=$RAG_DB_PATH"

# Free disk space (полезно перед тестом с upload'ами)
df -h .data/
```

## 🚨 Известные проблемы

### Alpine.js сбрасывает состояние при навигации

Симптом: после клика по nav ссылке показывается login форма а не содержимое.
Причина: Alpine.js хранит `tokenSet` в localStorage. Если страница перезагрузилась — нужно войти заново.
Фикс: очистить localStorage и перелогиниться (см. шаг 1).

### File input скрыт (display: none)

Симптом: Playwright не может найти file input для upload'а.
Причина: Alpine.js использует `style="display: none"` на input.
Фикс: использовать прямой `setInputFiles` на скрытый input через `page.locator('input[type="file"][accept*=".db"]').setInputFiles(path)`.

### Upload зона не работает в Playwright

Симптом: клик по upload зоне не вызывает filechooser.
Причина: Alpine.js использует `@click="$refs.tenantFileInput.click()"`, но в контейнере Playwright может не найти `$refs`.
Фикс: использовать API напрямую с curl (см. шаг 5).

### Console warnings (не фатальные)

Alpine.js пишет в консоль warnings типа:
- `"manifest is null"` — ожидаемо при первой загрузке
- `"newTenantUploadFile is null"` — ожидаемо
Ошибки 4xx/5xx в network — уже баг.

## 🔍 Диагностика

```js
// Получить весь текст страницы
document.body.innerText.substring(0, 2000)

// Получить Alpine.js state
document.querySelector('[x-data]')?.__x?.$data

// Список nav ссылок
Array.from(document.querySelectorAll('nav a')).map(a => a.textContent.trim())

// Список всех input'ов с их типами
Array.from(document.querySelectorAll('input')).map(i => ({
  type: i.type,
  placeholder: i.placeholder,
  style: i.style.display
}))
```

## 🧪 CI эквивалент

Если нужно проверить всё в CI (без браузера):

```bash
make ci                                     # линт + тесты
uv run agent-db e2e-data                    # data-level isolation
uv run agent-db e2e-mcp                     # MCP tools test
uv run agent-db e2e-full                    # полный pipeline
cd data-service && go test ./internal/server/... -v -run 'TestApproved|TestTenantPersist'  # persistence тесты
```

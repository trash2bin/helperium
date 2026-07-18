---
name: browser-e2e-test
description: "E2E тестирование Helperium через живой браузер (Playwright): admin dashboard, tenant persistence, write-tool approval, demo web UI."
disable-model-invocation: true
---

# Browser E2E Testing — Helperium

Тестирование Helperium через браузер с помощью Playwright.

**Когда использовать:**
- Нужно проверить, что тенанты пережили restart (persistence)
- Нужно добавить tenant через admin dashboard и убедиться, что он работает в demo UI
- Нужно проверить write-tool approval flow
- Нужно убедиться, что upload SQLite через UI работает
- Нужно найти JS/network ошибки на UI

**Инструменты:** `playwright_browser_*` — navigate, snapshot, console, network, click, type, find, evaluate.

## 🏗️ Admin Dashboard Architecture

Admin dashboard — SPA на Alpine.js 3.x, собранная из TypeScript (src/) в единый bundle через esbuild.

### Структура

```
admin-dashboard/
├── src/                          # Исходный код (TypeScript)
│   ├── core/                     # Ядро: registry, apiClient, store, eventBus, notify, apiLogger
│   │   ├── registry.ts           # AppRegistry — регистрация domain-модулей
│   │   ├── apiClient.ts          # HTTP клиент (Alpine.store('api'))
│   │   ├── store.ts              # Глобальное состояние (Alpine.store('ui'))
│   │   ├── eventBus.ts           # Pub/sub между модулями
│   │   ├── notify.ts             # Toast-уведомления
│   │   └── apiLogger.ts          # Логирование API-вызовов + debug-панель
│   ├── domains/                  # 11 domain-модулей: auth, tenants, config, tools, rag, agents, abuse, emergency, llm, voice, audit
│   ├── types.ts                  # 34 экспортированных интерфейсов
│   ├── i18n.ts                   # Синхронный загрузчик переводов (XHR → /i18n.json)
│   └── index.ts                  # Точка входа, dashboard() Alpine component factory
├── partials/                     # 16 HTML partials (head, login, app-open, 10 pages, app-close, modals, tail)
├── tests/                        # Unit-тесты (vitest): api.test.js, contract.test.js, core/registry.test.ts, types.test.ts, i18n.test.ts
├── internal/server/static/       # Скомпилированный вывод
│   ├── dist/app.js               # esbuild bundle (minified, sourcemap)
│   ├── index.html                # Собран из partials (build.sh: cat partials/* > index.html)
│   ├── i18n.json                 # 521+ переводов (ru/en)
│   ├── styles.css                # CSS
│   └── admin.css                 # Дополнительные стили
├── build.sh                      # typecheck → html-validate → esbuild → dist/app.js
└── .htmlvalidate.json            # Linter для HTML partials (правила отключены для Alpine.js атрибутов)
```

### Pipeline сборки

```
build.sh:
1. tsc --noEmit              (typecheck TypeScript)
2. cat partials/* > index.html  (сборка HTML)
3. html-validate index.html     (линт HTML)
4. esbuild src/index.ts --bundle --minify → dist/app.js
```

### Data flow в админке

```
Alpine.js x-data="dashboard()"
  │
  │ AppRegistry.getState() + AppRegistry.getMethods()
  │ → состояние и методы из всех 11 domain-модулей
  │
  │ API-вызовы через Alpine.store('api').get|post|put|del(url)
  │ → apiClient.ts → fetch → Go-прокси (admin-dashboard:8085)
  │   → data-service, api-service, rag-service
  │
  │ Переводы через __('key') (window.__ из i18n.ts)
  │ → синхронный XHR /i18n.json → $magic('__')
```

### Auth flow

```
login.html (partial)
  │
  │ <div x-data="dashboard()">
  │   <input type="password" x-model="tokenInput">
  │   <button @click="login()">Войти</button>
  │
  │ dashboard().login() → Alpine.store('ui').login()
  │   → localStorage.setItem('admin_token', token)
  │   → tokenSet = true
  │   → init() → AppRegistry.expectAll(EXPECTED_DOMAINS)
  │
checkAuth() в auth.ts → localStorage.getItem('admin_token') → Bearer
```

## 🚀 Pre-flight

Перед тестом сервисы должны быть запущены:

```bash
cd /Users/ivan/code/helperium
./scripts/dev.sh start   # или docker compose up -d
```

Убедись что `/admin/tenants` отвечает:

```bash
curl -s -H "Authorization: Bearer secret" http://127.0.0.1:8084/admin/tenants
```

## 🏪 Типовой тест

### 1. Login в admin dashboard

```js
// 1. Navigate
await playwright_browser_navigate({ url: 'http://127.0.0.1:8085' });

// 2. Alpine.js может помнить старый tokenSet через localStorage
//    При проблемах — очистить:
await playwright_browser_evaluate({ script: "localStorage.clear()" });
await playwright_browser_navigate({ url: 'http://127.0.0.1:8085' });
await playwright_browser_snapshot({});

// 3. Заполнить поле пароля напрямую (Alpine x-model ловит input event)
await playwright_browser_evaluate({
  script: `(() => {
    const input = document.querySelector('input[type="password"]');
    if (input) {
      input.value = 'secret';
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  })()`
});

// 4. Кликнуть "Войти"
await playwright_browser_click({ selector: 'button:has-text("Войти")' });
await playwright_browser_snapshot({});
```

**Важно:** Alpine.js использует `x-model`, который слушает `input` event. Заполнение через `fill()` не всегда работает, потому что элемент может быть невидим. `dispatchEvent(new Event('input'))` гарантирует что Alpine увидит изменение.

### 2. Проверка списка тенантов

```js
// Navigate to Тенанты (sidebar nav link)
const navLinks = await playwright_browser_find({ selector: 'aside nav a' });
await navLinks[0].click();  // или по тексту
await playwright_browser_snapshot({});

// Получить текст через evaluate
const text = await playwright_browser_evaluate({
  script: "document.body.innerText.substring(0, 1500)"
});
console.log('TENANTS:', text);
// Ожидается: default active, shop active (если добавлен)
```

Sidebar ссылки используют Alpine.js для перевода:
```html
<a href="#" @click="page = 'tenants'" x-text="__('nav.tenants')"></a>
```

Для поиска навигационной ссылки используй:
```js
await playwright_browser_click({ selector: 'aside nav a:first-child' });
```

### 3. Проверка demo web UI (данные из БД)

```js
// Open demo
await playwright_browser_navigate({ url: 'http://127.0.0.1:8080' });
await playwright_browser_snapshot({});

// Read text
const text = await playwright_browser_evaluate({
  script: "document.body.innerText.substring(0, 1000)"
});
// Ожидается: список сущностей с count (Categories 3, Products 4, ...)
```

### 4. Архитектурная валидация страниц админки

Quick check что все страницы рендерятся внутри `.app`:

```js
const pagesInApp = await playwright_browser_evaluate({
  script: `(() => {
    const app = document.querySelector('.app');
    if (!app) return { ok: false, reason: 'no .app' };
    const pageEls = [...document.querySelectorAll('[x-show*=\"page === \"]')];
    const pages = pageEls.map(el => {
      const match = el.getAttribute('x-show').match(/page === '(\w+)'/);
      return {
        page: match ? match[1] : null,
        inApp: app.contains(el)
      };
    });
    return { ok: pages.every(p => p.inApp), pages };
  })()`
});
console.log('Architecture check:', pagesInApp);
```

Ожидается: все страницы (`dashboard`, `tenants`, `config`, `tools`, `rag`, `agents`, `abuse`, `voice`, `llm`, `audit`) находятся внутри `.app`.

## 💬 Тестирование чата с агентом через demo web

### Архитектура чата

```
Браузер (http://127.0.0.1:8080)
  │
  ├── демо UI              ← отображает данные + виджет чата
  │     │
  │     └── POST /api/chat     ← SSE прокси через web:8080
  │           │
  │           └── POST /api/chat  → api-service:8081 → orchestrator → LLM (LiteLLM)
  │                                                         │
  │                                                         └── MCP tools → mcp-gateway → data-service → SQL
  │
  └── SSE поток: token → tool_call → token → final → done
```

Чат-виджет встроен в demo-страницу (embed.js, Shadow DOM).

### Как открыть чат в браузере

Виджет находится в **Shadow DOM**. Обычные `playwright_browser_click()` туда не достают — используй `playwright_browser_evaluate()`:

```js
// 1. Открыть демо
await playwright_browser_navigate({ url: 'http://127.0.0.1:8080' });

// 2. Клик по триггеру чата (через Shadow DOM)
await playwright_browser_evaluate({
  script: `(() => {
    const host = document.querySelector('[id^="helperium-widget-"]');
    if (!host || !host.shadowRoot) throw new Error('Widget host not found');
    const trigger = host.shadowRoot.querySelector('[class*="trigger"]');
    if (!trigger) throw new Error('Trigger not found');
    trigger.click();
  })()`
});

// 3. Выбрать агента (через main DOM — selector вне Shadow DOM)
await playwright_browser_evaluate({
  script: `(() => {
    const select = document.getElementById('agentSelect');
    if (select) {
      select.value = 'shop';
      select.dispatchEvent(new Event('change', { bubbles: true }));
    }
  })()`
});

// 4. Написать сообщение через Shadow DOM
await playwright_browser_evaluate({
  script: `(() => {
    const host = document.querySelector('[id^="helperium-widget-"]');
    if (!host || !host.shadowRoot) return;
    const textarea = host.shadowRoot.querySelector('textarea');
    if (!textarea) return;
    textarea.value = 'Покажи всех студентов';
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
  })()`
});

// 5. Отправить
await playwright_browser_evaluate({
  script: `(() => {
    const host = document.querySelector('[id^="helperium-widget-"]');
    if (!host || !host.shadowRoot) return;
    const submitBtn = host.shadowRoot.querySelector('button[type="submit"]');
    if (submitBtn) submitBtn.click();
  })()`
});

// 6. Ждать ответ (SSE стриминг — может быть долго если LLM думает)
await playwright_browser_snapshot({});
```

**Важно:** После установки `textarea.value` нужно обязательно `dispatchEvent(new Event('input', {bubbles: true}))` — иначе Shadow DOM не увидит изменение.

### SSE протокол (что приходит от LLM)

```
data: {"type":"token","text":"частичный "}
data: {"type":"token","text":"текст "}
data: {"type":"tool_call","name":"find_students"}   ← агент вызвал MCP тул
data: {"type":"token","text":"вот "}
data: {"type":"token","text":"результаты"}
data: {"type":"final","text":"полный ответ"}        ← финальный текст
data: {"type":"done"}                                 ← стрим завершён
```

### Как смотреть SSE и сетевые запросы в Playwright

```js
// Захватить console.log из браузера
const msgs = await playwright_browser_console_messages({ level: "info" });
console.log('Console messages:', msgs);

// Ждать сетевой ответ
const pageContent = await playwright_browser_snapshot({});
```

## ✅ Валидация ответа LLM

1. **SSE поток дошёл до `done`** — если последнее событие `done`, значит агент завершил ответ
2. **Нет `error` событий** — если был `error`, значит что-то сломалось
3. **В ответе есть данные из БД** — LLM должен вернуть что-то что связано с запросом
4. **Tool call был успешным** — если агент вызвал тул, проверь что в ответе есть результат

### Диагностика

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

# Аппрувнуть тул
curl -s -X POST -H "Authorization: Bearer secret" \
  http://127.0.0.1:8084/admin/tenants/shop/tools/list_products/approve
```

Approval'ы хранятся внутри tenant config'а в `.data/tenants/{id}.json` (поле `approved_tools`).

## 🔍 Диагностика

```js
// Получить весь текст страницы
await playwright_browser_evaluate({ script: "document.body.innerText.substring(0, 2000)" });

// Получить Alpine.js state
await playwright_browser_evaluate({
  script: "document.querySelector('[x-data]')?.__x?.$data"
});

// Список nav ссылок
await playwright_browser_evaluate({
  script: "JSON.stringify(Array.from(document.querySelectorAll('aside nav a')).map(a => a.textContent.trim()))"
});

// Список всех input'ов с их типами
await playwright_browser_evaluate({
  script: "JSON.stringify(Array.from(document.querySelectorAll('input')).map(i => ({ type: i.type, placeholder: i.placeholder, style: i.style.display })))"
});
```

## 🚨 Известные проблемы

### Alpine.js сбрасывает состояние при навигации

Симптом: после клика по nav ссылке показывается login форма а не содержимое.
Фикс: очистить localStorage и перелогиниться.

```js
await playwright_browser_evaluate({ script: "localStorage.clear()" });
await playwright_browser_navigate({ url: 'http://127.0.0.1:8085' });
```

### File input скрыт (display: none)

Симптом: Playwright не может найти file input для upload'а.
Фикс: использовать прямой `setInputFiles` на скрытый input:

```js
// Через evaluate установить файл напрямую
await playwright_browser_evaluate({
  script: `(() => {
    const input = document.querySelector('input[type="file"][accept*=".db"]');
    if (input) {
      // set file via DataTransfer
      const dt = new DataTransfer();
      // ... in practice use playwright_browser_upload_file or send via curl
    }
  })()`
});
```

### Console warnings (не фатальные)

Alpine.js пишет в консоль warnings типа:
- `"__ is not defined"` — если `i18n.ts` не загрузился первым
- `"manifest is null"` — ожидаемо при первой загрузке
- `"newTenantUploadFile is null"` — ожидаемо
Ошибки 4xx/5xx в network — уже баг.

## 🧪 CI эквивалент

Если нужно проверить всё в CI (без браузера):

```bash
make ci                                     # линт + тесты
uv run agent-db e2e-data                    # data-level isolation
uv run agent-db e2e-mcp                     # MCP tools test
cd tests && npx vitest run                  # vitest тесты админки
cd data-service && go test ./internal/server/... -v -run 'TestApproved|TestTenantPersist'  # persistence тесты
```

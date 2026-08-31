# Как работать с этим проектом через pi

## 🕸️ Граф знаний (codebase-memory)

Что это: индексированный граф кода (~9k узлов: функции, классы, роуты, HTTP-каналы между сервисами). **Документации и .env в нём НЕТ** — только код.

**Перед поиском по графу после правок в коде — переиндексировать в full:**
```javascript
codebase_memory_index_repository({ repo_path: ".", mode: "full" })
```

Default project: `helperium` (после переиндексации имя может смениться — проверить `codebase_memory_list_projects()`).

| Задача | Команда |
|---|---|
| Поиск символа | `codebase_memory_search_graph({ query: "MCPClient", project: "helperium" })` |
| Трассировка вызовов | `codebase_memory_trace_path({ function_name: "qualified.name", project: "helperium", direction: "both" })` |
| Что сломает изменение? | `codebase_memory_trace_path({ function_name: "qualified.Name", project: "helperium", direction: "inbound" })` |
| Архитектура | `codebase_memory_get_architecture({ project: "helperium", aspects: ["overview"] })` |
| Изменения с прошлого раза | `codebase_memory_detect_changes({ project: "helperium", scope: "." })` |
| Поиск по regex | `codebase_memory_search_code({ pattern: "tenant", project: "helperium", file_pattern: "*.go" })` |
| Cypher-запросы | `codebase_memory_query_graph({ query: "MATCH ...", project: "helperium" })` |
| Чтение кода | `codebase_memory_get_code_snippet({ qualified_name: "...", project: "helperium" })` |

**Ограничения:** не видит .env, shell-скрипты, динамические HTTP-вызовы (SSE, runtime URL). Не грепать классы руками — использовать граф.

---

## 🚀 Codemode (`codemode`)

Исполняемая среда TypeScript (`@boozedog/pi-codemode`) для пакетной обработки данных и вызова инструментов.

* **Зачем использовать:** Когда нужно прочитать несколько файлов одновременно (`Promise.all`), вызывать MCP-серверы пачкой (`mcp.<server>.<tool>()`), отфильтровать или сагрегировать большие объемы данных прямо в скрипте без лишних циклов в контексте LLM.
* **Доступные функции внутри:** `read({ path, offset, limit })`, `mcp.<namespace>.<tool>()`, `codemode.search_tools()`, `codemode.describe_tools()`, `print()`, `sendMessage()`.
* **Правила:**
  * Код автоматически проверяется компилятором TypeScript перед запуском.
  * Мутации файлов закрыты внутри guest-кода — для редактирования используй патчи (`apply_patch`, `replace_in_file`).
  * Для сложных или объемных строковых данных передавай их через объект `strings` (доступны как `π.keyName`), чтобы избежать проблем с кавычками.

---

## 🛠️ Инструменты и Скиллы

- **pi-subagents** — Делегирование задач. Используй когда: >5 файлов / >10 тулов / нужен review / параллельные задачи. Не делегируй: 1 файл / быстрый lookup.
- **pi-intercom** — Взаимодействие и координация между активными сессиями Pi (`intercom`).
- **ask_user_question** — Задать пользователю от 1 до 4 структурированных вопросов с вариативными опциями, когда требования неоднозначны.
- **pi-web-access** — Веб-поиск и фетчинг страниц (`web_search`, `fetch_content`, `source_check`, `get_search_content`).
- **git-commit** — **Коммиты и push только по явной просьбе пользователя**. Агент только читает git (diff/status/log). Никогда не пушить, не аммендить, не выполнять `reset --hard` самостоятельно.

---

## ⚠️ Правила работы

- **Не грепать/глоббить классы руками** — искать структуры и связи через `codebase-memory` или `codemode`.
- **Для больших файлов (>2000 строк)** — использовать `read` с `offset` и `limit`.
- **Для сложных цепочек вызовов и пакетной обработки** — использовать `codemode`.
- **После существенных правок кода** — переиндексировать граф знаний в режиме `full` (`codebase_memory_index_repository`).

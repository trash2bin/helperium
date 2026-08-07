# Как работать с этим проектом через pi

## 🕸️ Граф знаний (codebase-memory)

Что это: индексированный граф кода (~9k узлов: функции, классы, роуты, HTTP-каналы между сервисами). **Доки и .env в нём НЕТ** — только код.

**Перед поиском по графу после правок в коде — переиндексировать в full:**
```
codebase_memory_index_repository({ repo_path: ".", mode: "full" })
```

Default project: `helperium` (после переиндексации имя может смениться — проверить `codebase_memory_list_projects()`).

| Задача | Команда |
|---|---|
| Поиск символа | `codebase_memory_search_graph({ query: "MCPClient" })` |
| Трассировка вызовов | `codebase_memory_trace_path({ function_name: "qualified.name", direction: "both" })` |
| Что сломает изменение? | `codebase_memory_trace_path({ function_name: "qualified.Name", direction: "inbound" })` |
| Архитектура | `codebase_memory_get_architecture({ aspects: ["overview"] })` |
| Изменения с прошлого раза | `codebase_memory_detect_changes({ scope: "." })` |
| Поиск по regex | `codebase_memory_search_code({ pattern: "tenant", file_pattern: "*.go" })` |
| Cypher-запросы | `codebase_memory_query_graph({ query: "MATCH ..." })` |
| Чтение кода | `codebase_memory_get_code_snippet({ qualified_name: "..." })` |

**Ограничения:** не видит .env, shell-скрипты, динамические HTTP-вызовы (SSE, runtime URL). Не грепать классы руками — граф. Большие выводы (>1KB) — ctx_execute/ctx_batch_execute, не read/bash.

---

## Скиллы

- **context-mode (ctx_\*)** — любой вывод >1KB: `ctx_execute`, `ctx_execute_file`, `ctx_batch_execute`, `ctx_search`, `ctx_index`. Вместо raw Bash/read.
- **pi-subagents** — делегировать когда: >5 файлов / >10 тулов / нужен review / контекст >50% / параллельные задачи. Не делегировать: 1 файл / быстрый lookup / <5 тулов / shared context. Шаблоны: `/gather-context-and-clarify`, `/parallel-research`, `/parallel-handoff-plan`, `/parallel-review`, `/review-loop`. browser-debugger — Firefox, ARIA, console/network, **не правит код**. SSE-сессии — fresh context (не fork). СКИЛЛ ОБЯЗАТЕЛЕН К ПРОЧТЕНИЮ ПЕРЕД ДЕЛЕГИРОВАНИЕМ ЗАДАЧИ, внутри описаны способы запуска агентов и их типы + назначения.
- **git-commit** — **коммиты и push только по явной воле пользователя**. Агент: только read-only git (diff/status/log). Никогда не пушить, не аммендить, не reset --hard.
- **pi-intercom** — коммуникация между pi-сессиями.

---

## ⚠️ Правила

- **Не грепать/глоббить классы** — через codebase-memory.
- **Не использовать raw Bash для >1KB** — ctx_execute / ctx_batch_execute. Bash только для небольших команд в идеале для всего что требует время запуска (больше нескольких минут например запуск проекта или тестирования) только через bash все что быстро выполниться через contex_mode.
- **После правок кода — переиндексировать граф в full** (см. выше).

# Внешние пакеты (Pi Packages)
Необходимо для работы с [.pi/APPEND_SYSTEM.md](./APPEND_SYSTEM.md)

### @boozedog/pi-codemode
`pi install npm:@boozedog/pi-codemode`
Исполнение TypeScript-кода для параллельного вызова инструментов, фильтрации и пакетных операций (`codemode`).

### pi-subagents
`pi install npm:pi-subagents`
Делегирование задач субагентам, параллельные воркфлоу, рецензирование и изоляция контекста (`subagent`, `subagent_wait`, `subagent_supervisor`).

### pi-intercom
`pi install npm:pi-intercom`
Координация и обмен сообщениями между активными сессиями Pi (`intercom`).

### pi-mcp-adapter
`pi install npm:pi-mcp-adapter`
Адаптер MCP-протокола для интеграции внешних MCP-серверов (codebase-memory, playwright, anytype и др.).

### pi-web-access
`pi install npm:pi-web-access`
Инструменты поиска в сети и фетчинга страниц (`web_search`, `fetch_content`, `source_check`, `get_search_content`).

### @juicesharp/rpiv-ask-user-question
`pi install npm:@juicesharp/rpiv-ask-user-question`
Интерактивные вопросы пользователю с готовыми вариантами ответа (`ask_user_question`).

### pi-prompt-template-model
`pi install npm:pi-prompt-template-model`
Создание и запуск пользовательских шаблонов промптов и slash-команд.

---

### codebase-memory (MCP сервер)
Предустановленный граф знаний по коду проекта.

#### Использование
```javascript
codebase_memory_search_graph({ query: "...", project: "helperium" })
codebase_memory_trace_path({ function_name: "...", project: "helperium", direction: "both", mode: "calls", depth: 3 })
codebase_memory_get_architecture({ project: "helperium", aspects: ["all"] })
```

#### Переиндексировать
```javascript
codebase_memory_index_repository({ repo_path: ".", name: "helperium", mode: "full" })
```

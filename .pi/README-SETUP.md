# Внешние пакеты (Pi Packages)
Необходимо для работы с [APPEND_SYSTEM](./APPEND_SYSTEM.md)

### @ollama/pi-web-search
github.com/ollama/pi-web-search
Инструменты web\_search и web\_fetch через локальную Ollama

### pi-mcp-adapter
github.com/nicobailon/pi-mcp-adapter
Адаптер MCP-протокола для Pi

### pi-ollama
github.com/CaptCanadaMan/pi-ollama
Провайдер локальных Ollama-моделей

### @aliou/pi-processes
github.com/aliou/pi-processes
Управление фоновыми процессами

### context-mode
pi install npm:context-mode
Улучшения контекста

### subagents
pi install npm:pi-subagents
Сабагенты

### pi-intercom
pi install npm:pi-intercom
Межсессионная координация

### codebase-memory (MCP сервер)
Предустановлен — кодстатистический граф через MCP (codebase-memory).
Построен: 5234 nodes, 24614 edges.

#### Использование
```
codebase_memory_search_graph({ query: "...", project: "helperium" })
codebase_memory_trace_path({ function_name: "...", project: "helperium", direction: "both", mode: "calls", depth: 3 })
codebase_memory_get_architecture({ project: "helperium", aspects: ["all"] })
```

#### Переиндексировать
```
codebase_memory_index_repository({ repo_path: ".", name: "helperium", mode: "moderate" })
```

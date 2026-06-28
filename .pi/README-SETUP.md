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

### graphify   
pi install npm:@gaodes/pi-graphify
github.com/gaodes/pi-graphify    
Граф знаний /graphify .

#### Проиндексировать граф что очень важно для качества работы агента
``` bash
/skill:ctx-index ./graphify-out/GRAPH_REPORT.md
```

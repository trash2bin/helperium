# AGENTS.md — Технический паспорт проекта для AI-агентов

Этот документ является основной точкой входа для AI-агента. Он содержит архитектурный контекст, карту навигации и операционные инструкции, необходимые для внесения изменений в код без потери целостности системы.

## 🎯 1. О проекте и видении
**Проект**: Платформа для развертывания AI-агентов над произвольными базами данных клиентов.
**Текущий вектор**: Трансформация из доменного решения (один вуз) в **Generic B2B SaaS**.

**Ключевая идея**: Клиент подключает свою БД $\rightarrow$ Платформа интроспектирует схему $\rightarrow$ Автоматически генерируется REST API и MCP-инструменты $\rightarrow$ AI-агент получает доступ к данным без написания кода под каждую БД.

### 🔄 Архитектурный Pipeline (Как это работает)
Путь запроса от пользователя до данных:
`User Request` $\rightarrow$ `demo-web` (проксирует `X-Tenant-ID`) $\rightarrow$ `demo-api` (формирует Persona агента и системный промпт) $\rightarrow$ `mcp-gateway` (динамически запрашивает манифест инструментов из data-service для конкретного TenantID) $\rightarrow$ `data-service` (роутит запрос в конкретную БД клиента через `TenantStore` $\rightarrow$ generic query builder $\rightarrow$ SQL $\rightarrow$ DB).

---

## 🛠️ 2. Карта сервисов и навигация
Каждый сервис независим и общается по HTTP. Для детального изучения архитектуры каждого модуля используйте ссылки ниже.

| Сервис | Порт | Ответственность | Документация (Кликабельно) |
|---|---|---|---|
| **Data-service** (Go) | `:8084` | Generic CRUD/Query прокси. Интроспекция БД, генерация API. | [data-service/README.md](data-service/README.md) |
| **MCP-gateway** (Go) | `:8083` | Реализация MCP-протокола. Презентация инструментов агенту. | [mcp-gateway/README.md](mcp-gateway/README.md) |
| **RAG** (Python) | `:8082` | Поиск по документам (ChromaDB), чанкинг, эмбеддинги. | [rag/README.md](rag/README.md) |
| **API** (Python) | `:8081` | Оркестратор агента, LiteLLM, управление сессиями и бэклогом. | [demo/api/agent/AGENT_WORKFLOW.md](demo/api/agent/AGENT_WORKFLOW.md) |
| **Web** (Python) | `:8080` | UI-интерфейс и reverse-proxy к остальным сервисам. | [demo/web/README.md](demo/web/README.md) |
| **SDK** (Python) | — | Общие Pydantic-модели (`Entity`) и клиенты для сервисов. | [agent-tutor-sdk/README.md](agent-tutor-sdk/README.md) |

### 🚩 Глобальные документы
- **Стратегия и План**: [doc/NEW_ROADMAP.md](doc/NEW_ROADMAP.md) — текущие фазы, карта хардкода и целевое состояние SaaS.
- **Конфигурация**: [.env.example](.env.example) — все 180+ переменных окружения.
- **Схема БД/API**: [specs/config.example.json](specs/config.example.json) — Source of Truth для структуры данных.

---

## 🚀 3. Эксплуатация и разработка (Manual)

### 🛠️ Нативный запуск: `scripts/dev.sh`
Скрипт `dev.sh` — основная точка управления в среде Mac/Linux.

**Управление сервисами:**
- `./scripts/dev.sh start` — поднять весь стек в правильном порядке (data $\rightarrow$ rag $\rightarrow$ mcp $\rightarrow$ api $\rightarrow$ web).
- `./scripts/dev.sh stop` / `restart` / `status` — управление жизненным циклом.
- `./scripts/dev.sh logs {service|all}` — просмотр логов из `.data/logs/`.

### 🐳 Docker-запуск
Если нативная среда недоступна или требуется изоляция:
- `docker compose up -d` — запуск всех 5 сервисов в Dev-режиме.
- `docker compose --profile prod up -d` — запуск с Caddy (HTTPS через Let's Encrypt) для Production.
- `docker compose build` — пересборка образов после изменений в Dockerfile.
- **Тома**: Данные хранятся в `./.data/` (БД, индексы ChromaDB, кэш моделей).

### 🗄️ Работа с данными и сценариями (Критично для тестов)
Сервис `data-service` поддерживает фабрику тестовых БД.
- `./scripts/dev.sh db list` — список доступных сценариев (`sqlite-testseed`, `big-testseed` и др.).
- `./scripts/dev.sh db materialize <name>` — создать/пересоздать БД из сценария (сброс данных).
- `./scripts/dev.sh db serve <name>` — запустить data-service на конкретном сценарии.
- `./scripts/dev.sh db test <name>` — прогнать Go-тесты на конкретном сценарии.

---

## 🧪 4. Регрессионное тестирование
Перед коммитом или после правок **обязательно** проверить следующие уровни:

### 1. Python Unit/Integration тесты
```bash
uv run pytest rag/tests/            # Проверка индексации и поиска RAG
uv run pytest demo/api/tests/       # Проверка оркестрации агента и MCP-клиента
uv run pytest demo/web/tests/       # Проверка проксирования запросов
uv run pytest agent-tutor-sdk/tests/ # Проверка generic-моделей Entity
```

### 2. Go Unit/Integration тесты
```bash
cd data-service && go test ./internal/...  # Тесты адаптеров (SQLite vs PG) и Query Builder
cd mcp-gateway && go test ./...            # Тесты MCP-протокола и генерации инструментов
```

### 3. Сквозные интеграционные скрипты
- `./scripts/integration-multi-tenancy.sh` — проверка изоляции данных между разными `X-Tenant-ID`.
- `./scripts/test-dynamic-tools.sh` — проверка, что инструменты MCP меняются при смене схемы БД.

---

## 🧠 5. Использование Knowledge Graph (Graphify)

Проект содержит граф зависимостей (`graphify-out/`). **Не читай код вслепую — используй граф.**

**Алгоритм работы для агента:**
1. **Ориентирование**: Вместо `grep` используй `graphify_explain({ concept: "ClassName" })`, чтобы увидеть всех, кто вызывает этот класс и от кого он зависит.
2. **Трассировка**: Чтобы понять, как данные текут от API до БД, используй `graphify_path({ from: "APIHandler", to: "DatabaseAdapter" })`.
3. **Поиск**: Используй `graphify_query({ question: "...", mode: "bfs" })` для поиска взаимосвязей в архитектуре.
4. **Обновление**: После внесения правок в код выполни `graphify_update({ path: "." })`, чтобы граф оставался актуальным.

---

## ⚠️ 6. Важные ограничения и правила
- **Никакого SQL в Python**: Весь доступ к университетским данным идет ТОЛЬКО через HTTP-запросы к `data-service`.
- **Generic-подход**: При добавлении новых полей или сущностей не хардкодь их в коде, наша цель оформить по-максимум рабочий generic подход без прямой правки конфигов (это будет задача из ui).
- **Stateless**: Сервисы не должны хранить состояние сессии локально (кроме кэша сессий в SQLite), чтобы обеспечить масштабируемость.

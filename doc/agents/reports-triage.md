# Разбор жалоб посетителей (widget problem reports)

Runbook: утром в админке висят жалобы с флажка «Пожаловаться» — что с ними делать,
как по каждой выйти на точное место сбоя и в какие доки идти дальше. Жалоба — это
снимок клиентского контекста + серверные ключи (`correlation_id`, `session_key`),
по которым поднимается полный серверный трейс. Содержимое жалоб **никогда** не
попадает в LLM и не расходует user-turn квоту — это чистый observability-артефакт.

**Verified:** 2026-09-06 на HEAD `b0c9fc5` — полный живой прогон: жалоба на error-бабл →
`correlation_id` → 31 строка трейса в логах → backlog-сессия → «Разобрано».

## 1. Где лежат жалобы

| Поверхность | Как |
|---|---|
| Admin UI | `admin-dashboard` → «🚩 Жалобы»: фильтр по статусу, раскрывающиеся детали, кнопка «Разобрано» (admin-only; viewer — GET-only) |
| API | `GET /admin/reports?limit=&status=` и `POST /admin/reports/{id}/status` с `API_BEARER_TOKEN`; через дашборд-прокси — `GET /api/reports`, `POST /api/reports/{reportID}/status` |
| Файл | SQLite: локально `.data/reports.sqlite3`; в Docker `infra/.data/app/reports.sqlite3` (volume `app_data` → `/data/app` в api-контейнере) |
| Метрики | `reports_total{status}`, `report_store_errors_total` → [../monitoring.md](../monitoring.md) |

Не трогай `-wal`/`-shm` файлы живой базы и не удаляй `.data` целиком — только
точечные SQL-операции на остановленном сервисе или разбор через API.

## 2. Анатомия записи

| Поле | Что даёт при разборе |
|---|---|
| `message_kind` | `assistant` — жалоба на ответ; `error` — на красный error-бабл (почти всегда серверная история) |
| `message_text` | **Сырой текст, который получил рендерер виджета** — исходник для воспроизведения рендер-бага |
| `message_tools` / `display_names` | Какие MCP-инструменты звались (и как подписаны) — заодно проверка маппинга имён |
| `transcript` | Последние ≤20 сообщений сессии (клиентский sessionStorage), каждое с `ts` |
| `comment` | Свободный комментарий посетителя — часто там визуальный симптом, которого в данных нет |
| `last_error_text` / `last_error_correlation_id` | Последняя SSE-ошибка сессии и **correlation_id хода чата** — главный grep-ключ |
| `correlation_id` | correlation_id самого POST-запроса жалобы (меньше полезен — это не ход чата) |
| `session_key` | Ключ сессии, под которым её пишет чат: `agent:{имя}:{session_id}` для именованного агента и `direct:{session_id}` для agent-less direct-чата/голоса (`session_capability.effective_session_id()`) — прямой маппинг на backlog-файл (см. §5) |
| `page_url`, `client_ip`, `user_agent` | Где и чем (браузер/ОС) воспроизводилось — важно для рендер-багов |

## 3. Первая классификация — три пути

- **A. Рендер/поведение виджета** — сервер дал нормальный ответ, на клиенте
  разъехалась таблица/код-блок, задвоились пузыри на длинном диалоге. → §4
- **B. Ошибка или странный ответ** — error-бабл, fallback «Не удалось получить
  содержательный ответ», обрыв потока. → §5
- **C. Контентный промах** — ответ по форме нормальный, но неверный/не тот.
  Это аналитика качества, а не баг. → §6

## 4. Путь A: рендер-баг

Сервер не видел отрисовку — но жалоба хранит `message_text`, а это ровно тот
вход `renderMarkdown` (`services/api-service/embed/src/messages.ts`), из которого
воспроизводится любой рендер-баг:

1. Скопируй `message_text` из деталей жалобы.
2. Прогони через рендерер локально: витт-тест в `services/api-service/embed/tests/`
   (jsdom, `renderMarkdown` + `addMessage`) или страница с виджетом. Как поднять
   окружение — [../../services/api-service/embed/README.md](../../services/api-service/embed/README.md),
   как гонять suite — [testing-guide.md](testing-guide.md).
3. Сверь `user_agent` из жалобы: проблема может быть конкретно в мобильном
   вьюпорте/браузере, а не в разметке.
4. CSS-сторона — `services/api-service/embed/css/` (переполнение таблиц, длинные
   слова, высота контейнера).

Ничего не хранится «как выглядело» — скриншота нет, поэтому при чисто визуальном
симптоме, который не воспроизводится из исходника, опираешься на `comment` и UA.

## 5. Путь B: ошибка сервера/модели

Два ключа из жалобы, дополняющие друг друга:

```bash
# Ключ 1: correlation_id хода → точный трейс запроса в логах
grep "<last_error_correlation_id>" .data/logs/api.log        # нативный стек
docker compose logs api | grep "<last_error_correlation_id>" # Docker-деплой

# Ключ 2: session_key → вся серверная сессия в backlog-файле
# agent:autoparts-assistant:<sid>  →  $BACKLOG_DIR/agent_autoparts-assistant_<sid>.jsonl
# direct:<sid> (чат без агента)    →  $BACKLOG_DIR/direct_<sid>.jsonl
less backlog/agent_autoparts-assistant_<sid>.jsonl
```

Цепочка идентификаторов сквозная: браузер → demo-web прокси → api-service →
SSE-события несут **один** correlation id (x-correlation-id middleware, прокси
форвардит свой id наверх, loop-error события содержат его в payload). Если grep
внезапно пуст — проверь, что grepаешь нужный id: `last_error_correlation_id` (ход
чата), а не `correlation_id` (сам POST жалобы).

Логи: нативный стек — `.data/logs/api.log` (`./infra/scripts/dev.sh logs api`),
Docker — `docker compose logs api`; лог-формат JSON, поле `correlation_id` есть
даже у `Request started/completed`. Эксплуатация и запуск —
[operations.md](operations.md), межсервисный маршрут запроса —
[../api-flow.md](../api-flow.md), прокси demo/web — [web-service.md](web-service.md).

### Backlog-файл

`BACKLOG_MODE=full` (дефолт) пишет per-session JSONL: человекочитаемые JSON-записи
с разделителем `---===---`. Типы событий: `turn_start` (turn_id, вопрос юзера),
`llm_call` (модель, токены, длительность), `tool_call`/`tool_result`, `error`,
`turn_end`; внутри сессии всё связано `turn_id`. Retention — `BACKLOG_RETENTION_DAYS=30`.
Описание решения — [backlog-product-decision.md](backlog-product-decision.md),
env-переменные — [../../services/api-service/README.md](../../services/api-service/README.md).

### Типовые сигнатуры в логах и куда дальше

| Сигнатура в логе | Что это | Дальше смотреть |
|---|---|---|
| `fabricated tool-call envelope`, `completion security iteration=N` | Модель фабрикует tool-call разметку текстом; security-loop регенерирует | [tool-call-safety-layers.md](tool-call-safety-layers.md), README api-service (answer normalizer) |
| LLM provider timeout / retry deadline | Провайдер висит/падает, retry-политика сработала | [http-clients.md](http-clients.md), README api-service (раздел LLM/retry) |
| `429` + `Retry-After` | Rate limit (chat limiter или per-IP отчётов `REPORTS_RATE_LIMIT`) | [anti-abuse.md](anti-abuse.md) — квоты и лимиты |
| `[MCP] … is_error`, session reconnect | Ошибка MCP-инструмента/сессии | [mcp-session-lifecycle.md](mcp-session-lifecycle.md), [../../services/mcp-gateway/README.md](../../services/mcp-gateway/README.md), [security-isolation.md](security-isolation.md) |
| data-service `5xx`, SQL-ошибки | Read-only контур/схема tenant-БД | [../../services/data-service/README.md](../../services/data-service/README.md), [adapter-pattern.md](adapter-pattern.md) |
| Обрыв SSE без терминального события | Прокси/сеть закрыли поток; виджет обязан показать error-бабл | [../api-flow.md](../api-flow.md), [web-service.md](web-service.md), embed README (sse.ts) |

## 6. Путь C: контентный промах

Модель ответила неверно: сверяй по backlog, что реально вернул инструмент
(`tool_result`) и что модель из этого собрала (`final`). Если инструмент вернул
правду, а ответ неверный — вопрос качества модели/prompt'а: гоняй
[../benchmark/README.md](../benchmark/README.md) и
[../benchmark/core-benchmark.md](../benchmark/core-benchmark.md) на том же fixture,
прежде чем менять промпты. Если врёт сам инструмент (SQL/маппинг) —
[adapter-pattern.md](adapter-pattern.md) и README data-service.

## 7. Клиентское состояние виджета (для воспроизведения)

sessionStorage вкладки (префикс по имени агента): `at_messages_<agent>`
(история с транскриптом), `at_session_<agent>` (session id — тот самый, что в
`session_key` жалобы), `at_reported_<agent>` (уже пожалованные сообщения). Нюансы:
transcript капится 20 сообщениями, пожалованное сообщение всегда сохранено в
записи целиком отдельными полями, «уже пожаловался» живёт только в пределах вкладки.

## 8. Жизненный цикл и гигиена

- Разобрал — жми «Разобрано» (`status: new → reviewed`); фильтры в UI строятся на этом.
- `REPORTS_RETENTION_DAYS=0` (хранить вечно) — на VPS задай конечное значение;
  cleanup выполняется при старте api-service.
- `REPORTS_RATE_LIMIT` (дефолт `5/minute`, per-IP) защищает сам эндпоинт; жалоба
  не списывает user-turn квоту сессии — квоты чата описаны в
  [anti-abuse.md](anti-abuse.md).
- Алерты/дашборды — через `reports_total`/`report_store_errors_total`
  ([../monitoring.md](../monitoring.md)); рост `report_store_errors_total` — проблема
  записи в SQLite (диск/права), а не посетителей.
- Публичные ошибки sanitised: DSN/пути/хосты в жалобах и логах не светятся —
  если видишь утечку, это defect: [../../doc/PENTEST-CHEK.md](../PENTEST-CHEK.md).

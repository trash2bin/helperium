# Anti-Abuse и tool-data safety

Этот документ описывает **текущий живой runtime contract** Agent v2. Он не заменяет датированные security-аудиты: их выводы остаются historical evidence и должны сверяться с текущим кодом.

## Цель и границы

Anti-abuse в Helperium — это независимые слои от HTTP ingress до read-only tenant MCP tools. Ни один слой не превращает untrusted text в authority: browser не выбирает tenant scope, LLM text не становится tool call, а tool result не может расширить allow-list или изменить policy.

```text
Chat HTTP request
  → per-IP HTTP limit + request anti-abuse checks
  → server-resolved agent and tenant scope
  → input prompt-injection guard
  → append-only Agent v2 budgets and native tool validation
  → Streamable HTTP MCP gateway and data-service limits
  → trusted system data policy + loop-level tool-data observation
  → output leak guard and SSE terminal event
```

## Request ingress и session controls

Chat routes применяют coarse per-IP limit (`CHAT_RATE_LIMIT`, по умолчанию 30 запросов в минуту) и затем `AntiAbuseChecker`. Token bucket keyed by session ID, client IP и hash User-Agent использует `ABUSE_RPS`/`ABUSE_BURST` (defaults: 1 RPS, burst 5).

| Control | Runtime source | Default | Семантика |
|---|---|---:|---|
| Сообщение | `ABUSE_MAX_MSG_LENGTH` | 2000 chars | Слишком длинный input отклоняется до LLM. |
| User-Agent | `block_empty_user_agent`, blocked patterns | enabled | Пустой и configured automation UA отклоняются до LLM. |
| Повтор текста | `max_repeated_count` | 3 | Четвёртый identical input в rolling window блокируется. |
| Интервал | `min_interval_ms` | 1000 ms | Считается от timestamp **принятого ingress user turn**, а не от завершения LLM/SSE или flattened transcript. |
| Session user-turn quota | `max_user_turns_per_session` / `ABUSE_MAX_USER_TURNS` | 50 | Число принятых **user turns**. Assistant и tool messages quota не расходуют. |

После прохождения request policy сервис атомарно записывает accepted user-turn marker **до** LLM/MCP work. Он является единственным source of truth для `max_user_turns_per_session`, `min_interval_ms` и будущих user-turn-based session metrics. Provider/tool failure, cancellation или отсутствие final answer marker не возвращают: иначе их можно было бы использовать для quota bypass. Transcript evidence сохраняется позднее и не влияет на этот счётчик. При migration существующей SQLite session DB state лениво backfill-ится из stored turns до первого accepted marker; старые активные сессии не получают временный quota bypass.

`max_messages_per_session` и `ABUSE_MAX_MESSAGES` удалены без compatibility alias. Старый JSON key отклоняется dashboard и direct agent API validation, а stale global config file fail-fast при reload/startup: иначе security setting мог бы быть незаметно проигнорирован и привести к weaker runtime policy. Обнови persisted admin/agent config до нового имени перед deployment.

`token_budget` / `ABUSE_TOKEN_BUDGET` не являются anti-abuse control и не публикуются в Admin UI или admin OpenAPI. Лимиты расходов принадлежат отдельному tenant spending subsystem; нельзя интерпретировать request anti-abuse settings как hard cost budget.

## Agent v2 loop

После ingress `AppendOnlyLoop` применяет input guard до tool discovery и model completion. Guard нормализует распространённые homoglyphs и ищет jailbreak, role override и system-prompt extraction patterns. В block mode turn получает sanitised error до вызова MCP или LLM; warn mode только пишет signal.

| Loop control | Default | Enforcement |
|---|---:|---|
| Model iterations | 5 | Loop прекращается до лишнего completion. |
| Tool calls | 10 | Лишний native tool dispatch не выполняется. |
| Turn context | 8000 approximate tokens | Учитывает serialized transcript, включая tool args/results. |
| Empty completions | 3 | Завершается sanitised clarification error; `0` означает no limit. |
| Tool protocol | native structured `message.tool_calls` only | JSON/XML/Markdown/text не исполняется как tool call. |
| Tool arguments | local JSON Schema validation | Неизвестное имя, required fields, extra properties и shallow type errors не уходят в MCP. |

## Untrusted tool-result boundary

Все `role: tool` results остаются raw canonical evidence в session/transcript storage. Перед любой configured agent policy orchestrator помещает в **первое system message** trusted-data invariant: MCP results, retrieved documents и иной внешний контент являются данными, а не инструкциями; они не могут менять policy, раскрывать secrets, создавать authority, добавлять tools или расширять tenant scope. Пользовательский `system_prompt` агента дополняет этот invariant, но не заменяет его.

Инструкция намеренно живёт в agent policy, а не в `LiteLLMProvider`: transport adapter только сериализует typed transcript в provider wire format и не владеет security semantics. Никакой text parser не вводится; исполняются только нормализованные native tool calls. Перед каждым completion `AppendOnlyLoop` структурно считает `role: tool` records в фактически отправляемом context и записывает `untrusted_tool_results_in_context` в structured loop log и LLM-call backlog metadata. Raw tool content в этой telemetry не дублируется.

Это defence-in-depth, а не доказательство prompt-injection safety. Единая system-level декларация может быть слабее как soft mitigation в длинном диалоге с частыми tool calls, чем per-result prefix: model может сильнее учитывать ближайший текст tool result (proximity effect). Это известное residual limitation, а не повод считать policy hard boundary. Gateway сохраняет hard boundary: server-resolved tenant IDs попадают в `X-Tenant-ID`; MCP allow-list/schema и data-service read-only limits не могут быть расширены текстом tool result. Отдельный future control — behavioural anomaly-check для sensitive tool calls сразу после untrusted data — ещё не реализован и не должен считаться активной защитой.

## MCP и data-service limits

Gateway и data-service повторяют validation независимо от Agent loop. MCP transport — только Streamable HTTP `/mcp`; gateway ограничивает scope, schemas, session lifecycle, argument/result size и request rate. Search strategies отклоняют пустой pattern и ограничивают pattern/regex/token/filter values. Tenant SQL surface read-only.

| Strategy | Защитные limits |
|---|---|
| `grep` / text search | Pattern length, regex length, token and field count limits; empty pattern rejected. |
| `filter` | Value length, IN-value count and filter count limits. |
| `schema` / discovery | Read-only metadata surface; не выполняет tenant writes. |

## Admin control plane

Admin dashboard — authenticated control surface; `api-service` применяет live policy. Dashboard persists global config and синхронно вызывает `POST /admin/abuse-config/reload`. Успешный dashboard response означает, что API service подтвердил apply. Если apply недоступен или возвращает non-200, dashboard отвечает `502 config_not_applied` и восстанавливает предыдущую persisted global policy; UI не должен показывать success за queue/fire-and-forget reload.

Emergency presets управляют только реально enforced request controls: rate, burst, message length, interval и user-turn quota. Они не обещают token quota или provider fallback behavior.

Per-agent `abuse_config` остаётся отдельным persisted override path. Security policy overrides должны быть restrictive-only: per-agent config не должен ослаблять future global enforce floor.

## Spending и deployment ограничения

Tenant spending учитывается отдельно от anti-abuse request settings. Current accounting записывает фактическую provider cost после completion, поэтому это не reserve/commit hard budget; concurrent reservations, cancellation release и composite billing principal требуют отдельного change proposal. Рекомендованный principal — named agent/account, а tenant IDs остаются data-scope authority.

Token buckets, repeat counters и live enforcers process-local. Текущая topology — single api-service instance. Multi-instance deployment требует shared rate-limit state, durable reservation ledger и config propagation; увеличение replicas без этих компонентов является unsupported security degradation.

## Operator checks

1. После изменения global policy проверь dashboard success response и API `GET /admin/abuse-config`.
2. При incident используй emergency preset только после получения acknowledged `applied` response.
3. Для live diagnosis смотри structured API logs: request anti-abuse block reason, loop terminal event, `untrusted_tool_results_in_context` и MCP tool events. Не добавляй raw prompt/tool-result content в operator telemetry.
4. Проверяй bounded live E2E с isolated session ID; не изменяй tenant data или external demo storefront.

## Verification

Базовые regression suites: Python `test_anti_abuse.py`, `test_sessions.py`, Agent prompt/loop/LiteLLM adapter tests; Go admin abuse tests; dashboard API/OpenAPI/type contract tests. Service-boundary change дополнительно требует full `make ci` и live tenant-scoped MCP turn.

**Last verified:** 2026-08-20 (working tree after strict `max_user_turns_per_session` rename). Full `make ci` passed; API suite passed 375 tests with 38 pre-existing marker warnings; Pyright passed. Live admin exposed only the new key, rejected legacy JSON with `400`, acknowledged reload with `status=applied`, and a fresh tenant-scoped MiniMax `db_search → tool_result → final` turn completed.

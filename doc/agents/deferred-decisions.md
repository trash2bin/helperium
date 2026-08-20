# Карта отложенных решений после Agent v2 hardening

**Статус:** active decision artifact.

**Дата:** 2026-08-21.
**Baseline:** `83e7fa0` после Agent v2 anti-abuse workstream и исправления Docker E2E lifecycle.

Этот документ отделяет **осознанно отложенную продуктовую работу** от дефектов, которые должны быть исправлены до demo. Он не является backlog задач «сделать всё»: каждое направление имеет собственный trigger, решение владельца и критерий готовности. До наступления trigger его не следует реализовывать как превентивный refactor.

> **Принцип:** публичная demo должна доказывать основную ценность продукта — tenant-scoped read-only data access через agent и MCP — без ложного обещания enterprise-scale billing, multi-region HA или полной защиты от любых LLM prompt-injection сценариев.

## Текущая доказанная база

| Контур | Подтверждённый contract | Evidence |
|---|---|---|
| Data boundary | Tenant database surface read-only; browser не выбирает tenant scope; MCP получает server-resolved `X-Tenant-ID`. | [AGENTS.md](../../AGENTS.md), [security isolation](security-isolation.md) |
| Agent/tool protocol | Исполняются только native structured tool calls; text/Markdown/XML не становятся tool call. | [anti-abuse guide](anti-abuse.md) |
| Tool-data safety | Mandatory system invariant помечает tool/RAG content как data; hard boundaries остаются в scope, schema, allow-list и data-service. | [anti-abuse guide](anti-abuse.md#untrusted-tool-result-boundary) |
| Request controls | Per-IP/request limits, accepted-at user-turn quota, interval, repetitive-input control и bounded loop реально enforced. | [anti-abuse guide](anti-abuse.md#request-ingress-и-session-controls) |
| Control plane | Dashboard apply синхронно подтверждается API; failed apply откатывает persisted config. | [admin guide](../../services/admin-dashboard/README.md) |
| Delivery confidence | Full local CI, clean Docker E2E и live tenant-scoped MiniMax path были пройдены; CI E2E не зависит от normal exit init container. | [testing guide](testing-guide.md), [CI/CD guide](ci-cd.md) |

Эта база достаточна для **ограниченной публичной demo** при честно заданных operational limits. Она не превращает текущий single-instance deployment в enterprise SaaS.

## Карта решений и порядок возврата

| Приоритет | Решение | Статус сейчас | Не блокирует ограниченную demo? | Обязательный trigger |
|---:|---|---|---|---|
| P0 | Deployed-domain acceptance и operational readiness | Не заменяется local evidence | Нет — для публичного домена требуется перед открытием | Появляется внешний URL/реальный browser traffic |
| P1 | Reserve/commit spending и billing principal | Post-hoc tenant cost accounting | Да | Нужен hard spend cap, paid access или несколько agents/accounts |
| P1 | Shared anti-abuse/config state | Process-local single instance | Да | Нужны две и более API replicas или HA |
| P2 | Behavioural anomaly check after untrusted data | Не реализован намеренно | Да | Появляются sensitive/write-capable tools либо incident signal |
| P2 | MCP SDK negotiation workaround removal | `mode="legacy"` compatibility mode | Да | mcp-go добавляет `server/discover` либо SDK безопасно fallback-ится |
| P2 | Backlog/evidence product model | Current dialog evidence only | Да | Нужна выгрузка, training/evaluation dataset, replay UI или новая retention policy |
| P3 | Durable cumulative tool-data telemetry | Есть per-completion `…_in_context`, нет session-wide `seen` | Да | Реальная потребность в forensics/alerting и приняты retention/privacy rules |

## P0 — что действительно сделать до открытия публичной demo

Это не новый архитектурный workstream и не причина останавливать продукт. Это короткий **release gate** после появления настоящего target domain и до публикации ссылки.

| Проверка | Риск, который закрывает | Минимальное evidence | Owner decision |
|---|---|---|---|
| Browser acceptance на deployed domain | Отличие между localhost и реальным CORS/embed/cookie/proxy path | Manual browser journey: widget load → chat → tool result → final/error; console без mixed content/CORS errors | Product/engineering |
| CORS и embed origins | Случайный wildcard или новый origin без explicit allowlist | Environment review плюс preflight from allowed и disallowed origin | Engineering |
| Production secrets/config | Demo с dev/default credentials или неверной tenant binding | Distinct admin/viewer/MCP keys; explicit origins; config review без публикации values | Operator |
| Bounded incident procedure | Оператор не умеет выключить abuse/LLM path честно | Проверенный dashboard apply/rollback и emergency preset runbook | Operator |
| Read-only tenant confirmation | Demo source accidentally получил write access | Tenant credentials and manifest review; no writes in trace | Engineering/operator |

Публичная demo должна прямо сообщать границы: **read-only**, supported single-instance topology, no financial hard cap, no guarantee answer correctness beyond supplied tenant data. Это уменьшает product risk сильнее, чем ещё один внутренний refactor.

## P1 — spending reserve/commit и billing principal

### Почему текущая реализация не является бюджетным лимитом

Provider usage/cost нормализуются на provider boundary, но spending tracker записывает фактическую стоимость **после completion**. Следовательно, параллельные requests могут пройти до того, как cost окажется учтён. Composite data scope также не является корректным billing identity. Это правильно отделено от anti-abuse: request limits нельзя выдавать за money controls.

### Решение, которое нужно принять

| Вопрос | Рекомендованная позиция | Почему |
|---|---|---|
| Billing principal | Named agent/account | Agent/account выражает плательщика; tenant ID выражает только data authority и может быть composite. |
| Enforcement model | Reserve before completion, commit actual cost after completion, release unused reserve on cancellation/failure | Только такая модель позволяет обещать hard/pre-authorized cap. |
| Idempotency | Stable request/attempt ID и durable ledger | Retry, SSE reconnect и provider timeout не должны списывать дважды. |
| Multi-instance storage | Shared durable ledger | In-memory counter не переживает replica/process boundary. |
| Unknown final cost | Conservative reserve policy плюс explicit fail/limit behavior | Provider может вернуть usage поздно либо не вернуть вовсе. |

### Trigger и minimum proposal

Открыть отдельное change proposal, когда продукт вводит paid usage, external quotas или обещает hard cost ceiling. Proposal обязан содержать principal model, ledger schema, reserve/commit/release state machine, idempotency matrix, provider failure semantics, composite-scope treatment, migration and reconciliation plan. Нельзя начинать с переноса текущего класса в «billing abstraction»: это улучшит вид кода, но не решит race и double charge.

## P1 — multi-instance anti-abuse и config propagation

### Current boundary

Token buckets, repeat counters, session/cache views и live enforcers рассчитаны на один `api-service` process. MCP session/cache также stateful. Увеличение replica count без общей state layer создаёт не просто availability difference, а ослабление abuse controls.

### Required decision packet

| Компонент | Минимальное решение до scale-out |
|---|---|
| Rate/repeat state | Redis or equivalent shared, expiring, atomic counters/buckets |
| Session admission | Durable accepted-turn state with concurrency-safe admission semantics |
| Abuse config | Versioned config propagation plus acknowledged rollout/rollback across instances |
| Spending | Reserve/commit ledger from previous decision |
| MCP sessions | Sticky sessions or horizontally safe session ownership strategy |
| Observability | Instance-labelled metrics, alerting and rollback game day |

**Trigger:** any deployment with more than one API replica, automatic restart/rolling deployment policy that can overlap instances, or an HA/SLO promise. Until then the honest product constraint is single instance.

## P2 — behavioural anomaly check after untrusted data

### What exists and what does not

Tool results are structurally constrained: they cannot change resolved tenant scope, create new tool names, bypass schema validation or turn text into a tool call. The mandatory system instruction is useful defence-in-depth but is not a proof that the model ignores hostile prose. Current telemetry accurately reports how many tool results the model sees in a completion context; it does not judge model intent.

### Why this remains a separate design

An anomaly detector needs a definition of **sensitive action**, an allowed baseline, human-review/escalation behavior and a false-positive policy. A generic rule such as “block every tool after any tool result” would break legitimate multi-step read-only agent workflows, including `search → get`.

### Trigger and acceptance criteria

Start this proposal only when write-capable tools, external side effects, privileged data classes, or a real incident justify the additional policy. The proposal must define protected actions, observed signals, warn/block modes, user-safe errors, audit fields without raw sensitive content, bypass governance and replay tests.

## P2 — MCP SDK `mode="legacy"` compatibility workaround

This label is easy to misread. It does **not** restore retired SSE/JSON-RPC MCP transport. The current client still uses Streamable HTTP `/mcp` and standard `initialize`; `mode="legacy"` only suppresses the Python SDK v2 automatic post-handshake `server/discover` request because current mcp-go does not implement it.

**Removal condition:** upgrade only after mcp-go supports `server/discover`, or the SDK reliably falls back to `initialize`. The verification must cover initialization, tools discovery, tool call, session cleanup, tenant scope and cross-turn continuation. Do not replace the workaround with vendor-specific client code or reintroduce obsolete transports. See [MCP session lifecycle](mcp-session-lifecycle.md#python-sdk-negotiation-compatibility).

## P2 — backlog, benchmark and training-data boundary

The backlog is intentionally **dialogue evidence**, not generic logging. It stores a reproducible turn trace for quality investigation and benchmark work. Runtime logs diagnose services; metrics/tracing aggregate operations. Mixing them would contaminate training/evaluation evidence, enlarge privacy exposure and turn a useful trace store into an unbounded log sink.

The separate decision record is [backlog product decision](backlog-product-decision.md). Before changing this subsystem, decide all four items below together:

1. Versioned turn-level record schema and replay compatibility.
2. Redaction/retention and explicit consent for any training/evaluation export.
3. Storage/UI separation between technical events and dialogue evidence.
4. Benchmark reader compatibility and archive migration.

## P3 — cumulative tool-data telemetry

`untrusted_tool_results_in_context` answers the narrow question: **what tool-result records did the model see in this completion context?** It is a snapshot and can decline when whole historical turns are trimmed. This is more honest for per-call risk correlation than a misleading monotonic counter.

A future `untrusted_tool_results_seen` must not be silently added. It would require a decision about session identity, storage lifetime, retention, privacy/redaction, reset semantics, aggregation and whether it is a security signal, an operator alert, or benchmark evidence. Until such a need exists, the current per-completion metric and existing LLM-call metadata are sufficient.

## Decision sequence after public demo

The appropriate next item is determined by product event, not by aesthetic preference:

```text
Public demo URL
  └─→ Complete P0 deployed-domain release gate

Paid usage / hard cap promise
  └─→ Design reserve/commit billing first

Second API replica / HA commitment
  └─→ Design shared state and config propagation first

Sensitive or write-capable tool
  └─→ Design behavioural anomaly control first

Benchmark/training/replay expansion
  └─→ Decide backlog retention and evidence model first

No trigger yet
  └─→ Improve demo quality, public positioning, feedback capture and benchmark evidence
```

## Explicit non-actions

The following actions are intentionally **not** recommended now:

- Do not introduce Redis merely because it is a familiar infrastructure component.
- Do not call a post-hoc cost record a token budget or a hard spending cap.
- Do not add a cumulative security counter without retention/privacy semantics.
- Do not turn `mode="legacy"` into a provider-specific routing branch or reintroduce MCP SSE routes.
- Do not pollute dialogue backlog with startup, healthcheck or arbitrary runtime logs.
- Do not block a narrowly scoped public demo on enterprise-scale architecture that the demo does not claim.

## Review cadence

Review this artifact at three events: before public demo publication, before promising a paid/hard-limited offering, and before running more than one API replica. Update it only when one decision is actually made; otherwise it remains a stable map of deliberate deferrals.

## References

- [AGENTS.md](../../AGENTS.md)
- [Anti-abuse and tool-data safety](anti-abuse.md)
- [Backlog product decision](backlog-product-decision.md)
- [MCP session lifecycle](mcp-session-lifecycle.md)
- [Testing guide](testing-guide.md)
- [CI/CD guide](ci-cd.md)

# Stress harness — эксплуатация (runbook)

> Дизайн и контракты: [README.md](README.md) (v4). Этот документ — как **запустить**:
> команды, что они пишут, и как читать результат. Карта доков — в [AGENTS.md](../../AGENTS.md).

Статус: фаза 1 (L1, MCP) — готова к прогону на стенде; фаза 2 (stub-LLM + чат-драйвер L2)
— код есть, на живом стенде ещё не гонялся. Чисел нет — см. §6 README: прогон без
полного манифеста числом не считается.

---

## Что где живёт

| Что | Где |
|---|---|
| Харнесс (Python-пакет) | `services/agent-db/agent_db/stress/` |
| CLI | `python -m agent_db.stress` / `agent-db-stress` |
| Профили нагрузки | `services/agent-db/agent_db/stress/profiles/*.json` |
| Фикстуры аргументов тулов | `services/agent-db/agent_db/stress/fixtures/*.json` |
| Артефакты прогона | `test-results/stress/<run_uuid>/` (в `.gitignore`) |
| Тесты пакета | `services/agent-db/tests/test_stress_*.py` (офлайн, без сети) |

Компоненты: `transport` (MCP), `chat_transport`/`chat_driver` (SSE-чат L2+),
`stub_llm` (подложка модели), `runner` (планировщик стадии), `ladder` (knee-лестница),
`manifest`/`report` (манифест и отчёт), `guard` (эксклюзивный прогон),
`evidence` (§10 layout), `preflight`.

## Команды

```bash
# из корня репо; тесты пакета — офлайн и быстрые
PYTHONPATH=$PWD uv run -- python -m pytest services/agent-db/tests/test_stress_*.py -q

# префлайт: стенд вообще способен прогнать этот профиль?
uv run --package agent-db agent-db-stress check \
  services/agent-db/agent_db/stress/profiles/mcp-tool-call-l1.json \
  --mcp-url http://127.0.0.1:8083/mcp --api-key "$MCP_API_KEY" \
  --tenants stress-1,stress-2,stress-3,stress-4

# сухой прогон: план лестницы + манифест, без тиков
agent-db-stress run <профиль> --mcp-url ... --tenants ... --dry-run

# прогон лестницы
agent-db-stress run services/agent-db/agent_db/stress/profiles/mcp-tool-call-l1.json \
  --mcp-url http://127.0.0.1:8083/mcp --api-key "$MCP_API_KEY" \
  --tenants stress-1,stress-2,stress-3,stress-4 \
  --artifact-root test-results/stress --repo-root . \
  --rates 5,10,20,40,80 --duration-s 300 --warmup-s 60 --repeats 2
```

Ключевые опции `run`: `--rates`, `--duration-s`, `--warmup-s`, `--repeats`,
`--t-budget-ms` (по умолчанию из §7: L1 50 мс, L2/L3 500 мс), `--tool-p95-ms`
(аналитический прогноз §3), `--budget NAME=VALUE` (что реально поднято на стенде, §5),
`--no-expand-pool`, `--dry-run`, `--no-host-probe` (только для тестов; на стенде
проб хоста оставлять включённым — иначе манифест неполон и прогон непубликуем).

Для L2/L3 добавляются: `--chat-url http://127.0.0.1:8081` и опционально `--agent <имя>`.

Exit-коды: `0` — прогон завершён (вердикт может быть fail — это тоже измерение);
`2` — не стартовал (префлайт/лок/профиль); `3` — aborted (причина в
`status.json.abort_reason`).

## Стенд (Linux + Docker), с нуля

```bash
# 1. зависимости: docker + compose plugin, git, uv
# 2. репо и env
git clone <repo> helperium && cd helperium
cp .env.example .env   # при необходимости поправить

# 3. поднять SUT: тест-профиль ОБЯЗАТЕЛЕН — он поднимает MCP_RATE_LIMIT_RPS
#    и ABUSE_IP_* с дефолтов до 1000, иначе лестница упрётся в лимитер (§5)
./infra/scripts/compose.sh --profile test up -d data-service mcp-gateway api admin-dashboard web

# 4. мониторинг (опционально, но полезно смотреть во время прогона)
./infra/scripts/compose.sh --profile monitoring up -d prometheus grafana

# 5. фиктивная БД + тенанты: сценарий sqlite-testseed (6 сущностей, сид в репо)
uv run --package agent-db agent-db materialize sqlite-testseed
for i in 1 2 3 4; do
  ADMIN_TOKEN=... uv run --package agent-db agent-db register "stress-$i" sqlite-testseed
done

# 6. префлайт → прогон (см. команды выше)

# 7. после прогона
./infra/scripts/compose.sh --profile test down -v   # демо-контейнеры autoparts-store не трогать
```

Проверка, что фиктивная БД живая (то, что делает префлайт за тебя):

```bash
curl -s -H "X-Tenant-ID: stress-1" -H "Authorization: Bearer $MCP_API_KEY" \
  http://127.0.0.1:8083/mcp/manifest | head
# ожидание: 6 тулов (db_map, db_describe, db_filter, db_get, db_related, db_search)
```

## L2/L3: подложка модели

Стаб — часть прибора (§4), не SUT. Поднять его рядом со стендом (хост должен быть
доступен контейнерам api-service: на Linux это адрес хоста в docker-сети):

```bash
agent-db-stress stub \
  --p50-ms 800 --p95-ms 2500 --prompt-ms-per-token 2 \
  --script services/agent-db/agent_db/stress/fixtures/stub-script.jsonl \
  --log /tmp/stub-timings.jsonl --concurrency 256 --port 9099
```

(Формат `--script`: JSONL-строки `{"tool": "db_get", "arguments": {...}}` — раунды хода;
после исчерпания скрипта ход завершается финальным текстом. Раунды определяются по
транскрипту — серверу не нужен курсор, поэтому многораундовый диалог воспроизводим,
в отличие от `USE_SCRIPTED_LLM`.)

Чтобы api-service ходил в стаб: в агенте, которым грузим (`--agent`), прописать
`llm_config` с `provider: "openai"`, `model: "stub"`,
`api_base: "http://<host>:9099/v1"`, `api_key: "sk-stub"` — пер-агентный `api_base`
сохраняется провайдером (`test_provider_config_preserves_api_base`), правок кода не нужно.

Артефакт стаба (`--log`) кладите в прогон опцией `run --stub-log <путь>`: харнесс
скопирует его в `stub/` директории прогона, потому что `platform_overhead` считается
как `t_complete − Σ service_ms` по этим записям, и без них отчёт теряет главный
столбец §7 (прогон с `--stub-log` на несуществующий файл отклоняется на префлайте).

## Что читать после прогона

| Файл | Что это |
|---|---|
| `report-*.md` | таблица §10, knee-график, оговорки, блокеры публикации |
| `report.json` | то же машиночитаемо: вердикты, knee, `blockers`, `notes` |
| `run-manifest.json` | environment/code split; `publication_blockers()` пуст → прогон публикуем |
| `preflight.json` | что проверено до первого тика (фикстуры, тенанты, бюджеты, quiet-host) |
| `status.json` | `running/completed/failed/aborted` + `abort_reason` |
| `raw/<stage>.jsonl` | **канонический источник перцентилей** (по записи на ход, CO-компенсация) |
| `raw/<stage>.calls.jsonl` | подекомпозиция по вызовам (без собственных плановых смещений) |
| `generator/<stage>.json` | CPU/лаг/дропы/хвост генератора: валидность стадии |
| `stub/*.jsonl` | тайминги стаба (для L2/L3) |

Публикация числа (§2/§6): медиана ≥2 повторов knee, манифест полный, дерево чистое,
`environment` совпадает при сравнении прогонов. Одиночный прогон — «форма, не ставка».

## Известные границы (не забыть на стенде)

- Генератор и SUT на одном хосте делят CPU: при широких пулах (высокие rps) — выносить
  генератор на отдельную машину (`--generator-host` в манифесте это фиксирует). Гейт
  насыщения генератора (0.8 ядра в измеренном окне) инвалидирует стадию сам.
- `ulimit -n` и эфемерные порты — потолок прибора при пуле в сотни воркеров (§6).
- `CHAT_RATE_LIMIT` (30/min на путь = 0.5 rps) не поднят тест-профилем: для L2+ его
  надо поднять в окружении api и зафиксировать через `--budget chat_rate_limit=...`.
- Серверных метрик прогон пока не собирает (колонки `SUT CPU`/`mcp lock p95` печатаются
  как `n/a` с причиной); backlog-джойн для `platform_overhead` на L2/L3 — следующий шаг.

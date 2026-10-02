# Stress harness — эксплуатация (runbook)

> Дизайн и контракты: [README.md](README.md) (v4). Этот документ — как **запустить**:
> команды, что они пишут, и как читать результат. Карта доков — в [AGENTS.md](../../AGENTS.md).

Статус: фаза 1 (L1, MCP) — прогоняется на стенде целиком (CLI → лестница → манифест →
отчёт → дерево улик); фаза 2 (stub-LLM + чат-драйвер L2) — код есть, стаб проверен офлайн,
на живом стенде ещё не гонялся. Чисел нет — см. §6 README: прогон без полного манифеста
числом не считается.

Последняя сквозная проверка механики: 2026-09-29, нативный стенд на Darwin-ноутбуке
(data-service + mcp-gateway на 127.0.0.1:18084/18083, `sqlite-testseed`, 4 тенанта),
лестница 20→40→80 rps по 20 s — все ступени валидны, 0 ошибок, перцентили отчёта
независимо воспроизведены из `raw/*.jsonl`. Числа непубликуемы и таковыми помечены:
манифест перечислил 8 блокеров (грязное дерево, нет записанных бинарей, ненаблюдаемые
`cpu_governor`/`disk_type`/cgroup-поля), генератор и сервисы делили 8 ядер.

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
`--binary LABEL=PATH[:SOURCE_ROOT]` (артефакт сборки, §6 — без него нативный прогон
непубликуем), `--no-expand-pool`, `--dry-run`, `--no-host-probe` (только для тестов;
на стенде пробы хоста оставлять включённым — иначе манифест неполон и прогон непубликуем).

Серверные метрики (§10): `--admin-token` (bearer для `/metrics` data-service и
api-service; `/metrics` fail-closed за тем же токеном, что `/admin/*`),
`--metrics-target SERVICE=URL` (endpoint, который харнесс сам не знает — адрес
data-service выводить неоткуда, он с ним напрямую не говорит), `--metrics-interval-s`
(по умолчанию 5), `--no-server-metrics` (не скрейпить; `server/` остаётся с
GAP-маркером, и числа прогона остаются самосверкой). Шлюз и api-service выводятся
из `--mcp-url` / `--chat-url` автоматически.

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

## Удалённый стенд по SSH

Стенд можно держать на отдельной машине (домашний Linux в локалке) и гонять нагрузку
по SSH. Главное — отвязать запуск от живого терминала и обойти сетевые и файловые
грабли удалённого Docker. Ниже операционный минимум, проверенный на Arch-стенде;
он же работает на любом Linux с Docker и compose. Замени `<user>@<host>` на свой адрес.

**Доступ без пароля.** Пароль в строке для скриптованного прогона — не вариант:
`BatchMode` требует ключ, а expect-обёртки хрупки. Сначала ключ, дальше команды
идут сами:

```bash
ssh-copy-id <user>@<host>
```

**PATH для `uv`.** Если `uv` ставился пользовательским инсталлятором, он лежит в
`~/.local/bin`, и каждая SSH-команда должна его видеть, иначе `uv run` не найдётся:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

**Долгие прогоны не должны умирать вместе с SSH.** Лестница идёт минуты; оборванная
сессия убьёт прогон и оставит `aborted` в `status.json`. Отвязывай процесс от
терминала:

```bash
setsid nohup <команда прогона> > ~/run.log 2>&1 < /dev/null &
tail -f ~/run.log
```

**Права на bind-тома.** Контейнеры ходят не под твоим uid (внутри обычный `app`,
около 1000/1001), а bind-каталоги принадлежат тебе. SQLite пишет journal/WAL рядом
с базой, поэтому каталоги данных и сценариев надо открыть — это тест-стенд, не
production:

```bash
chmod -R 777 infra/.data .data services/data-service/testdata/scenarios
```

Иначе `agent-db materialize`/`register` кладут файлы туда, куда контейнер не может
записать, и тенант поднимается нездоровым.

**DSN тенантов — контейнерные пути.** CLI `agent-db register` подставляет хостовый
путь, которого внутри контейнера нет. Рабочая форма: путь, каким его видит контейнер,
и отдельная копия `data.db` на каждого тенанта в каталоге `infra/.data/app/`.

**Стаб-LLM и контейнеры — через docker-шлюз.** Стаб (`agent-db-stress stub`) живёт
на хосте, api-service — в контейнере, и `localhost` изнутри контейнера это не хост.
Адрес хоста в docker-сети (обычно `172.28.0.1`), стаб слушает `0.0.0.0`. Проверяй
доступность именно изнутри контейнера; в образе может не быть ни `wget`, ни `curl`.

**Дашборды — через туннель.** Grafana и Prometheus обычно привязаны к `127.0.0.1`
стенда и снаружи не видны. Туннель со своей машины:

```bash
ssh -N -L 3000:127.0.0.1:3000 -L 9090:127.0.0.1:9090 <user>@<host>
# дальше http://localhost:3000 (Grafana) и http://localhost:9090 (Prometheus)
```

**Мелочи, которые царапаются.**

- `ulimit -n` на свежей машине по умолчанию 1024; при пуле в сотни воркеров поднимай:
  `bash -c "ulimit -n 65535; <команда прогона>"`.
- `--tolerance` принимает только значения строго между 0 и 1; `1.0` отклоняется.
- `pkill -f "docker compose"` убивает и твою собственную оболочку (паттерн совпадает
  с её cmdline) — гаси контейнеры точечно, по именам.
- Лимитер и бюджет держи согласованными: дефолтный `mcp_rate_limit_rps` даст ложный
  потолок §5 (см. «Известные границы»), поэтому поднимай `MCP_TEST_RATE_LIMIT_*`
  и передавай его же в `--budget`.

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
в отличие от `USE_SCRIPTED_LLM`.) Имя тула в шаге — логическое (`db_map`): стаб сам разрешает его в advertised-имя, поэтому один скрипт ведёт и separate, и composite scope. Под composite (агент на нескольких тенантах, тулзы `stress-1__db_map`) шаг может нести `tenant`, чтобы целить конкретного тенанта — иначе имя с несколькими совпадениями отказывает как `ambiguous`, а не воронит нагрузку в один тенант молча.

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
| `raw/<rps>rps-run<k>-attempt<n>.jsonl` | **канонический источник перцентилей** (по записи на ход, CO-компенсация); имя — билет стадии: повторные прогоны knee и попытки расширения пула не смешиваются в одном файле |
| `raw/<rps>rps-run<k>-attempt<n>.calls.jsonl` | подекомпозиция по вызовам (без собственных плановых смещений) |
| `generator/<stage>.json` | CPU и лаг генератора: валидность стадии. Обе величины в двух видах — `cpu_measured_s`/`tick_lag_p99_ms` по измеренному окну (по ним судится валидность) и `cpu_s`/`stage_lag_p99_ms` по всей стадии (улика: разрыв = генератору понадобился прогрев) |
| `server/<service>.json` | срезы `/metrics` по времени: `slices[]` с `series` либо `gap` (никогда оба), `scrapes`/`gap_count`/`gap_reasons`, `url` и `authenticated` — по ним счётчики сервера сверяются с записями харнесса. Пропущенный скрейп — gap, не ноль |
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
- **Срезы `/metrics` собираются (с 2026-09-29), но в колонки отчёта ещё не заведены.**
  `server/<service>.json` пишется, и по нему счётчики сервера сходятся с уликами харнесса;
  при этом колонки `SUT CPU` / `mcp lock p95` по-прежнему печатаются как `n/a` с причиной —
  они считаются из cgroup-срезов и из api-service, то есть `host/` и L2+. Перцентили по
  гистограммам сервера на L1 сравнивать нельзя: бакеты начинаются с 1 ms и почти всё ложится
  в первый. Backlog-джойн для `platform_overhead` на L2/L3 — следующий шаг.
- **Скрейп сам инкрементит счётчики запросов data-service.** `/metrics` проходит через тот же
  `StructuredLoggingMiddleware`, поэтому кросс-проверка обязана вычитать `scrapes − 1` из
  `data_requests_total`. У `mcp_tool_calls_total` шлюза этого эффекта нет — он считает вызовы,
  а не HTTP-запросы.
- **`--budget` обязателен, если лимитер на стенде поднят.** Префлайт не читает env чужого
  процесса: без `--budget mcp_rate_limit_rps=...` он берёт дефолт (10 rps) и печатает
  `caps_the_ladder: true` с `source: default` — то есть манифест опишет не тот лимитер,
  который реально работал. Проверено на прогоне 2026-09-29: шлюз поднят на 5000 rps,
  префлайт до передачи `--budget` сообщал про 10.
- **На нативном стенде бинари надо объявить флагом `--binary`.** Сами они в манифест не
  попадают, и без них `publication_blockers()` держит «no runtime artefact is recorded»:
  ни digest'ов образов, ни sha256 бинарей, то есть числа ни к какой сборке не привязаны.
  Форма — `--binary LABEL=PATH[:SOURCE_ROOT]`, флаг повторяемый:

  ```
  --binary mcp-gateway=/tmp/hstand/mcp-gateway \
  --binary data-service=/tmp/hstand/data-service:services/data-service/cmd/server
  ```

  `SOURCE_ROOT` по умолчанию — `services/<label>`; манифест хеширует бинарь и сравнивает
  его mtime с самым свежим `.go` в этом дереве (`_test.go` не считается). Бинарь старше
  своих исходников попадает в блокеры как `stale`, а путь, по которому файла нет, — это
  отказ `collect_code`, а не «unchecked»: провенанс пишется пустым с причиной, и прогон
  остаётся непубликуемым. До 2026-09-29 флага не было вовсе, и этот блокер на нативном
  стенде снять было нечем.

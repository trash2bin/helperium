# Widget demo readiness: живой прогон витрины autoparts на HEAD `f094429` (2026-09-01)

> **Тип документа:** архивный evidence-snapshot (разовая проверка состояния). Обновлениям не подлежит; выводы действительны только для указанного HEAD и описанного локального runtime-состояния.
> **Verification marker:** HEAD `f094429` (`f094429695f37e5e24fe8ec3955813905aeeef2a`), рабочее дерево содержит незакоммиченные правки ревью-цикла от 2026-08-31 (см. `doc/archive/demo-readiness-followup-2026-08-31-head-f094429.md`). Все правки этой сессии — только в git-игнорируемых env-файлах и runtime-состоянии data-service; tracked-код не менялся.
> **Метод:** read-only диагностика + конфигурационные правки env/Agent Store. Контейнеры `autoparts-*` не останавливались и не изменялись; E2E/Docker-ресурсы не создавались.

## 1. Контекст

Вопрос владельца: что такое `HELPERIUM_WIDGET_ENABLED` в `demo/autoparts-store`, не является ли это хардкодом привязки к одному тенанту, и как это устроено при подготовке публичной демки. В ходе ответа обнаружены реальные дефекты конфигурации, которые были исправлены, и выполнен живой прогон чата end-to-end.

## 2. Механика флага (зафиксировано по коду)

- `catalog/settings.py:118`: `HELPERIUM_WIDGET_ENABLED = env_bool(..., False)` — fail-safe default; это **выключатель embed-виджета**, а не привязка к тенанту.
- `catalog/templates/catalog/base.html:34`: единственный потребитель — условный `<script src="{{ assistant_api_base }}/embed/embed.js" data-agent="{{ assistant_agent }}" ...>`.
- Привязка к тенанту: `HELPERIUM_AGENT` → атрибут `data-agent` → виджет шлёт `POST {apiBase}/api/chat/{agent}` (`embed/src/sse.ts:227`) → api-service резолвит **named agent из Agent Store** (`routes/chat.py:430`, `tenant_authority.named_agent_scope`) → `tenant_ids` берутся из persisted записи агента, браузерный scope невозможен (подтверждает контракт из аудита 2026-08-28).
- Документированный дрейф дефолтов (compose `:-true` / example `false`) — намеренный (`demo/autoparts-store/README.md:181-186`), в этой сессии не менялся.
- **Хардкода нет**: второй тенант добавляется регистрацией второго агента (`POST /api/agents`, `tenant_ids`) и вторым embed-сниппетом с другим `data-agent`; флаг не трогается.

## 3. Найденные и исправленные дефекты

| # | Дефект | Корень | Фикс |
|---|---|---|---|
| D1 | Виджет рендерился, но каждое сообщение = `404 Agent 'autoparts' not found` | `.env`/`.env.public` storefront'а: `HELPERIUM_AGENT=autoparts`, а в Agent Store существует `autoparts-assistant` | Имя в обоих env-файлах → `autoparts-assistant`; в `.env.public.example` добавлен комментарий о необходимости точного совпадения с Agent Store |
| D2 | LLM получала **0 инструментов** (`tools_sent=False tool_count=0`), gateway `/mcp` возвращал 500 для тенанта `autoparts`, circuit breaker открывался | Тенант `autoparts` не был зарегистрирован в нативном data-service (bootstrap регистрирует его только в Docker-профиле storefront'а) | Регистрация по протоколу `helperium_readonly_bootstrap.py`: `POST /admin/tenants` (postgres DSN read-only роли, `read_only: true`, пустые entities/endpoints) + `POST /admin/config/rewrite` → 7 entities / 43 endpoints; конфиг persisted в `.data/tenants/autoparts.json`, пережил рестарт стека |
| D3 | `limit_reached` на сложных вопросах при валидном tool-calling | `AGENT_MAX_TURN_TOKENS=12000` не вмещал trace с результатами тулов; 8 итераций / 10 тулов мало для многошагового поиска | `.env`: `AGENT_MAX_TURN_TOKENS=32000`, `AGENT_MAX_ITERATIONS=14`, `AGENT_MAX_TOOL_CALLS=18`; рестарт `dev.sh` |

## 4. Живой прогон после фиксов (модель `minimax-m3:cloud`, tenant `autoparts`)

| Вопрос | Результат |
|---|---|
| «Какие бренды есть в каталоге?» | ✅ `db_search` + `db_describe` → 20 брендов |
| «Сколько товаров дешевле 3000 рублей?» | ✅ один вызов `filter_catalog_product {price__lt: 3000}` → **259** (из `total`) |
| «Покажи товары категории Тормозная система дороже 5000» | ⚠️ валидная цепочка вызовов, но модель не находит кратчайший путь и упирается в лимит вызовов |
| «Какой самый дорогой товар?» | ⚠️ не решается никакой доступной моделью — см. F1 |

Матрица провайдеров (без изменения кода, только конфиг агента):

| Модель | Вердикт |
|---|---|
| `openai/deepseek-v4-flash` (polza, была primary) | ❌ зовёт `filter_*` только с `limit` → обучающая ошибка тулзы → terminal (по контракту loop'а) |
| `minimax-m3:cloud` (ollama) | ✅ лучший доступный: корректный multi-tool calling |
| `nvidia_nim/nemotron-3.5-lightning` | ❌ 401 — ключ в ProviderStore невалиден |
| `openai/gpt-4o-mini` (api.openai.com) | ❌ 401 — в `.env` `OPENAI_API_KEY` содержит polza-ключ (`pza_...`), а не OpenAI |
| `openai/gpt-4o-mini` через polza | ⚠️ валиден, но на rank/max-вопросах тоже бродит перебором |

**Фолбэк провайдеров доказан живьём:** в логах `nvidia_nim failed 401 → trying next candidate → polza` в пределах одного turn'а; успешно ответивший провайдер сохраняется до конца turn'а (соответствует `services/api-service/README.md`).

## 5. Открытые находки (требуют отдельного решения)

- **F1 (P2, нужна санкция владельца — публичная tool surface):** `sort_by` и `format=full` реализованы и работают в HTTP-слое data-service (`/catalog_product/filter?price__gt=0&sort_by=-price&format=full` → `id:96, price:7331`), но намеренно исключены из MCP-схемы тулз (`services/data-service/internal/search/filter.go:186`: *"sort_by, format still work in ParseRequest but are not in schema"*), а compact-превью не содержит значений полей → моделям нечем ранжировать/max-поискать и нечего читать без `db_get` per-row. Следствие: нерешаемые вопросы «самый дорогой/дешёвый», лавина `db_get`, выжигание `AGENT_MAX_TOOL_CALLS`. Потенциальный фикс — добавить `sort_by`/`format` параметрами в schema filter-тулз (или включить значения полей в compact-превью) + регресс-тесты + Docker E2E.
- **F2 (P3):** `nvidia-nim-nemotron-35-lightning` в ProviderStore включён с невалидным ключом — на каждом фолбэке тратится попытка и в логах 401.
- **F3 (P3):** расхождение `OPENAI_API_KEY` (polza-ключ) с провайдером `openai` (`api.openai.com`) — прямой OpenAI-путь недееспособен, пока ключ не заменён или провайдер не направлен на polza.

## 6. Итоговое состояние на конец сессии

- Все 6 сервисов `dev.sh status` healthy; чат widget → api-service → mcp-gateway → data-service → PostgreSQL работает end-to-end.
- Конфиг агента `autoparts-assistant`: `llm_config = minimax-m3:cloud (ollama)`, `provider_priority = [polza, ollama]`.
- Demo-store env (`HELPERIUM_AGENT=autoparts-assistant`) синхронизирован с Agent Store; для публичного деплоя остаётся поменять `HELPERIUM_API_BASE` на реальный origin (или оставить пустым — тогда `window.location.origin`).
- Затронутые файлы: `demo/autoparts-store/.env`, `demo/autoparts-store/.env.public`, `demo/autoparts-store/.env.public.example`, `.env` (корень, лимиты), `.data/tenants/autoparts.json` (runtime-артефакт регистрации). Первый файл — единственный из списка, попадающий в git.

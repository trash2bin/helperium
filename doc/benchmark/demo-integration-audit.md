# Аудит: интеграция demo-web + embed widget — фиксы и корни

**Дата:** 2026-08-07
**Контекст:** сессия «изучи и почини»: demo-web (:8080), виджет (:8081 /embed), data-service (:8084), админка (:8085). Найдено и починено 6 проблем, 2 остались фронтовыми.
**Статус:** ✅ бэкенд-фиксы применены и проверены; фронтовые баги зафиксированы (app.js вкладки, admin-dashboard Alpine)

## TL;DR

Демо-стек ожил после цепочки фиксов: SQLite `disk I/O error` (удалённые файлы БД), прокси demo-web терял query-params, виджет 404 из-за отсутствия seed-агента, заглушка LLM вместо реальных ответов. Виджет теперь отвечает реальной моделью через MCP-тулы (проверено в браузере: `db_map → db_describe → db_search → db_get` → таблица товаров).

## Что сломалось и почему

| # | Симптом | Корень | Фикс |
|---|---|---|---|
| 1 | data-service: `disk I/O error (1802)` на всех запросах | Файлы БД (`shop/data.db`, `auto-shop/data.db`) удалены на хосте, пока сервис держал их открытыми (все fd показывали `(deleted)`; WAL/shm — остатки) | `docker restart` data-service → БД жива (integrity_check ok, 3 категории / 4 товара) |
| 2 | `/api/data/categories?pattern=...` через demo-web → 400 `'pattern' is required` | `_proxy_to_data_service()` строил `url = DATA_SERVICE_URL + data_path` **без query string** (в отличие от `_proxy_to_api`, где `params=dict(request.query_params)` есть) | `demo/web/server.py`: добавил `params=dict(request.query_params)` в `http_client.get` |
| 3 | Виджет `POST /api/chat/default` → 404 | Агента `default` нет в `agents.sqlite` (сторе пуст на свежем окружении) | Seed в lifespan: `services/api-service/src/api_service/server/app.py` — если агента `default` нет, создаётся (`tenant_ids=["default"]`, widget_config) |
| 4 | Чат отвечает «модель завершила работу без ответа» | `USE_SCRIPTED_LLM=1` + пустой `SCRIPTED_LLM_PATH` → ScriptedLLMProvider с пустыми раундами | Переключение на реальный провайдер: `USE_SCRIPTED_LLM=0`, `OPENAI_API_KEY/BASE/MODEL` в compose (Polza, deepseek-v4-flash) |
| 5 | `/admin/llm-providers` → 500 `PermissionError: '.data'` | `DEFAULT_PROVIDERS_PATH = Path(".data/providers.json")` — cwd `/app` не writable для пользователя `app` (uid 1000), каталог root-owned | `provider_store.py`: env override `PROVIDER_STORE_PATH`; compose: `PROVIDER_STORE_PATH=/data/app/providers.json` (volume app_data) |
| 6 | `OPENAI_MODEL` пустой при `source .env` | Значение `deepseek/deepseek-v4-flash@provider=DeepSeek&allow_fallbacks=false` содержит `&` — bash при source интерпретирует `&` как фоновую команду → переменная обрезается | `.env`: значение закавычено |

## Как проверялось

- `GET /api/data/categories?pattern=Книги` через demo-web → `{"total":1,"preview":[{"id":3,"name":"Книги"}]}` (кириллица raw и url-encoded).
- `POST /api/chat` (X-Tenant-ID: default, browser UA) → SSE: `tool_call(db_map)` → `tool_result` (схема) → `token`/`final` «В базе данных **3 категории**…».
- Виджет в браузере (:8080): вопрос «какие товары есть в наличии?» → цепочка `db_map → db_describe → db_search → db_get` → таблица 4 товаров с ценами/остатками.
- Seed: удалил агента `default`, рестарт api → лог `Seeded default agent`, агент пересоздался.

## Известные ограничения / что осталось

- **Frontend (не тронуто, ждёт владельца фронта):**
  1. `demo/web/static/app.js`: вкладки «Loading entities…» навсегда — `tabOps = ["list","find","custom_query"]` не включает `"strategy"`. Современный манифест: `op:"strategy", strategy:"grep"` на пути `/categories` (без `/grep` суффикса). `loadData()` шлёт `/api/data/{tab}` без `pattern` — grep требует непустой `pattern` (иначе 400).
  2. `admin-dashboard`: 26× `ReferenceError: isDefaultRuleDisabled is not defined` — секция config на `x-show="page === 'config'"`, Alpine рендерит содержимое сразу, а методы копируются только после `tokenSet` в `init()`. Фикс: `x-if` вместо `x-show` или раннее копирование методов.
- Заголовок «API: Ollama unavailable» в demo-web — копится из `OLLAMA_URL`, хотя реальный провайдер теперь Polza. Косметика.
- `.env` gitignored — правки (кавычки OPENAI_MODEL) не попадают в git; при свежем клоне нужны вручную.
- `ENABLE_THINK=false` в compose (из .env) — контейнер раньше имел `true`; явно переопределён в compose дефолт.

## Полезные команды

```bash
# пересоздать api с env из .env (docker-compose не читает корневой .env сам)
cd infra && set -a && source ../.env && set +a && docker-compose up -d --force-recreate api

# проверить кириллический pattern через web
curl -s -H "X-Tenant-ID: default" --get --data-urlencode "pattern=Книги" http://127.0.0.1:8080/api/data/categories

# провайдеры
curl -s -H "Authorization: Bearer secret" http://127.0.0.1:8081/admin/llm-providers
```

## Файлы изменены

- `demo/web/server.py` — проксирование query params в data-service
- `services/api-service/src/api_service/server/app.py` — seed агента `default` в lifespan
- `services/api-service/src/api_service/provider_store.py` — env override `PROVIDER_STORE_PATH`
- `infra/docker-compose.yml` — `USE_SCRIPTED_LLM=0`, `OLLAMA_URL` (host.docker.internal), `OPENAI_*`, `PROVIDER_STORE_PATH`, `ENABLE_THINK=false`
- `.env` (gitignored) — кавычки вокруг `OPENAI_MODEL`

**Last verified:** 2026-08-07 (HEAD 07f7515)

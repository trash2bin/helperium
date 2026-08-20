# Runbook — onboard a new client in hours

Internal cheat sheet. Not for the client — for you. Updated: July 2026.

---

## Prerequisites from client

- [ ] PostgreSQL access (host, port, user, password, database)
- [ ] Domain (for HTTPS in prod mode)
- [ ] LLM API key (OpenAI / Anthropic / Mistral) or local Ollama
- [ ] Documents for RAG (PDF, DOCX, TXT)
- [ ] Where to embed the widget (page URL, inside `<body>`)

---

## Server + Docker

```bash
ssh root@client-server
apt install docker.io docker-compose-v2
git clone https://github.com/trash2bin/helperium
cd helperium

mkdir -p .data/{app,rag,hf_cache,uploads,pg}
cp .env.example .env
```

---

## Minimal config

```bash
DB_DRIVER=postgres
DATABASE_URL=postgres://user:pass@host:5432/dbname?sslmode=require

# LLM — pick one:
OLLAMA_URL=http://host.docker.internal:11434
OLLAMA_MODEL=qwen2.5:0.5b
# or
MISTRAL_API_KEY=sk-...
MISTRAL_MODEL=mistral/mistral-small
# or OPENAI_API_KEY / ANTHROPIC_API_KEY

DEFAULT_TENANT_ID=client-name
DEMO_TENANTS=client-name

# Only for prod:
DOMAIN=chat.client.com
# Generate one strong secret, keep it out of Git, and set the same value in both.
MCP_REQUIRE_AUTH=true
MCP_API_KEY=<generated-strong-secret>
MCP_CLIENT_API_KEY=<same-generated-strong-secret>
# MCP stays internal behind web/Caddy. Leave empty unless a browser-facing MCP
# ingress is intentionally added; then list only its exact HTTPS origin.
MCP_ALLOWED_ORIGINS=

# Optional: read-only access to admin dashboard
# VIEWER_TOKEN=viewer-token
```

The remaining variables have safe defaults for the demo. **Do not leave MCP auth disabled in a public deployment**: `MCP_REQUIRE_AUTH=true` plus matching non-empty credentials are mandatory.

---

## Start + health check

```bash
./infra/scripts/compose.sh up -d                  # dev
./infra/scripts/compose.sh --profile prod up -d   # prod + Caddy HTTPS

# Wait 120s — RAG downloads embedding model on first start
./infra/scripts/compose.sh logs rag --tail 20
./infra/scripts/compose.sh ps

curl http://localhost:8084/health    # → {"status":"ok"}
curl http://localhost:8082/health    # → {"status":"ok"}
curl http://localhost:8081/health    # → {"status":"ok"}
```

---

## Monitoring stack

```bash
./infra/scripts/compose.sh --profile monitoring up -d
# Grafana: http://localhost:3000 (admin / admin) — 18-panel dashboard
# Prometheus: http://localhost:9090
```

Each service exposes `/metrics` by default.

---

## Tenant + data

```bash
uv run agent-db register client-name autoparts  # реальная команда: register <tenant> <scenario>

# Introspect client DB schema
curl http://localhost:8084/admin/introspect?tenant=client-name

# Import RAG documents via admin dashboard (:8085) or CLI:
uv run agent-rag-ingest import /path/to/doc.pdf -d client-name
```

---

## Configure agent

Admin dashboard: `http://localhost:8085`

**Auth:** логин с `ADMIN_TOKEN` (полный доступ) или `VIEWER_TOKEN` (только чтение).

1. **Tenants** — check client-name exists
2. **Config** — verify LLM provider
3. **Tools** — verify MCP tools from manifest
4. **Agents** — create agent, set system prompt
5. **RAG** — upload documents, test search
6. **Anti-Abuse** — tune RPS, burst and `max_user_turns_per_session`
7. **Anti-Abuse presets** — Normal/Cautious/Lockdown являются active controls и синхронно применяются в `api-service`; проверяй acknowledged apply/rollback contract.

---

## Embed widget

```html
<script src="https://chat.client.com/embed/embed.js"
        data-agent="assistant"
        data-title="Assistant"
        data-accent="#0f766e"
        data-position="right"
        data-api-base="https://chat.client.com">
</script>
```

Insert into `<body>` on the client's page. Shadow DOM — no CSS conflicts.

---

## Verification

```bash
uv run agent-db e2e          # full E2E: materialize → register → web proxy + SSE chat
uv run agent-db test          # tenant isolation tests

# Public chat via web (http://localhost:8080) — check streaming and tool calling.
# Direct chat is fixed to DEFAULT_TENANT_ID/default; named agents own their
# persisted tenant scope and are the only public multi-tenant surface.

# Secure MCP transport + multi-tenant isolation contract.
MCP_API_KEY="$MCP_API_KEY" MCP_ALLOWED_ORIGINS="$MCP_ALLOWED_ORIGINS" \
  uv run pytest services/agent-db/tests/e2e/test_mcp_streamable_http.py -v

# Check logs for errors
./infra/scripts/compose.sh logs --tail 100 2>&1 | grep -i error
```

---

## Troubleshooting

```bash
./infra/scripts/compose.sh logs api --tail 50
./infra/scripts/compose.sh logs rag --tail 50
./infra/scripts/compose.sh restart api

# Reset RAG index:
./infra/scripts/compose.sh stop rag
rm -rf .data/rag/chroma_db
./infra/scripts/compose.sh up -d rag

# Delete and re-create tenant:
uv run agent-db drop autoparts  # реальная команда: drop <scenario>
# then repeat from section "Tenant + data"
```

---

## Production (HTTPS)

```bash
./infra/scripts/compose.sh --profile prod up -d
# Caddy auto-provisions Let's Encrypt certs, proxies :443 → web:8080, redirects :80 → :443
```

---

## Quick reference

```
1. git clone + mkdir -p .data/{app,rag,hf_cache,uploads,pg} + cp .env.example .env
2. Edit .env: DATABASE_URL, LLM key, DEFAULT_TENANT_ID, DOMAIN
3. docker compose up -d
4. docker compose --profile monitoring up -d   (Grafana :3000)
5. uv run agent-db register client-name autoparts  # реальная команда: register <tenant> <scenario>
6. Admin dashboard: upload RAG, create agent, check tools
7. Widget: <script src="/embed/embed.js" data-agent="assistant">
8. uv run agent-db e2e
```

---

## Backups

| Data | Responsible | Notes |
|------|-------------|-------|
| Client's DB | **Client** | pg_dump / PITR at their hosting provider |
| Tenant configs | Platform | ~44KB, `infra/scripts/backup.sh` |
| LLM keys | Platform | Store separately from server (vault / sealed secrets) |
| ChromaDB / RAG index | Platform | Re-indexable from source docs |
| Session / Backlog | Platform | Ephemeral, not critical |

```bash
bash infra/scripts/backup.sh  # → backups/<date>/tenants/ + .env
```

---

# Runbook — второй деплой за часы

Внутренняя шпаргалка. Не для клиента — для себя. Актуально: июль 2026.

---

## Что нужно от клиента

- [ ] Доступ к PostgreSQL (хост, порт, юзер, пароль, база)
- [ ] Домен (для HTTPS в prod)
- [ ] API-ключ к LLM (OpenAI / Anthropic / Mistral) или локальный Ollama
- [ ] Документы для RAG (PDF, DOCX, TXT)
- [ ] Куда встроить виджет (URL страницы, внутри `<body>`)

---

## Сервер + Docker

```bash
ssh root@client-server
apt install docker.io docker-compose-v2
git clone https://github.com/trash2bin/helperium
cd helperium

mkdir -p .data/{app,rag,hf_cache,uploads,pg}
cp .env.example .env
```

---

## Минимальный конфиг

```bash
DB_DRIVER=postgres
DATABASE_URL=postgres://user:pass@host:5432/dbname?sslmode=require

# LLM — один из:
OLLAMA_URL=http://host.docker.internal:11434
OLLAMA_MODEL=qwen2.5:0.5b
# или
MISTRAL_API_KEY=sk-...
MISTRAL_MODEL=mistral/mistral-small
# или OPENAI_API_KEY / ANTHROPIC_API_KEY

DEFAULT_TENANT_ID=client-name
DEMO_TENANTS=client-name

# Только для prod:
DOMAIN=chat.client.com
# Сгенерируй один сильный секрет, не клади его в Git, оба значения совпадают.
MCP_REQUIRE_AUTH=true
MCP_API_KEY=<сгенерированный-сильный-secret>
MCP_CLIENT_API_KEY=<тот-же-secret>
# MCP остаётся internal. Оставь пустым, пока browser-facing MCP ingress не нужен;
# тогда укажи только точный HTTPS Origin.
MCP_ALLOWED_ORIGINS=
```

Остальные переменные имеют безопасные demo-дефолты. **В public deployment нельзя оставлять MCP auth выключенным**: обязательны `MCP_REQUIRE_AUTH=true` и совпадающие непустые credentials.

---

## Старт + проверка здоровья

```bash
./infra/scripts/compose.sh up -d                  # dev
./infra/scripts/compose.sh --profile prod up -d   # prod + Caddy HTTPS

# Ждём 120s — RAG качает embedding-модель при первом старте
./infra/scripts/compose.sh logs rag --tail 20
./infra/scripts/compose.sh ps

curl http://localhost:8084/health    # → {"status":"ok"}
curl http://localhost:8082/health    # → {"status":"ok"}
curl http://localhost:8081/health    # → {"status":"ok"}
```

---

## Мониторинг

```bash
./infra/scripts/compose.sh --profile monitoring up -d
# Grafana: http://localhost:3000 (admin / admin) — 18 панелей
# Prometheus: http://localhost:9090
```

Каждый сервис отдаёт `/metrics` по умолчанию.

---

## Тенант + данные

```bash
uv run agent-db register client-name autoparts  # реальная команда: register <tenant> <scenario>

# Проинтроспектировать схему БД клиента
curl http://localhost:8084/admin/introspect?tenant=client-name

# Импорт RAG-документов через админку (:8085) или CLI:
uv run agent-rag-ingest import /path/to/doc.pdf -d client-name
```

---

## Настройка агента

Админка: `http://localhost:8085`

1. **Tenants** — проверить, что client-name создан
2. **Config** — проверить LLM провайдер
3. **Tools** — проверить MCP-тулы из манифеста
4. **Agents** — создать агента, system prompt
5. **RAG** — загрузить документы, проверить поиск
6. **Anti-Abuse** — RPS, burst, user-turn quota
7. **Anti-Abuse presets** — Normal/Cautious/Lockdown являются active controls и синхронно применяются в `api-service`; проверяй acknowledged apply/rollback contract.

---

## Виджет

```html
<script src="https://chat.client.com/embed/embed.js"
        data-agent="assistant"
        data-title="Помощник"
        data-accent="#0f766e"
        data-position="right"
        data-api-base="https://chat.client.com">
</script>
```

Вставить в `<body>` на сайте клиента. Shadow DOM — CSS сайта не ломается.

---

## Проверка

```bash
uv run agent-db e2e          # полный E2E: materialize → register → web proxy + SSE chat
uv run agent-db test          # тесты изоляции тенантов

# Чат через web (http://localhost:8080) — стриминг, tool calling

# Логи без ошибок
./infra/scripts/compose.sh logs --tail 100 2>&1 | grep -i error
```

---

## Если что-то пошло не так

```bash
./infra/scripts/compose.sh logs api --tail 50
./infra/scripts/compose.sh logs rag --tail 50
./infra/scripts/compose.sh restart api

# Сбросить RAG-индекс:
./infra/scripts/compose.sh stop rag
rm -rf .data/rag/chroma_db
./infra/scripts/compose.sh up -d rag

# Удалить и пересоздать тенанта:
uv run agent-db drop autoparts  # реальная команда: drop <scenario>
# затем повторно с раздела "Тенант + данные"
```

---

## Production (HTTPS)

```bash
./infra/scripts/compose.sh --profile prod up -d
# Caddy сам получает Let's Encrypt, проксирует :443 → web:8080, редиректит :80 → :443
```

---

## Краткая памятка

```
1. git clone + mkdir -p .data/{app,rag,hf_cache,uploads,pg} + cp .env.example .env
2. Правим .env: DATABASE_URL, LLM ключ, DEFAULT_TENANT_ID, DOMAIN
3. docker compose up -d
4. docker compose --profile monitoring up -d   (Grafana :3000)
5. uv run agent-db register client-name autoparts  # реальная команда: register <tenant> <scenario>
6. Админка: загрузить RAG, создать агента, утвердить тулы
7. Виджет: <script src="/embed/embed.js" data-agent="assistant">
8. uv run agent-db e2e
```

---

## Бэкапы

| Данные | Ответственный | Заметки |
|--------|---------------|---------|
| БД клиента | **Клиент** | pg_dump / PITR у хостинг-провайдера |
| Конфиги тенантов | Платформа | ~44KB, `infra/scripts/backup.sh` |
| LLM ключи | Платформа | Хранить отдельно от сервера (vault / sealed secrets) |
| ChromaDB / RAG индекс | Платформа | Переиндексируется из исходных доков |
| Сессии / Backlog | Платформа | Эфемерные, не критичны |

```bash
bash infra/scripts/backup.sh  # → backups/<date>/tenants/ + .env
```

## Public auto-parts demo

This is a standalone public storefront, not a replacement for the core Helperium deployment. Its production files live in `demo/autoparts-store`; the synthetic catalogue generator is intentionally out of scope for the deployment workflow.

### Release checklist

- [ ] A DNS `A`/`AAAA` record for `DEMO_DOMAIN` points to the server.
- [ ] Ports `80` and `443` are open to the internet and unused by another ingress.
- [ ] The root Helperium `prod` Caddy is not bound to the same ports on this host.
- [ ] `DJANGO_SECRET_KEY` and `STORE_DB_PASSWORD` are unique, high-entropy values.
- [ ] `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS` use the real HTTPS domain.
- [ ] Leave `DEMO_ORDER_SUBMISSIONS=false` until there is an explicit, consented order-processing workflow.

### Deploy

```bash
cd helperium/demo/autoparts-store
cp .env.public.example .env.public
# Generate secrets locally; never commit .env.public.
openssl rand -base64 48   # DJANGO_SECRET_KEY
openssl rand -base64 36   # STORE_DB_PASSWORD
$EDITOR .env.public
docker compose --env-file .env.public -f docker-compose.public.yml up -d --build
docker compose --env-file .env.public -f docker-compose.public.yml ps
curl -fsS https://$DEMO_DOMAIN/healthz/
```

The database is attached only to `storefront_internal`; its Compose definition deliberately has no `ports:` section. Caddy is the only public service. It obtains and renews TLS certificates and proxies the storefront to Gunicorn.

### Helperium assistant (opt-in)

Deploy the storefront with `HELPERIUM_WIDGET_ENABLED=false`. Only after the public tenant, an agent and allowlisted embed origin are configured in the core stack should you set `HELPERIUM_WIDGET_ENABLED=true`, `HELPERIUM_API_BASE=https://<helperium-domain>` and `HELPERIUM_AGENT=<agent-id>`, then recreate `storefront`.

```bash
docker compose --env-file .env.public -f docker-compose.public.yml up -d --force-recreate storefront
```

### Smoke check and rollback

```bash
# Expected: 200 plus Caddy/Django security headers.
curl -fsSI https://$DEMO_DOMAIN/healthz/
docker compose --env-file .env.public -f docker-compose.public.yml logs --tail=100 storefront storefront-caddy

# Stop public ingress without deleting synthetic catalogue data.
docker compose --env-file .env.public -f docker-compose.public.yml stop storefront-caddy storefront
```

For a browser pass, verify the homepage, a catalogue category, product card, cart POST action, checkout disclosure, responsive layout and — only when enabled — the assistant opening and one successful streamed response.

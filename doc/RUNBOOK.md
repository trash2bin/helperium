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
```

The other ~170 vars have safe defaults. Only change per client.

---

## Start + health check

```bash
docker compose up -d                              # dev
docker compose --profile prod up -d               # prod + Caddy HTTPS

# Wait 120s — RAG downloads embedding model on first start
docker compose logs rag --tail 20
docker compose ps

curl http://localhost:8084/health    # → {"status":"ok"}
curl http://localhost:8082/health    # → {"status":"ok"}
curl http://localhost:8081/health    # → {"status":"ok"}
```

---

## Monitoring stack

```bash
docker compose --profile monitoring up -d
# Grafana: http://localhost:3000 (admin / admin) — 12-panel dashboard
# Prometheus: http://localhost:9090
```

Each service exposes `/metrics` by default.

---

## Tenant + data

```bash
uv run agent-db tenant register client-name

# Introspect client DB schema
curl http://localhost:8084/admin/introspect?tenant=client-name

# Import RAG documents via admin dashboard (:8085) or CLI:
uv run agent-rag-ingest import /path/to/doc.pdf -d client-name
```

---

## Configure agent

Admin dashboard: `http://localhost:8085`

1. **Tenants** — check client-name exists
2. **Config** — verify LLM provider
3. **Tools** — approve write-tools (disabled by default)
4. **Agents** — create agent, set system prompt
5. **RAG** — upload documents, test search
6. **Anti-Abuse** — tune RPS, burst, session budget
7. **Emergency Presets** — Normal → Cautious → Lockdown

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
uv run agent-db e2e-data      # tenant isolation
uv run agent-db e2e-mcp       # MCP tool isolation
uv run agent-db e2e-full      # all three levels

# Chat via web (http://localhost:8080) — check streaming, tool calling

# Write-tools disabled by default
curl http://localhost:8084/admin/tools/pending

# Check logs for errors
docker compose logs --tail 100 2>&1 | grep -i error
```

---

## Troubleshooting

```bash
docker compose logs api --tail 50
docker compose logs rag --tail 50
docker compose restart api

# Reset RAG index:
docker compose stop rag
rm -rf .data/rag/chroma_db
docker compose up -d rag

# Delete and re-create tenant:
uv run agent-db tenant delete client-name
# then repeat from section "Tenant + data"
```

---

## Production (HTTPS)

```bash
docker compose --profile prod up -d
# Caddy auto-provisions Let's Encrypt certs, proxies :443 → web:8080, redirects :80 → :443
```

---

## Quick reference

```
1. git clone + mkdir -p .data/{app,rag,hf_cache,uploads,pg} + cp .env.example .env
2. Edit .env: DATABASE_URL, LLM key, DEFAULT_TENANT_ID, DOMAIN
3. docker compose up -d
4. docker compose --profile monitoring up -d   (Grafana :3000)
5. uv run agent-db tenant register client-name
6. Admin dashboard: upload RAG, create agent, approve tools
7. Widget: <script src="/embed/embed.js" data-agent="assistant">
8. uv run agent-db e2e-full
```

---

## Backups

| Data | Responsible | Notes |
|------|-------------|-------|
| Client's DB | **Client** | pg_dump / PITR at their hosting provider |
| Tenant configs | Platform | ~44KB, `scripts/backup.sh` |
| LLM keys | Platform | Store separately from server (vault / sealed secrets) |
| ChromaDB / RAG index | Platform | Re-indexable from source docs |
| Session / Backlog | Platform | Ephemeral, not critical |

```bash
bash scripts/backup.sh  # → backups/<date>/tenants/ + .env
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
```

Остальные ~170 переменных с безопасными дефолтами. Править только под клиента.

---

## Старт + проверка здоровья

```bash
docker compose up -d                              # dev
docker compose --profile prod up -d               # prod + Caddy HTTPS

# Ждём 120s — RAG качает embedding-модель при первом старте
docker compose logs rag --tail 20
docker compose ps

curl http://localhost:8084/health    # → {"status":"ok"}
curl http://localhost:8082/health    # → {"status":"ok"}
curl http://localhost:8081/health    # → {"status":"ok"}
```

---

## Мониторинг

```bash
docker compose --profile monitoring up -d
# Grafana: http://localhost:3000 (admin / admin) — 12 панелей
# Prometheus: http://localhost:9090
```

Каждый сервис отдаёт `/metrics` по умолчанию.

---

## Тенант + данные

```bash
uv run agent-db tenant register client-name

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
3. **Tools** — утвердить write-тулы (по умолчанию выключены)
4. **Agents** — создать агента, system prompt
5. **RAG** — загрузить документы, проверить поиск
6. **Anti-Abuse** — RPS, burst, session budget
7. **Emergency Presets** — Normal → Cautious → Lockdown

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
uv run agent-db e2e-data      # изоляция тенантов
uv run agent-db e2e-mcp       # изоляция MCP-тулов
uv run agent-db e2e-full      # все три уровня

# Чат через web (http://localhost:8080) — стриминг, tool calling

# Write-тулы выключены по умолчанию
curl http://localhost:8084/admin/tools/pending

# Логи без ошибок
docker compose logs --tail 100 2>&1 | grep -i error
```

---

## Если что-то пошло не так

```bash
docker compose logs api --tail 50
docker compose logs rag --tail 50
docker compose restart api

# Сбросить RAG-индекс:
docker compose stop rag
rm -rf .data/rag/chroma_db
docker compose up -d rag

# Удалить и пересоздать тенанта:
uv run agent-db tenant delete client-name
# затем повторно с раздела "Тенант + данные"
```

---

## Production (HTTPS)

```bash
docker compose --profile prod up -d
# Caddy сам получает Let's Encrypt, проксирует :443 → web:8080, редиректит :80 → :443
```

---

## Краткая памятка

```
1. git clone + mkdir -p .data/{app,rag,hf_cache,uploads,pg} + cp .env.example .env
2. Правим .env: DATABASE_URL, LLM ключ, DEFAULT_TENANT_ID, DOMAIN
3. docker compose up -d
4. docker compose --profile monitoring up -d   (Grafana :3000)
5. uv run agent-db tenant register client-name
6. Админка: загрузить RAG, создать агента, утвердить тулы
7. Виджет: <script src="/embed/embed.js" data-agent="assistant">
8. uv run agent-db e2e-full
```

---

## Бэкапы

| Данные | Ответственный | Заметки |
|--------|---------------|---------|
| БД клиента | **Клиент** | pg_dump / PITR у хостинг-провайдера |
| Конфиги тенантов | Платформа | ~44KB, `scripts/backup.sh` |
| LLM ключи | Платформа | Хранить отдельно от сервера (vault / sealed secrets) |
| ChromaDB / RAG индекс | Платформа | Переиндексируется из исходных доков |
| Сессии / Backlog | Платформа | Эфемерные, не критичны |

```bash
bash scripts/backup.sh  # → backups/<date>/tenants/ + .env
```

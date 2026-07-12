# ============================================================
# API server: LiteLLM agent, session history, backlog
# Core deps + litellm + SDK — no torch, no chromadb
# ============================================================
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY helperium-sdk helperium-sdk/
COPY api-service/pyproject.toml api-service/
RUN uv sync --frozen --no-install-project --package api-service --no-dev
COPY . .
RUN uv sync --frozen --package api-service --no-dev

FROM python:3.12-slim

RUN groupadd --gid 1000 app && \
    useradd --uid 1000 --gid app --shell /bin/bash --create-home app

RUN apt-get update && \
    apt-get install --no-install-recommends -y curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/api-service /app/api-service
COPY --from=builder --chown=app:app /app/demo /app/demo
COPY --from=builder --chown=app:app /app/helperium-sdk /app/helperium-sdk


ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/app \
    DEMO_API_HOST=0.0.0.0 \
    DEMO_API_PORT=8081 \
    MCP_SERVICE_URL=http://mcp:8083/mcp

USER app
WORKDIR /app

EXPOSE 8081

CMD ["python", "-m", "api_service.server"]
